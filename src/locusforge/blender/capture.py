"""One isolated Blender process per immutable render job."""

import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import configure_render, light_scene, make_camera, reset, write_json

CV_TO_BLENDER = Matrix.Diagonal((1.0, -1.0, -1.0, 1.0))


def scene_bvh():
    vertices, polygons = [], []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type != "MESH" or obj.hide_render:
            continue
        mesh = obj.to_mesh()
        try:
            offset = len(vertices)
            if (
                len(vertices) + len(mesh.vertices) > 4000000
                or len(polygons) + len(mesh.polygons) > 2000000
            ):
                raise RuntimeError(
                    "Geometry exceeds 4M vertices / 2M polygons; optimize the scene first"
                )
            transform = instance.matrix_world
            vertices.extend(transform @ vertex.co for vertex in mesh.vertices)
            polygons.extend(
                tuple(offset + index for index in face.vertices) for face in mesh.polygons
            )
        finally:
            obj.to_mesh_clear()
    if not polygons:
        raise RuntimeError("No renderable triangle surfaces were imported")
    bounds = np.array([tuple(point) for point in vertices])
    return BVHTree.FromPolygons(vertices, polygons), {
        "vertices": len(vertices),
        "polygons": len(polygons),
        "bounds_min": bounds.min(axis=0).tolist(),
        "bounds_max": bounds.max(axis=0).tolist(),
    }


def preflight(frames, radius, tree, mesh_info):
    positions = [Vector(tuple(frame["c2w"][axis][3] for axis in range(3))) for frame in frames]
    directions = [
        Vector(value)
        for value in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    ]
    minimum = float("inf")
    violations = []
    samples = 0
    for frame_index, (start, end) in enumerate(zip(positions[:-1], positions[1:], strict=True)):
        length = (end - start).length
        steps = max(1, math.ceil(length / (radius / 2)))
        # Distance-to-surface is 1-Lipschitz. This margin covers the gaps between probes.
        margin = length / steps / 2
        for step in range(steps + 1):
            point = start.lerp(end, step / steps)
            _, _, _, clearance = tree.find_nearest(point)
            if clearance is None:
                raise RuntimeError("BVH failed to measure clearance")
            minimum = min(minimum, clearance - margin)
            samples += 1
            if clearance < radius + margin:
                violations.append(
                    {
                        "frame": frame_index,
                        "reason": "camera_surface_clearance",
                        "clearance_m": clearance,
                        "required_m": radius + margin,
                    }
                )
            elif any(
                (hit := tree.ray_cast(point, direction, 1000))[1] is not None
                and hit[1].dot(direction) > 1e-5
                for direction in directions
            ):
                violations.append(
                    {"frame": frame_index, "reason": "inside_solid_or_inverted_surface"}
                )
            if len(violations) >= 20:
                break
        if len(violations) >= 20:
            break
    return {
        "passed": not violations,
        "camera_radius_m": radius,
        "samples_tested": samples,
        "minimum_clearance_lower_bound_m": minimum,
        "violations": violations,
        **mesh_info,
        "scope": "swept camera sphere; conservative first-hit orientation checks; not a human navmesh",
    }


def compositor(directory):
    scene = bpy.context.scene
    scene.view_layers[0].use_pass_z = True
    scene.view_layers[0].use_pass_position = True
    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()
    layers = tree.nodes.new("CompositorNodeRLayers")
    composite = tree.nodes.new("CompositorNodeComposite")
    tree.links.new(layers.outputs["Image"], composite.inputs["Image"])
    output = tree.nodes.new("CompositorNodeOutputFile")
    output.base_path = str(directory)
    output.format.file_format = "OPEN_EXR"
    output.format.color_mode = "RGB"
    output.format.color_depth = "32"
    output.file_slots[0].path = "position_"
    tree.links.new(layers.outputs["Position"], output.inputs[0])
    output.file_slots.new("range")
    output.file_slots[-1].path = "range_"
    tree.links.new(layers.outputs["Depth"], output.inputs[1])


def read_pass(path, height, width):
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.name = "Non-Color"
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    result = pixels.reshape(height, width, 4)[::-1].copy()
    bpy.data.images.remove(image)
    path.unlink()
    return result


