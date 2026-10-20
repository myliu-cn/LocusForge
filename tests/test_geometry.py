import math

import numpy as np
import pytest
from pydantic import ValidationError

from locusforge.geometry import (
    camera_to_world,
    intrinsics,
    plan,
    project,
    rotation_error_deg,
    visible_overlap,
)
from locusforge.models import Capture, Recipe, Trajectory
from locusforge.quality import candidate_pairs


def test_clock_endpoint_intrinsics_and_actions_contract():
    recipe = Recipe()
    result = plan(recipe)
    assert result["valid"]
    assert len(result["frames"]) == 720
    assert result["frames"][-1]["timestamp_seconds"] == 719 / 24
    first, last = (np.array(result["frames"][index]["c2w"]) for index in (0, -1))
    np.testing.assert_array_equal(first, last)
    for frame in result["frames"][::25]:
        pose = np.array(frame["c2w"])
        np.testing.assert_allclose(pose @ np.array(frame["w2c"]), np.eye(4), atol=1e-12)
        assert np.linalg.det(pose[:3, :3]) == pytest.approx(1)
    assert result["intrinsics"][0][2] == 640
    assert result["intrinsics"][1][2] == 360
    assert result["stats"]["video_duration_seconds"] == 30


def test_float32_camera_does_not_invent_rotation_at_identical_pose():
    camera = camera_to_world(np.array([3.8, 3.8, 1.7]), np.array([-3.8, -3.8, -0.4]))
    camera = camera.astype(np.float32).astype(float)
    assert rotation_error_deg(camera, camera) < 1e-10
    rotation = np.eye(4)
    angle = math.radians(35)
    rotation[:3, :3] = [
        [math.cos(angle), -math.sin(angle), 0],
        [math.sin(angle), math.cos(angle), 0],
        [0, 0, 1],
    ]
    assert rotation_error_deg(camera, camera @ rotation) == pytest.approx(35, abs=1e-8)


def test_occlusion_rejects_pose_proximity_without_visible_support():
    k = intrinsics(64, 64, 60)
    pose = np.eye(4)
    far = np.full((64, 64), 5.0, dtype=np.float32)
    near = np.full((64, 64), 2.0, dtype=np.float32)
    assert visible_overlap(far, far, pose, pose, k)["overlap"] == 1
    assert visible_overlap(far, near, pose, pose, k)["overlap"] == 0
    invalid = np.zeros_like(far)
    assert visible_overlap(invalid, far, pose, pose, k)["tested"] == 0


def test_pixel_centers_and_forward_projection():
    k = intrinsics(64, 32, 60)
    center_pixel = np.array([[15.5, 10.5, 1]])
    point = center_pixel @ np.linalg.inv(k).T * 8
    uv, z = project(point, np.eye(4), k)
    np.testing.assert_allclose(uv, center_pixel[:, :2])
    np.testing.assert_allclose(z, 8)


def test_trajectory_gates_reject_fast_and_trivial_paths():
    fast = Recipe(capture=Capture(duration_seconds=2, fps=24), quality={"min_gap_seconds": 0.5})
    result = plan(fast)
    assert not result["valid"]
    assert not next(check for check in result["checks"] if check["name"] == "speed")["passed"]
    trivial = Recipe(trajectory=Trajectory(radius=0.5))
    assert not plan(trivial)["valid"]


def test_closed_waypoints_and_forward_heading():
    recipe = Recipe(
        trajectory=Trajectory(
            kind="waypoints",
            heading="forward",
            waypoints=[[-4, -4, 1.7], [4, -4, 1.7], [4, 4, 1.7], [-4, 4, 1.7]],
        )
    )
    result = plan(recipe)
    assert result["valid"]
    assert result["frames"][0]["c2w"] == result["frames"][-1]["c2w"]


def test_figure_eight_is_not_an_orbit():
    result = plan(Recipe(trajectory=Trajectory(kind="figure_eight", heading="forward")))
    positions = np.array([frame["c2w"] for frame in result["frames"]])[:, :3, 3]
    assert np.linalg.norm(positions[0] - positions[len(positions) // 2]) < 0.1
    assert result["stats"]["path_length_m"] > 20


def test_candidate_budget_and_mandatory_terminal_revisit():
    recipe = Recipe()
    poses = np.array([frame["c2w"] for frame in plan(recipe)["frames"]])
    pairs = candidate_pairs(poses, recipe, budget=8)
    assert (0, len(poses) - 1) in pairs
    assert len(pairs) <= 8
    assert all((j - i) / recipe.capture.fps >= recipe.quality.min_gap_seconds for i, j in pairs)


@pytest.mark.parametrize(
    "value",
    [
        {"capture": {"width": 321}},
        {"capture": {"seed": -1}},
        {"capture": {"device": "GRAPHICS"}},
        {"capture": {"engine": "BLENDER_EEVEE_NEXT", "device": "CPU"}},
        {"trajectory": {"radius": float("nan")}},
        {"trajectory": {"kind": "waypoints", "waypoints": [[0, 0, 0]]}},
        {"quality": {"min_gap_seconds": 60}},
        {"typo": True},
    ],
)
def test_invalid_recipes_are_explicitly_rejected(value):
    with pytest.raises(ValidationError):
        Recipe.model_validate(value)
