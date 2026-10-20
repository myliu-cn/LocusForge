"""Renderer-only helpers: Blender's bundled Python, not the API interpreter."""

import json
import math
import os
from pathlib import Path

import bpy


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def linear_color(hexadecimal):
    values = [int(hexadecimal[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    return tuple(
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values
    )


def reset():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.context.scene.frame_set(1)


def material_emits(material):
    if not material.use_nodes:
        return False
    for node in material.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            color, strength = node.inputs["Emission Color"], node.inputs["Emission Strength"]
        elif node.type == "EMISSION":
            color, strength = node.inputs["Color"], node.inputs["Strength"]
        else:
            continue
        if (color.is_linked or max(color.default_value[:3]) > 0) and (
            strength.is_linked or strength.default_value > 0
        ):
            return True
    return False


def light_scene(mode="daylight"):
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        if obj.type == "CAMERA" or (obj.type == "LIGHT" and mode == "daylight"):
            bpy.data.objects.remove(obj, do_unlink=True)
    world = bpy.data.worlds.new("Locus fixed daylight")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (*linear_color("dce7ef"), 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = (
        0.45 if mode == "daylight" else 0.02
    )
    scene.world = world
    if mode == "daylight":
        bpy.ops.object.light_add(
            type="SUN", rotation=(math.radians(30), math.radians(-24), math.radians(-35))
        )
        sun = bpy.context.object
        sun.name = "Locus fixed sun"
        sun.data.energy = 3.0
        sun.data.angle = math.radians(5)
        bpy.ops.object.light_add(type="AREA", location=(0, 0, 10))
        fill = bpy.context.object
        fill.name = "Locus fixed sky fill"
        fill.data.energy = 1200
        fill.data.shape = "DISK"
        fill.data.size = 14
    elif mode == "asset":
        emitted = any(
            material_emits(slot.material)
            for obj in scene.objects
            if obj.type == "MESH" and not obj.hide_render
            for slot in obj.material_slots
            if slot.material is not None
        )
        if not emitted and not any(obj.type == "LIGHT" for obj in scene.objects):
            raise RuntimeError(
                "Asset lighting requires imported lights or emissive materials; author lighting in the GLB or select daylight"
            )
    else:
        raise ValueError(f"Unsupported lighting mode: {mode}")
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.render.film_transparent = False
    scene.render.use_motion_blur = False


def configure_render(capture):
    scene = bpy.context.scene
    scene.render.engine = capture["engine"]
    scene.render.resolution_x = capture["width"]
    scene.render.resolution_y = capture["height"]
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1
    scene.render.pixel_aspect_y = 1
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 25
    scene.render.fps = capture["fps"]
    scene.render.fps_base = 1.0
    scene.render.use_persistent_data = True
    if capture["engine"] == "CYCLES":
        scene.cycles.samples = capture["samples"]
        scene.cycles.seed = capture["seed"]
        scene.cycles.use_animated_seed = False
        scene.cycles.use_adaptive_sampling = False
        scene.cycles.use_denoising = capture["samples"] >= 16
        scene.cycles.max_bounces = 6
        scene.cycles.diffuse_bounces = 3
        scene.cycles.glossy_bounces = 3
        scene.cycles.transmission_bounces = 0
        scene.cycles.device = "CPU"
        if capture["device"] != "CPU":
            preferences = bpy.context.preferences.addons["cycles"].preferences
            preferences.compute_device_type = capture["device"]
            preferences.refresh_devices()
            devices = [device for device in preferences.devices if device.type == capture["device"]]
            if not devices:
                raise RuntimeError(
                    f"No available {capture['device']} devices; CPU fallback is forbidden"
                )
            for device in preferences.devices:
                device.use = device in devices
            scene.cycles.device = "GPU"
    else:
        scene.eevee.taa_render_samples = capture["samples"]
        scene.eevee.use_raytracing = True


def make_camera(capture):
    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.name = "Locus calibrated pinhole"
    camera.data.type = "PERSP"
    camera.data.sensor_fit = "HORIZONTAL"
    camera.data.sensor_width = 36
    camera.data.lens = 36 / (2 * math.tan(math.radians(capture["fov_deg"]) / 2))
    camera.data.shift_x = 0
    camera.data.shift_y = 0
    camera.data.clip_start = 0.02
    camera.data.clip_end = 1000
    camera.data.dof.use_dof = False
    bpy.context.scene.camera = camera
    return camera