def main():
    config_path = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    spec = json.loads(config_path.read_text(encoding="utf-8"))
    root = config_path.parent
    output = root / "dataset"
    recipe, planned = spec["recipe"], spec["plan"]
    capture = recipe["capture"]
    if bpy.app.version[:2] != (4, 5):
        raise RuntimeError("This release requires Blender 4.5 LTS for the calibrated pass contract")
    reset()
    bpy.ops.import_scene.gltf(filepath=spec["asset_path"], import_pack_images=True)
    scale = spec["scene"]["meters_per_unit"]
    for obj in list(bpy.context.scene.objects):
        if obj.parent is None:
            obj.scale *= scale
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    tree, mesh_info = scene_bvh()
    check = preflight(planned["frames"], recipe["quality"]["camera_radius_m"], tree, mesh_info)
    write_json(root / "preflight.json", check)
    if not check["passed"]:
        write_json(root / "progress.json", {"stage": "rejected", "completed_frames": 0})
        return
    if spec.get("preflight_only", False):
        return
    light_scene(capture["lighting"])
    configure_render(capture)
    camera = make_camera(capture)
    for directory in (output / "rgb", output / "depth", root / "passes"):
        directory.mkdir(parents=True, exist_ok=True)
    compositor(root / "passes")
    width, height = capture["width"], capture["height"]
    projection = camera.calc_matrix_camera(
        bpy.context.evaluated_depsgraph_get(),
        x=width,
        y=height,
        scale_x=1,
        scale_y=1,
    )
    k = np.array(
        [
            [projection[0][0] * width / 2, 0, width / 2],
            [0, projection[1][1] * height / 2, height / 2],
            [0, 0, 1],
        ],
        dtype=float,
    )
    if np.max(np.abs(k - np.array(planned["intrinsics"]))) > 0.001:
        raise RuntimeError("Requested intrinsics differ from the effective Blender projection")
    actual_frames, elapsed = [], []
    maximum_projection_error = 0.0
    raw_maximum_projection_error = 0.0
    corrected_pixels = 0
    worst_projection_probe = None
    grid_y, grid_x = np.mgrid[:height, :width]
    pixel_centers = np.stack((grid_x + 0.5, grid_y + 0.5), axis=-1)
    inverse_k = np.linalg.inv(k)
    for frame in planned["frames"]:
        start = time.monotonic()
        camera.matrix_world = Matrix(frame["c2w"]) @ CV_TO_BLENDER
        bpy.context.view_layer.update()
        actual_c2w = np.array(camera.matrix_world @ CV_TO_BLENDER, dtype=float)
        actual_w2c = np.linalg.inv(actual_c2w)
        index = frame["index"]
        bpy.context.scene.render.filepath = str(output / "rgb" / f"{index:06d}.png")
        bpy.ops.render.render(write_still=True)
        world = read_pass(root / "passes" / "position_0001.exr", height, width)[:, :, :3]
        raw_z = read_pass(root / "passes" / "range_0001.exr", height, width)[:, :, 0]
        camera_points = world @ actual_w2c[:3, :3].T + actual_w2c[:3, 3]
        z = camera_points[:, :, 2].copy()
        valid = (
            np.isfinite(raw_z) & (raw_z > camera.data.clip_start) & (raw_z < camera.data.clip_end)
        )
        valid &= np.isfinite(z) & (z > camera.data.clip_start) & (z < camera.data.clip_end)
        if valid.any():
            pixels = camera_points[valid] @ k.T
            uv = pixels[:, :2] / pixels[:, 2:3]
            errors = np.linalg.norm(uv - pixel_centers[valid], axis=1)
            raw_error = float(errors.max())
            raw_maximum_projection_error = max(raw_maximum_projection_error, raw_error)
            repair = np.zeros((height, width), dtype=bool)
            repair[valid] = errors > 0.005
            repair_count = int(repair.sum())
            if raw_error > 2 or repair_count > int(valid.sum()) * 0.25:
                raise RuntimeError(
                    "Systematic position-pass calibration error; sparse ray repair is unsafe"
                )
            # Shading-position offsets on bevels can move the Position pass off its pixel ray.
            # Correct only affected pixels using the same frozen, evaluated triangle geometry.
            origin = Vector(actual_c2w[:3, 3])
            for row, column in zip(*np.nonzero(repair), strict=True):
                direction = actual_c2w[:3, :3] @ (inverse_k @ [column + 0.5, row + 0.5, 1])
                hit, _, _, _ = tree.ray_cast(
                    origin, Vector(direction).normalized(), camera.data.clip_end * 2
                )
                if hit is None:
                    raise RuntimeError(
                        "Position-pass pixel has no matching opaque geometry intersection"
                    )
                camera_point = actual_w2c[:3, :3] @ np.array(hit) + actual_w2c[:3, 3]
                z[row, column] = camera_point[2]
                camera_points[row, column] = camera_point
            corrected_pixels += repair_count
            pixels = camera_points[valid] @ k.T
            uv = pixels[:, :2] / pixels[:, 2:3]
            expected = pixel_centers[valid]
            error = float(np.max(np.linalg.norm(uv - expected, axis=1)))
            if error > maximum_projection_error:
                worst = int(np.argmax(np.linalg.norm(uv - expected, axis=1)))
                worst_projection_probe = {
                    "frame": index,
                    "expected_uv": expected[worst].tolist(),
                    "observed_uv": uv[worst].tolist(),
                    "camera_point": camera_points[valid][worst].tolist(),
                }
            maximum_projection_error = max(maximum_projection_error, error)
        z[~valid] = 0
        np.save(output / "depth" / f"{index:06d}.npy", z.astype(np.float32), allow_pickle=False)
        actual_frames.append(
            {
                "index": index,
                "timestamp_seconds": frame["timestamp_seconds"],
                "progress": frame.get("progress", index / max(1, len(planned["frames"]) - 1)),
                "orientation_command": frame.get("orientation"),
                "actual_orientation": {
                    "yaw_deg": math.degrees(math.atan2(-actual_c2w[1, 2], actual_c2w[0, 2])),
                    "pitch_deg": math.degrees(
                        math.atan2(actual_c2w[2, 2], np.linalg.norm(actual_c2w[:2, 2]))
                    ),
                    "roll_deg": math.degrees(math.atan2(actual_c2w[2, 0], -actual_c2w[2, 1])),
                },
                "c2w": actual_c2w.tolist(),
                "w2c": actual_w2c.tolist(),
                "rgb": f"rgb/{index:06d}.png",
                "depth": f"depth/{index:06d}.npy",
            }
        )
        elapsed.append(time.monotonic() - start)
        write_json(
            root / "progress.json",
            {
                "stage": "rendering",
                "completed_frames": index + 1,
                "total_frames": len(planned["frames"]),
                "median_frame_seconds": float(np.median(elapsed)),
            },
        )
    write_json(
        output / "cameras.json",
        {
            "schema_version": "1.1",
            "world": "right-handed, Z-up, meters",
            "camera": "OpenCV: right/down/forward; c2w maps camera coordinates to world",
            "pixel_coordinates": "edge-based; array[v,u] center is (u+0.5,v+0.5)",
            "depth": "float32 axial camera Z in meters; 0 means invalid/background; no pickle",
            "intrinsics": k.tolist(),
            "width": width,
            "height": height,
            "fps": capture["fps"],
            "video_duration_seconds": capture["duration_seconds"],
            "last_sample_seconds": planned["stats"]["last_sample_seconds"],
            "planner_version": planned.get("planner_version", "1.0"),
            "orientation_constraint": "world-Z horizon lock; no camera roll",
            "orientation_knots": planned.get("orientation_knots", []),
            "motion_schedule": planned.get("motion_schedule", []),
            "motion_schedule_scope": "normalized per lap; repeat recipe.trajectory.laps times",
            "frames": actual_frames,
        },
    )
    write_json(
        root / "render_result.json",
        {
            "blender_version": bpy.app.version_string,
            "blender_build_hash": bpy.app.build_hash.decode(),
            "engine": capture["engine"],
            "device": capture["device"],
            "samples": capture["samples"],
            "median_frame_seconds": float(np.median(elapsed)),
            "render_seconds": sum(elapsed),
            "max_position_pass_reprojection_error_px": maximum_projection_error,
            "raw_position_pass_max_reprojection_error_px": raw_maximum_projection_error,
            "ray_corrected_pixels": corrected_pixels,
            "ray_repair_trigger_px": 0.005,
            "worst_projection_probe": worst_projection_probe,
            "annotation": "world-position axial Z, with sparse center-ray geometry correction above 0.005 px",
            "frozen_scene_frame": 1,
            "motion_blur": False,
            "depth_of_field": False,
            "view_transform": bpy.context.scene.view_settings.view_transform,
            "look": bpy.context.scene.view_settings.look,
            "exposure": 0,
            "denoising": bool(bpy.context.scene.cycles.use_denoising)
            if capture["engine"] == "CYCLES"
            else False,
            "lighting": (
                "fixed Locus daylight rig v1; imported cameras and lights removed"
                if capture["lighting"] == "daylight"
                else "imported lights and emissive geometry frozen at frame 1; world strength 0.02"
            ),
        },
    )


if __name__ == "__main__":
    main()
