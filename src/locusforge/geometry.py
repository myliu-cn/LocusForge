from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .models import OrientationKeyframe, Recipe, Trajectory
from .motion import sample_program, shortest_degrees, smootherstep

Array = NDArray[np.float64]


def intrinsics(width: int, height: int, horizontal_fov_deg: float) -> Array:
    focal = width / (2 * math.tan(math.radians(horizontal_fov_deg) / 2))
    return np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]], dtype=float)


def camera_to_world(position: Array, forward: Array) -> Array:
    length = np.linalg.norm(forward)
    if length < 1e-8:
        raise ValueError("Camera position and look target coincide")
    forward = forward / length
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    if np.linalg.norm(right) < 1e-5:
        raise ValueError("A camera direction parallel to world-up has ambiguous roll")
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    matrix = np.eye(4)
    matrix[:3, :3] = np.column_stack((right, down, forward))
    matrix[:3, 3] = position
    return matrix


def rotation_error_deg(a: Array, b: Array) -> float:
    relative = np.linalg.solve(a[:3, :3], b[:3, :3])
    cosine = (np.trace(relative) - 1) / 2
    sine = (
        np.linalg.norm(
            [
                relative[2, 1] - relative[1, 2],
                relative[0, 2] - relative[2, 0],
                relative[1, 0] - relative[0, 1],
            ]
        )
        / 2
    )
    return float(np.degrees(np.arctan2(sine, np.clip(cosine, -1, 1))))


def _dense_path(path: Trajectory, count: int = 8193) -> Array:
    parameter = np.linspace(0, 1, count)
    angle = 2 * np.pi * parameter
    if path.kind == "waypoints":
        points = np.asarray(path.waypoints, dtype=float)
        distances = points - np.roll(points, 1, axis=0) if path.closed else np.diff(points, axis=0)
        if np.min(np.linalg.norm(distances, axis=1)) < 0.01:
            raise ValueError("Adjacent spline control points must be at least 1 cm apart")
        scaled = parameter * (len(points) if path.closed else len(points) - 1)
        segment = np.floor(scaled).astype(int)
        if path.closed:
            segment %= len(points)
            t = (scaled % 1)[:, None]
            a, b, c, d = (points[(segment + offset) % len(points)] for offset in (-1, 0, 1, 2))
        else:
            segment = np.minimum(segment, len(points) - 2)
            t = (scaled - segment)[:, None]
            a, b, c, d = (
                points[np.clip(segment + offset, 0, len(points) - 1)] for offset in (-1, 0, 1, 2)
            )
        result = 0.5 * (
            2 * b
            + (-a + c) * t
            + (2 * a - 5 * b + 4 * c - d) * t**2
            + (-a + 3 * b - 3 * c + d) * t**3
        )
    else:
        if path.kind == "orbit":
            x = path.radius * np.cos(angle)
            y = path.radius * path.aspect * np.sin(angle)
        else:
            x = path.radius * np.sin(angle)
            y = path.radius * path.aspect * np.sin(2 * angle) / 2
        phase = math.radians(path.phase_deg)
        result = np.column_stack(
            (
                x * math.cos(phase) - y * math.sin(phase) + path.center[0],
                x * math.sin(phase) + y * math.cos(phase) + path.center[1],
                np.full(count, path.height),
            )
        )
    if path.closed:
        result[-1] = result[0]
    return result


def orientation_knots(path: Trajectory) -> list[OrientationKeyframe]:
    knots = list(path.orientation_keyframes)
    if not knots or knots[0].at != 0:
        knots.insert(0, OrientationKeyframe(at=0))
    if knots[-1].at != 1:
        anchor = knots[0] if path.closed else knots[-1]
        knots.append(OrientationKeyframe(at=1, yaw_deg=anchor.yaw_deg, pitch_deg=anchor.pitch_deg))
    return knots


def orientation_offsets(path: Trajectory, progress: Array) -> tuple[Array, Array]:
    knots = orientation_knots(path)
    at = np.array([key.at for key in knots])
    yaw = [knots[0].yaw_deg]
    for a, b in zip(knots[:-1], knots[1:], strict=True):
        yaw.append(yaw[-1] + shortest_degrees(a.yaw_deg, b.yaw_deg))
    yaw_values = np.array(yaw)
    pitch = np.array([key.pitch_deg for key in knots])
    segment = np.clip(np.searchsorted(at, progress, side="right") - 1, 0, len(knots) - 2)
    phase = smootherstep((progress - at[segment]) / (at[segment + 1] - at[segment]))
    return (
        yaw_values[segment] + phase * (yaw_values[segment + 1] - yaw_values[segment]),
        pitch[segment] + phase * (pitch[segment + 1] - pitch[segment]),
    )


