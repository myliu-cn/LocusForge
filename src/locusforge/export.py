from __future__ import annotations

import hashlib
import json
import math
import os
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import imageio_ffmpeg

from . import __version__
from .io import atomic_json, read_json, safe_child, sha256_file, utc_now
from .models import Recipe


def encoder_command(recipe: Recipe, dataset: Path, threads: int) -> list[str]:
    capture = recipe.capture
    return [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-framerate",
        str(capture.fps),
        "-start_number",
        "0",
        "-i",
        str(dataset / "rgb" / "%06d.png"),
        "-frames:v",
        str(capture.frame_count),
        "-an",
        "-c:v",
        "libx264",
        "-threads",
        str(min(threads, 4)),
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(capture.fps),
        "-fps_mode",
        "cfr",
        "-video_track_timescale",
        str(capture.fps * 1000),
        "-movflags",
        "+faststart",
        str(dataset / "video.mp4"),
    ]


def verify_video(path: Path, recipe: Recipe, cancelled: Callable[[], None]) -> dict[str, Any]:
    reader = imageio_ffmpeg.read_frames(
        str(path), pix_fmt="rgb24", input_params=["-threads", "2"], output_params=["-vsync", "0"]
    )
    try:
        metadata = next(reader)
        if not isinstance(metadata, dict):
            raise ValueError("Decoder did not return video metadata")
        count = 0
        expected_size = recipe.capture.width * recipe.capture.height * 3
        for frame in reader:
            cancelled()
            if len(frame) != expected_size:
                raise ValueError("Decoded frame size does not match the camera calibration")
            count += 1
        if count != recipe.capture.frame_count:
            raise ValueError(
                f"Encoded video has {count} frames; expected {recipe.capture.frame_count}"
            )
        if tuple(metadata["size"]) != (recipe.capture.width, recipe.capture.height):
            raise ValueError("Encoded resolution differs from the RGB-D contract")
        if not math.isclose(metadata["fps"], recipe.capture.fps, abs_tol=0.001):
            raise ValueError("Encoded FPS differs from the exposure clock")
        if not math.isclose(metadata["duration"], recipe.capture.duration_seconds, abs_tol=0.02):
            raise ValueError("Encoded duration differs from N/FPS")
        return {
            "decoded_frames": count,
            "fps": metadata["fps"],
            "size": list(metadata["size"]),
            "duration_seconds": metadata["duration"],
            "codec": metadata["codec"],
            "role": "lossy H.264 review proxy; lossless RGB PNGs are the canonical training pixels",
        }
    finally:
        reader.close()


def package_dataset(
    root: Path,
    job: dict[str, Any],
    report: dict[str, Any],
    cancelled: Callable[[], None],
) -> dict[str, Any]:
    dataset = root / "dataset"
    atomic_json(dataset / "quality.json", report)
    atomic_json(dataset / "recipe.json", job["recipe"])
    scene = job["scene"]
    atomic_json(dataset / "scene.json", scene)
    files = []
    for path in sorted(dataset.rglob("*")):
        cancelled()
        if path.is_file() and path.name != "manifest.json":
            files.append(
                {
                    "path": path.relative_to(dataset).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    manifest = {
        "schema_version": "1.0",
        "generator": "LocusForge",
        "generator_version": __version__,
        "job_id": job["id"],
        "created_at": utc_now(),
        "training_eligible": report["training_eligible"],
        "purpose": job["recipe"]["capture"]["purpose"],
        "scene_id": scene["id"],
        "source_group": scene["source_group"],
        "split": scene["split"],
        "split_policy": "SHA256(locusforge-split-v1:source_group) first 32 bits modulo 100; 80/10/10",
        "asset_sha256": scene["asset_sha256"],
        "asset_redistributed": False,
        "asset_license": scene["license"],
        "rights_attestation": {
            "source_url": scene["source_url"],
            "attribution": scene["attribution"],
            "commercial_use_confirmed": scene["commercial_use_confirmed"],
            "training_use_confirmed": scene["training_use_confirmed"],
            "note": "Operator attestation, not automated legal approval or a license for third-party rights.",
        },
        "clock": {
            "frame_count": job["recipe"]["capture"]["fps"]
            * job["recipe"]["capture"]["duration_seconds"],
            "fps": job["recipe"]["capture"]["fps"],
            "rule": "N samples at i/FPS; exact closing pose at (N-1)/FPS; video duration N/FPS",
        },
        "files": files,
    }
    atomic_json(dataset / "manifest.json", manifest)
    temporary, target = root / "dataset.zip.tmp", root / "dataset.zip"
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True, compresslevel=1) as archive:
            for path in sorted(dataset.rglob("*")):
                cancelled()
                if path.is_file():
                    compression = (
                        zipfile.ZIP_STORED
                        if path.suffix in {".mp4", ".png"}
                        else zipfile.ZIP_DEFLATED
                    )
                    archive.write(
                        path, path.relative_to(dataset).as_posix(), compress_type=compression
                    )
        cancelled()
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "manifest_sha256": sha256_file(dataset / "manifest.json"),
    }


def verify_export(path: Path) -> dict[str, Any]:
    if path.is_dir():
        manifest = read_json(path / "manifest.json")
        checked = len(manifest["files"])
        for item in manifest["files"]:
            target = safe_child(path, item["path"])
            if not target.is_file() or target.stat().st_size != item["bytes"]:
                raise ValueError(f"Missing or resized file: {item['path']}")
            if sha256_file(target) != item["sha256"]:
                raise ValueError(f"Checksum mismatch: {item['path']}")
    else:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError("Archive contains duplicate names")
            checked = len(manifest["files"])
            for item in manifest["files"]:
                if archive.getinfo(item["path"]).file_size != item["bytes"]:
                    raise ValueError(f"Resized archive member: {item['path']}")
                hasher = hashlib.sha256()
                with archive.open(item["path"]) as stream:
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                if digest != item["sha256"]:
                    raise ValueError(f"Checksum mismatch: {item['path']}")
    return {
        "verified": True,
        "files_checked": checked,
        "job_id": manifest["job_id"],
        "training_eligible": manifest["training_eligible"],
    }
