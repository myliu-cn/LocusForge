import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from locusforge.config import PACKAGE_DIR
from locusforge.geometry import plan
from locusforge.io import atomic_json
from locusforge.models import Recipe

BLENDER = shutil.which(os.environ.get("LOCUS_BLENDER", "blender"))
pytestmark = [
    pytest.mark.renderer,
    pytest.mark.skipif(
        BLENDER is None, reason="Set LOCUS_BLENDER to run actual renderer calibration"
    ),
]


def test_asset_lighting_is_explicit_and_preserves_authored_lights():
    code = f"""
import sys, bpy
sys.path.insert(0, {str(PACKAGE_DIR / "blender")!r})
from common import reset, light_scene
reset()
bpy.ops.mesh.primitive_cube_add()
material = bpy.data.materials["Material"]
bpy.context.object.data.materials.append(material)
try:
    light_scene("asset")
except RuntimeError:
    pass
else:
    raise AssertionError("An unlit asset must not silently receive invented lighting")
bpy.ops.object.light_add(type="POINT", location=(1, 2, 3))
light = bpy.context.object
light.data.energy = 150
light_scene("asset")
assert len([obj for obj in bpy.context.scene.objects if obj.type == "LIGHT"]) == 1
assert tuple(light.location) == (1, 2, 3)
assert light.data.energy == 150
assert abs(bpy.context.scene.world.node_tree.nodes["Background"].inputs[1].default_value - 0.02) < 1e-6
bpy.data.objects.remove(light, do_unlink=True)
material.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 2
light_scene("asset")
assert not any(obj.type == "LIGHT" for obj in bpy.context.scene.objects)
print("ASSET_LIGHTING_VERIFIED")
"""
    result = subprocess.run(
        [
            BLENDER,
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python-expr",
            code,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "ASSET_LIGHTING_VERIFIED" in result.stdout


def test_actual_blender_depth_and_pixel_center_calibration(tmp_path):
    subprocess.run(
        [
            BLENDER,
            "--background",
            "--factory-startup",
            "--threads",
            "2",
            "--python-exit-code",
            "1",
            "--python",
            str(Path(__file__).with_name("blender_probe.py")),
            "--",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        timeout=90,
    )
    result = json.loads((tmp_path / "probe.json").read_text())
    assert result["plane_z_max_error_m"] < 1e-5
    assert result["pixel_x_max_error_px"] < 0.02
    assert result["pixel_y_max_error_px"] < 0.02
    assert result["range_corner"] == pytest.approx(5, abs=1e-5)
    assert result["range_center"] == pytest.approx(5, abs=1e-5)


def test_inside_geometry_is_rejected_before_rendering(tmp_path):
    recipe = Recipe.model_validate(
        {
            "trajectory": {"radius": 0.5, "height": 0.28},
            "capture": {"width": 64, "height": 64, "fps": 2, "duration_seconds": 12, "samples": 1},
            "quality": {"min_excursion_m": 0.1},
        }
    )
    atomic_json(
        tmp_path / "spec.json",
        {
            "recipe": recipe.model_dump(),
            "scene": {"meters_per_unit": 1},
            "asset_path": str(PACKAGE_DIR / "assets" / "courtyard" / "scene.glb"),
            "plan": plan(recipe),
        },
    )
    subprocess.run(
        [
            BLENDER,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--threads",
            "2",
            "--python-exit-code",
            "1",
            "--python",
            str(PACKAGE_DIR / "blender" / "capture.py"),
            "--",
            str(tmp_path / "spec.json"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    result = json.loads((tmp_path / "preflight.json").read_text())
    assert not result["passed"]
    assert result["violations"]
    assert not (tmp_path / "dataset" / "rgb").exists()


def test_non_square_hd_actual_projection_and_metric_depth(tmp_path):
    recipe = Recipe.model_validate(
        {
            "capture": {
                "width": 1280,
                "height": 720,
                "fps": 1,
                "duration_seconds": 12,
                "samples": 1,
                "purpose": "preview",
            },
        }
    )
    planned = plan(recipe)
    planned["frames"] = planned["frames"][:2]
    atomic_json(
        tmp_path / "spec.json",
        {
            "recipe": recipe.model_dump(),
            "scene": {"meters_per_unit": 1},
            "asset_path": str(PACKAGE_DIR / "assets" / "courtyard" / "scene.glb"),
            "plan": planned,
        },
    )
    subprocess.run(
        [
            BLENDER,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--threads",
            "2",
            "--python-exit-code",
            "1",
            "--python",
            str(PACKAGE_DIR / "blender" / "capture.py"),
            "--",
            str(tmp_path / "spec.json"),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    result = json.loads((tmp_path / "render_result.json").read_text())
    assert result["max_position_pass_reprojection_error_px"] < 0.02
    depth = np.load(tmp_path / "dataset" / "depth" / "000000.npy", allow_pickle=False)
    assert depth.shape == (720, 1280)
    assert depth.dtype == np.float32
    assert np.isfinite(depth).all()
    assert (depth > 0).mean() > 0.5


@pytest.mark.parametrize("pitch", [-25, 25])
def test_rendered_orientation_matches_yaw_pitch_and_has_no_roll(tmp_path, pitch):
    recipe = Recipe.model_validate(
        {
            "trajectory": {
                "heading": "forward",
                "orientation_keyframes": [
                    {"at": 0, "yaw_deg": 35, "pitch_deg": pitch},
                    {"at": 0.5, "yaw_deg": -40, "pitch_deg": -pitch},
                ],
            },
            "capture": {
                "width": 160,
                "height": 90,
                "fps": 4,
                "duration_seconds": 24,
                "samples": 1,
                "purpose": "preview",
            },
        }
    )
    planned = plan(recipe)
    planned["frames"] = planned["frames"][:2]
    atomic_json(
        tmp_path / "spec.json",
        {
            "recipe": recipe.model_dump(),
            "scene": {"meters_per_unit": 1},
            "asset_path": str(PACKAGE_DIR / "assets" / "courtyard" / "scene.glb"),
            "plan": planned,
        },
    )
    subprocess.run(
        [
            BLENDER,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--threads",
            "2",
            "--python-exit-code",
            "1",
            "--python",
            str(PACKAGE_DIR / "blender" / "capture.py"),
            "--",
            str(tmp_path / "spec.json"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    cameras = json.loads((tmp_path / "dataset" / "cameras.json").read_text())
    assert cameras["schema_version"] == "1.1"
    for actual, expected in zip(cameras["frames"], planned["frames"], strict=True):
        np.testing.assert_allclose(actual["c2w"], expected["c2w"], atol=1e-6)
        assert abs(actual["actual_orientation"]["roll_deg"]) < 0.001
        assert actual["actual_orientation"]["pitch_deg"] == pytest.approx(
            expected["orientation"]["pitch_deg"], abs=0.0001
        )
        assert actual["orientation_command"] == expected["orientation"]
    assert abs(cameras["frames"][0]["actual_orientation"]["pitch_deg"]) > 24