def motion_path(path: Trajectory, progress: Array) -> tuple[Array, Array, list[dict[str, Any]]]:
    if path.kind == "program":
        return sample_program(path, progress)
    dense = _dense_path(path)
    arc = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))))
    if arc[-1] < 0.1:
        raise ValueError("Trajectory is too short")
    distances = progress * float(arc[-1]) * path.laps
    periodic = np.remainder(distances, arc[-1]) if path.closed else distances
    positions = np.column_stack([np.interp(periodic, arc, dense[:, axis]) for axis in range(3)])
    if path.closed:
        positions[-1] = positions[0]
    epsilon = min(0.01, arc[-1] / 10000)
    before, after = periodic - epsilon, periodic + epsilon
    if path.closed:
        before, after = np.remainder(before, arc[-1]), np.remainder(after, arc[-1])
    else:
        before, after = np.maximum(before, 0), np.minimum(after, arc[-1])
    forwards = np.column_stack(
        [
            np.interp(after, arc, dense[:, axis]) - np.interp(before, arc, dense[:, axis])
            for axis in range(3)
        ]
    )
    return positions, forwards, []


def plan(recipe: Recipe) -> dict[str, Any]:
    capture, path, quality = recipe.capture, recipe.trajectory, recipe.quality
    progress = np.linspace(0, 1, capture.frame_count)
    positions, forwards, schedule = motion_path(path, progress)
    if path.heading == "target":
        forwards = np.asarray(path.target) - positions
    lengths = np.linalg.norm(forwards, axis=1)
    if np.any(lengths < 1e-9):
        raise ValueError(
            "A path has an undefined tangent or coincident camera target; edit its control points"
        )
    horizontal = np.linalg.norm(forwards[:, :2], axis=1)
    base_yaw = np.degrees(np.arctan2(-forwards[:, 1], forwards[:, 0]))
    base_pitch = np.degrees(np.arctan2(forwards[:, 2], horizontal))
    if path.heading == "forward":
        last_yaw = path.start_yaw_deg
        for index in range(len(forwards)):
            if horizontal[index] < 1e-8:
                base_yaw[index], base_pitch[index] = last_yaw, 0.0
            else:
                last_yaw = base_yaw[index]
    yaw_offset, pitch_offset = orientation_offsets(path, progress)
    yaw, pitch = base_yaw + yaw_offset, base_pitch + pitch_offset
    if np.max(np.abs(pitch)) > 85 + 1e-7:
        raise ValueError(
            "Combined camera pitch must remain within +/-85 degrees; reduce the waypoint tilt to avoid a vertical singularity"
        )
    yr, pr = np.radians(yaw), np.radians(pitch)
    forwards = np.column_stack((np.cos(pr) * np.cos(yr), -np.cos(pr) * np.sin(yr), np.sin(pr)))
    poses = np.array([camera_to_world(p, f) for p, f in zip(positions, forwards, strict=True)])
    closure_position = float(np.linalg.norm(positions[-1] - positions[0]))
    closure_rotation = rotation_error_deg(poses[0], poses[-1])
    if path.closed and closure_position < 1e-7 and closure_rotation < 1e-6:
        poses[-1] = poses[0]
    velocities = np.diff(positions, axis=0) * capture.fps
    speed = np.linalg.norm(velocities, axis=1)
    angular = np.array(
        [rotation_error_deg(a, b) * capture.fps for a, b in zip(poses[:-1], poses[1:], strict=True)]
    )
    acceleration = np.linalg.norm(np.diff(velocities, axis=0) * capture.fps, axis=1)
    excursion = float(np.max(np.linalg.norm(positions - positions[0], axis=1)))
    stats = {
        "frame_count": capture.frame_count,
        "video_duration_seconds": capture.duration_seconds,
        "last_sample_seconds": (capture.frame_count - 1) / capture.fps,
        "path_length_m": float(np.sum(np.linalg.norm(np.diff(positions, axis=0), axis=1))),
        "max_speed_mps": float(np.max(speed)),
        "max_angular_speed_dps": float(np.max(angular)),
        "max_acceleration_mps2": float(np.max(acceleration)) if acceleration.size else 0.0,
        "max_abs_pitch_deg": float(np.max(np.abs(pitch))),
        "max_roll_deg": 0.0,
        "orientation_keyframe_count": len(path.orientation_keyframes),
        "excursion_m": excursion,
        "endpoint_position_error_m": float(np.linalg.norm(positions[-1] - positions[0])),
        "endpoint_rotation_error_deg": rotation_error_deg(poses[0], poses[-1]),
        "raw_rgb_depth_bytes": capture.frame_count * capture.width * capture.height * 7,
        "pixel_samples": capture.frame_count * capture.width * capture.height * capture.samples,
    }
    checks = [
        (
            "speed",
            stats["max_speed_mps"] <= quality.max_speed_mps,
            stats["max_speed_mps"],
            quality.max_speed_mps,
        ),
        (
            "angular_speed",
            stats["max_angular_speed_dps"] <= quality.max_angular_speed_dps,
            stats["max_angular_speed_dps"],
            quality.max_angular_speed_dps,
        ),
        (
            "acceleration",
            stats["max_acceleration_mps2"] <= quality.max_acceleration_mps2,
            stats["max_acceleration_mps2"],
            quality.max_acceleration_mps2,
        ),
        ("excursion", excursion >= quality.min_excursion_m, excursion, quality.min_excursion_m),
    ]
    if path.closed:
        checks.extend(
            [
                ("path_position_closure", closure_position < 1e-6, closure_position, 1e-6),
                ("path_orientation_closure", closure_rotation < 0.001, closure_rotation, 0.001),
            ]
        )
    knots = orientation_knots(path)
    smallest_interval = min(b.at - a.at for a, b in zip(knots[:-1], knots[1:], strict=True))
    if path.orientation_keyframes:
        checks.append(
            (
                "orientation_sampling",
                smallest_interval * (capture.frame_count - 1) >= 2,
                smallest_interval * (capture.frame_count - 1),
                2,
            )
        )
    if schedule:
        minimum_motion_samples = (
            min(piece["end_at"] - piece["start_at"] for piece in schedule)
            * (capture.frame_count - 1)
            / path.laps
        )
        checks.append(("motion_sampling", minimum_motion_samples >= 2, minimum_motion_samples, 2))
    return {
        "intrinsics": intrinsics(capture.width, capture.height, capture.fov_deg).tolist(),
        "frames": [
            {
                "index": i,
                "timestamp_seconds": i / capture.fps,
                "progress": float(progress[i]),
                "orientation": {
                    "base_yaw_deg": float(base_yaw[i]),
                    "base_pitch_deg": float(base_pitch[i]),
                    "yaw_offset_deg": float(yaw_offset[i]),
                    "pitch_offset_deg": float(pitch_offset[i]),
                    "yaw_deg": float(yaw[i]),
                    "pitch_deg": float(pitch[i]),
                    "roll_deg": 0.0,
                },
                "c2w": pose.tolist(),
                "w2c": np.linalg.inv(pose).tolist(),
            }
            for i, pose in enumerate(poses)
        ],
        "stats": stats,
        "checks": [
            {"name": name, "passed": passed, "value": value, "threshold": limit}
            for name, passed, value, limit in checks
        ],
        "valid": all(check[1] for check in checks),
        "geometry_checked": False,
        "motion_schedule": schedule,
        "orientation_knots": [key.model_dump() for key in knots],
        "planner_version": "2.0",
        "interpolation": "quintic shortest-yaw/pitch waypoint offsets; horizon-locked zero roll; motion program uses eased primitives and explicit stationary corner turns",
        "warnings": [
            "Orientation progress spans the entire sampled sequence, not each lap.",
            "Pure vertical movement retains the previous horizontal bearing; pitch remains independently editable.",
        ]
        + (
            [
                "Open path: no automatic closing segment or camera teleport; final loop certification still applies."
            ]
            if not path.closed
            else []
        )
        + (
            [
                "Motion program corners include explicit stationary heading transitions; time weights include these transitions."
            ]
            if schedule
            else []
        ),
    }


