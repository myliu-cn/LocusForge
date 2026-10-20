from __future__ import annotations

import hashlib
import itertools
from collections import defaultdict
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .geometry import Array, rotation_error_deg, visible_overlap
from .io import atomic_json, read_json, write_jsonl
from .models import Recipe


def candidate_pairs(poses: Array, recipe: Recipe, budget: int = 256) -> list[tuple[int, int]]:
    tolerance = recipe.quality.max_position_error_m
    minimum_frames = int(np.ceil(recipe.quality.min_gap_seconds * recipe.capture.fps))
    positions = poses[:, :3, 3]
    cells: dict[tuple[int, ...], list[int]] = defaultdict(list)
    pairs: set[tuple[int, int]] = {(0, len(poses) - 1)}
    offsets = list(itertools.product((-1, 0, 1), repeat=3))
    for j, position in enumerate(positions):
        key = tuple(np.floor(position / tolerance).astype(int).tolist())
        for offset in offsets:
            neighbors = cells.get(tuple(a + b for a, b in zip(key, offset, strict=True)), [])
            for i in neighbors:
                if j - i < minimum_frames:
                    continue
                if (
                    np.linalg.norm(positions[i] - position) <= tolerance
                    and rotation_error_deg(poses[i], poses[j])
                    <= recipe.quality.max_rotation_error_deg
                ):
                    pairs.add((i, j))
        bucket = cells[key]
        if len(bucket) < 12:
            bucket.append(j)
        else:
            # Keep early anchors, plus recent revisits, without unbounded stationary buckets.
            bucket[6:] = bucket[7:] + [j]
        if len(pairs) > budget * 4:
            pairs = set(
                sorted(pairs, key=lambda pair: pair[1] - pair[0], reverse=True)[: budget * 2]
            )
    return sorted(pairs, key=lambda pair: (-(pair[1] - pair[0]), pair[0], pair[1]))[:budget]


