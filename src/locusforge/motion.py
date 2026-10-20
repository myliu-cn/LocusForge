from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .models import ArcSegment, HoldSegment, MoveSegment, Trajectory, TurnSegment

Array = NDArray[np.float64]


def smootherstep(value: Array) -> Array:
    return value**3 * (10 + value * (-15 + 6 * value))


def shortest_degrees(start: float, end: float) -> float:
    difference = (end - start + 180) % 360 - 180
    return 180.0 if difference == -180 and end - start > 0 else difference


def forward_vector(yaw: float) -> Array:
    angle = math.radians(yaw)
    return np.array([math.cos(angle), -math.sin(angle), 0.0])


@dataclass
class Piece:
    kind: str
    start: Array
    end: Array
    yaw_start: float
    yaw_delta: float
    weight: float
    authored_index: int | None
    radius: float = 0.0


def compile_program(path: Trajectory) -> list[Piece]:
    position = np.array(path.start, dtype=float)
    navigation_yaw = float(path.start_yaw_deg)
    view_yaw = navigation_yaw
    pieces: list[Piece] = []

    def align_view(desired: float) -> None:
        nonlocal view_yaw
        delta = shortest_degrees(view_yaw, desired)
        if abs(delta) > 1e-7:
            pieces.append(
                Piece(
                    "auto_turn",
                    position.copy(),
                    position.copy(),
                    view_yaw,
                    delta,
                    0.2 + abs(delta) / 180 * 0.8,
                    None,
                )
            )
            view_yaw += delta

    for index, segment in enumerate(path.segments):
        if isinstance(segment, MoveSegment):
            forward = forward_vector(navigation_yaw)
            right = np.cross(forward, [0, 0, 1])
            directions = {
                "forward": forward,
                "backward": -forward,
                "right": right,
                "left": -right,
                "up": np.array([0, 0, 1.0]),
                "down": np.array([0, 0, -1.0]),
            }
            direction = directions[segment.direction]
            if np.linalg.norm(direction[:2]) > 1e-8:
                align_view(math.degrees(math.atan2(-direction[1], direction[0])))
            destination = position + direction * segment.distance_m
            pieces.append(
                Piece(
                    "move", position.copy(), destination.copy(), view_yaw, 0, segment.weight, index
                )
            )
            position = destination
        elif isinstance(segment, ArcSegment):
            align_view(navigation_yaw)
            start_angle, end_angle = np.radians(
                [navigation_yaw, navigation_yaw + segment.angle_deg]
            )
            curvature = math.copysign(1 / segment.radius_m, segment.angle_deg)
            destination = position + np.array(
                [
                    (math.sin(end_angle) - math.sin(start_angle)) / curvature,
                    (math.cos(end_angle) - math.cos(start_angle)) / curvature,
                    0.0,
                ]
            )
            pieces.append(
                Piece(
                    "arc",
                    position.copy(),
                    destination.copy(),
                    view_yaw,
                    segment.angle_deg,
                    segment.weight,
                    index,
                    segment.radius_m,
                )
            )
            position = destination
            navigation_yaw += segment.angle_deg
            view_yaw += segment.angle_deg
        elif isinstance(segment, TurnSegment):
            pieces.append(
                Piece(
                    "turn",
                    position.copy(),
                    position.copy(),
                    view_yaw,
                    segment.yaw_deg,
                    segment.weight,
                    index,
                )
            )
            navigation_yaw += segment.yaw_deg
            view_yaw += segment.yaw_deg
        elif isinstance(segment, HoldSegment):
            pieces.append(
                Piece("hold", position.copy(), position.copy(), view_yaw, 0, segment.weight, index)
            )
    if path.closed and np.linalg.norm(position - path.start) < 1e-6:
        align_view(float(path.start_yaw_deg))
    return pieces


def sample_program(path: Trajectory, progress: Array) -> tuple[Array, Array, list[dict[str, Any]]]:
    pieces = compile_program(path)
    weights = np.array([piece.weight for piece in pieces])
    boundaries = np.concatenate(([0.0], np.cumsum(weights) / np.sum(weights)))
    local = np.remainder(progress * path.laps, 1) if path.closed else progress
    local[-1] = 1.0
    positions = np.empty((len(progress), 3), dtype=float)
    yaw = np.empty(len(progress), dtype=float)
    for index, piece in enumerate(pieces):
        selected = (local >= boundaries[index]) & (
            local <= boundaries[index + 1]
            if index == len(pieces) - 1
            else local < boundaries[index + 1]
        )
        t = (local[selected] - boundaries[index]) / (boundaries[index + 1] - boundaries[index])
        s = smootherstep(np.clip(t, 0, 1))
        yaw[selected] = piece.yaw_start + piece.yaw_delta * s
        if piece.kind == "arc":
            start_angle = math.radians(piece.yaw_start)
            angle = start_angle + np.radians(piece.yaw_delta * s)
            curvature = math.copysign(1 / piece.radius, piece.yaw_delta)
            positions[selected] = piece.start + np.column_stack(
                (
                    (np.sin(angle) - math.sin(start_angle)) / curvature,
                    (np.cos(angle) - math.cos(start_angle)) / curvature,
                    np.zeros(len(s)),
                )
            )
        else:
            positions[selected] = piece.start + s[:, None] * (piece.end - piece.start)
    yaw_radians = np.radians(yaw)
    directions = np.column_stack((np.cos(yaw_radians), -np.sin(yaw_radians), np.zeros(len(yaw))))
    schedule = [
        {
            "kind": piece.kind,
            "authored_index": piece.authored_index,
            "start_at": float(boundaries[index]),
            "end_at": float(boundaries[index + 1]),
            "automatic": piece.authored_index is None,
        }
        for index, piece in enumerate(pieces)
    ]
    return positions, directions, schedule