def project(points_world: Array, w2c: Array, k: Array) -> tuple[Array, Array]:
    camera = points_world @ w2c[:3, :3].T + w2c[:3, 3]
    homogeneous = camera @ k.T
    depth = camera[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        uv = homogeneous[:, :2] / depth[:, None]
    return uv, depth


def visible_overlap(
    source_depth: NDArray[np.floating[Any]],
    target_depth: NDArray[np.floating[Any]],
    source_c2w: Array,
    target_w2c: Array,
    k: Array,
    stride: int = 8,
) -> dict[str, float | int]:
    height, width = source_depth.shape
    yy, xx = np.mgrid[stride // 2 : height : stride, stride // 2 : width : stride]
    z = source_depth[yy, xx].astype(float).ravel()
    valid = np.isfinite(z) & (z > 0)
    if not np.any(valid):
        return {"overlap": 0.0, "tested": 0, "visible": 0}
    pixels = np.column_stack((xx.ravel() + 0.5, yy.ravel() + 0.5, np.ones(z.size)))
    camera = (pixels[valid] @ np.linalg.inv(k).T) * z[valid, None]
    world = camera @ source_c2w[:3, :3].T + source_c2w[:3, 3]
    uv, projected_z = project(world, target_w2c, k)
    inside = (projected_z > 0) & np.isfinite(uv).all(axis=1)
    inside &= (uv[:, 0] >= 0) & (uv[:, 0] < width) & (uv[:, 1] >= 0) & (uv[:, 1] < height)
    selected = uv[inside]
    # Pixel coordinates are edge-based: the center of array[y, x] is (x + .5, y + .5).
    x = np.floor(selected[:, 0]).astype(int)
    y = np.floor(selected[:, 1]).astype(int)
    observed = target_depth[y, x]
    expected = projected_z[inside]
    tolerance = np.maximum(0.03, 0.01 * expected)
    visible = np.isfinite(observed) & (observed > 0) & (np.abs(observed - expected) <= tolerance)
    return {
        "overlap": float(visible.sum() / valid.sum()),
        "tested": int(valid.sum()),
        "visible": int(visible.sum()),
    }