def certify_loops(
    dataset: Path,
    cameras: dict[str, Any],
    recipe: Recipe,
    cancelled: Callable[[], None],
) -> dict[str, Any]:
    poses = np.array([frame["c2w"] for frame in cameras["frames"]])
    inverses = np.array([frame["w2c"] for frame in cameras["frames"]])
    positions = poses[:, :3, 3]
    k = np.array(cameras["intrinsics"])
    stride = max(1, min(recipe.capture.width, recipe.capture.height) // 48)

    @lru_cache(maxsize=8)
    def depth(index: int) -> Any:
        return np.load(dataset / "depth" / f"{index:06d}.npy", allow_pickle=False, mmap_mode="r")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for i, j in candidate_pairs(poses, recipe):
        cancelled()
        gap = (j - i) / recipe.capture.fps
        position_error = float(np.linalg.norm(positions[i] - positions[j]))
        rotation_error = rotation_error_deg(poses[i], poses[j])
        excursions = np.linalg.norm(positions[i : j + 1] - positions[i], axis=1)
        departure = i + int(np.argmax(excursions))
        excursion = float(np.max(excursions))
        forward = visible_overlap(depth(i), depth(j), poses[i], inverses[j], k, stride)
        backward = visible_overlap(depth(j), depth(i), poses[j], inverses[i], k, stride)
        away = visible_overlap(depth(i), depth(departure), poses[i], inverses[departure], k, stride)
        overlap = min(float(forward["overlap"]), float(backward["overlap"]))
        reasons = []
        if gap < recipe.quality.min_gap_seconds:
            reasons.append("gap_too_short")
        if position_error > recipe.quality.max_position_error_m:
            reasons.append("position_error")
        if rotation_error > recipe.quality.max_rotation_error_deg:
            reasons.append("rotation_error")
        if excursion < recipe.quality.min_excursion_m:
            reasons.append("insufficient_excursion")
        if overlap < recipe.quality.min_overlap:
            reasons.append("insufficient_visible_overlap")
        if away["overlap"] > recipe.quality.max_departure_overlap:
            reasons.append("anchor_never_sufficiently_left_view")
        pair: dict[str, Any] = {
            "source_frame": i,
            "target_frame": j,
            "gap_seconds": gap,
            "kind": "exact" if position_error < 1e-5 and rotation_error < 0.001 else "near",
            "position_error_m": position_error,
            "rotation_error_deg": rotation_error,
            "overlap_forward": forward["overlap"],
            "overlap_backward": backward["overlap"],
            "overlap_min": overlap,
            "samples_forward": forward["tested"],
            "samples_backward": backward["tested"],
            "excursion_m": excursion,
            "departure_frame": departure,
            "departure_overlap": away["overlap"],
            "certified": not reasons,
            "reasons": reasons,
        }
        if reasons:
            rejected.append(pair)
        else:
            accepted.append(pair)
        if len(accepted) >= 64:
            break
    histogram = {"0-10s": 0, "10-30s": 0, "30-60s": 0, "60s+": 0}
    for pair in accepted:
        pair_gap = pair["gap_seconds"]
        bucket = (
            "0-10s"
            if pair_gap < 10
            else "10-30s"
            if pair_gap < 30
            else "30-60s"
            if pair_gap < 60
            else "60s+"
        )
        histogram[bucket] += 1
    return {
        "schema_version": "1.0",
        "method": "bidirectional depth-reprojected visible surface support",
        "sampling_stride_pixels": stride,
        "occlusion_tolerance": "max(0.03 m, 0.01 * projected Z)",
        "candidate_budget": 256,
        "accepted_pair_budget": 64,
        "exhaustive": False,
        "pairs": accepted,
        "rejected_candidates": rejected[:128],
        "evaluated_candidates": len(accepted) + len(rejected),
        "gap_histogram": histogram,
    }


def export_actions(cameras: dict[str, Any], destination: Path) -> None:
    values = []
    frames = cameras["frames"]
    for current, following in zip(frames[:-1], frames[1:], strict=True):
        delta = np.array(current["w2c"]) @ np.array(following["c2w"])
        dt = following["timestamp_seconds"] - current["timestamp_seconds"]
        values.append(
            {
                "from_frame": current["index"],
                "to_frame": following["index"],
                "dt_seconds": dt,
                "T_camera_from_next_camera": delta.tolist(),
                "translation_in_from_camera_m": delta[:3, 3].tolist(),
                "velocity_in_from_camera_mps": (delta[:3, 3] / dt).tolist(),
                "rotation_next_to_from": delta[:3, :3].tolist(),
                "rotation_magnitude_deg": rotation_error_deg(np.eye(4), delta),
            }
        )
    write_jsonl(destination, values)


def validate_dataset(
    root: Path,
    recipe: Recipe,
    cancelled: Callable[[], None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = root / "dataset"
    cameras = read_json(dataset / "cameras.json")
    renderer = read_json(root / "render_result.json")
    preflight = read_json(root / "preflight.json")
    count = recipe.capture.frame_count
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, measured: Any, required: Any) -> None:
        checks.append(
            {"name": name, "passed": bool(passed), "measured": measured, "required": required}
        )

    rgb_files = sorted((dataset / "rgb").glob("*.png"))
    depth_files = sorted((dataset / "depth").glob("*.npy"))
    check(
        "frame_count",
        len(cameras["frames"]) == len(rgb_files) == len(depth_files) == count,
        [len(cameras["frames"]), len(rgb_files), len(depth_files)],
        count,
    )
    if not checks[-1]["passed"]:
        raise ValueError("Incomplete frame set: validation cannot certify a partial render")
    minimum_depth = 1.0
    maximum_dark, maximum_bright = 0.0, 0.0
    hashes: set[str] = set()
    camera_error = 0.0
    roll_error = 0.0
    time_error = 0.0
    for index, frame in enumerate(cameras["frames"]):
        cancelled()
        expected = f"{index:06d}"
        if (
            frame["index"] != index
            or frame["rgb"] != f"rgb/{expected}.png"
            or frame["depth"] != f"depth/{expected}.npy"
        ):
            raise ValueError("Frames must be contiguous, zero-indexed, and explicitly aligned")
        rgb_path, depth_path = dataset / frame["rgb"], dataset / frame["depth"]
        with Image.open(rgb_path) as image:
            if image.size != (recipe.capture.width, recipe.capture.height) or image.mode != "RGB":
                raise ValueError(f"RGB shape or color mode mismatch at frame {index}")
            pixels = np.asarray(image)
            hashes.add(hashlib.sha256(pixels.tobytes()).hexdigest())
            luminance = pixels @ np.array([0.2126, 0.7152, 0.0722])
            maximum_dark = max(maximum_dark, float(np.mean(luminance <= 3)))
            maximum_bright = max(maximum_bright, float(np.mean(luminance >= 252)))
        depth = np.load(depth_path, mmap_mode="r", allow_pickle=False)
        if (
            depth.shape != (recipe.capture.height, recipe.capture.width)
            or depth.dtype != np.float32
        ):
            raise ValueError(f"Depth must be HxW float32 at frame {index}")
        if not np.isfinite(depth).all() or np.any(depth < 0):
            raise ValueError(f"Non-finite or negative depth at frame {index}")
        minimum_depth = min(minimum_depth, float(np.mean(depth > 0)))
        c2w, w2c = np.array(frame["c2w"]), np.array(frame["w2c"])
        if (
            c2w.shape != (4, 4)
            or w2c.shape != (4, 4)
            or not np.isfinite(c2w).all()
            or not np.isfinite(w2c).all()
        ):
            raise ValueError(f"Malformed camera matrices at frame {index}")
        camera_error = max(
            camera_error,
            float(np.max(np.abs(c2w @ w2c - np.eye(4)))),
            float(np.max(np.abs(c2w[:3, :3].T @ c2w[:3, :3] - np.eye(3)))),
            abs(float(np.linalg.det(c2w[:3, :3])) - 1),
            float(np.max(np.abs(c2w[3] - [0, 0, 0, 1]))),
        )
        roll_error = max(roll_error, abs(float(np.degrees(np.arctan2(c2w[2, 0], -c2w[2, 1])))))
        time_error = max(time_error, abs(frame["timestamp_seconds"] - index / recipe.capture.fps))
    loops = certify_loops(dataset, cameras, recipe, cancelled)
    check(
        "swept_camera_clearance",
        preflight["passed"],
        preflight["minimum_clearance_lower_bound_m"],
        recipe.quality.camera_radius_m,
    )
    check("camera_matrix_consistency", camera_error < 1e-5, camera_error, "< 1e-5")
    check("zero_camera_roll", roll_error < 0.001, roll_error, "< 0.001 degrees")
    check("sample_clock", time_error < 1e-9, time_error, "< 1e-9 seconds")
    check(
        "position_pass_projection",
        renderer["max_position_pass_reprojection_error_px"] < 0.02,
        renderer["max_position_pass_reprojection_error_px"],
        "< 0.02 pixels",
    )
    check(
        "valid_depth",
        minimum_depth >= recipe.quality.min_valid_depth,
        minimum_depth,
        recipe.quality.min_valid_depth,
    )
    check(
        "exposure_not_empty",
        max(maximum_dark, maximum_bright) < 0.98,
        {"max_black_fraction": maximum_dark, "max_white_fraction": maximum_bright},
        "< 0.98",
    )
    check("non_frozen_video", len(hashes) > 1, len(hashes), "> 1 distinct rendered image")
    check(
        "certified_revisits",
        len(loops["pairs"]) >= recipe.quality.min_pairs,
        len(loops["pairs"]),
        recipe.quality.min_pairs,
    )
    export_actions(cameras, dataset / "actions.jsonl")
    atomic_json(dataset / "loops.json", loops)
    passed = all(item["passed"] for item in checks)
    report = {
        "schema_version": "1.0",
        "passed": passed,
        "training_eligible": passed and recipe.capture.purpose == "dataset",
        "purpose": recipe.capture.purpose,
        "checks": checks,
        "certified_pairs": len(loops["pairs"]),
        "gap_histogram": loops["gap_histogram"],
        "minimum_valid_depth": minimum_depth,
        "unique_rgb_frames": len(hashes),
        "renderer": renderer,
        "geometry": preflight,
        "warnings": [
            "Geometric validity is not a photorealism or downstream training-performance guarantee.",
            "Overlap is sampled, occlusion-aware support, not exact dense correspondence.",
            "Meters are exact in the authored simulation; real-world asset scale is an operator attestation.",
        ]
        + (
            ["Preview capture: explicitly not training-eligible."]
            if recipe.capture.purpose == "preview"
            else []
        ),
    }
    return report, loops
