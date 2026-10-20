"""Executed only inside Blender; measures depth and pixel-center conventions."""

import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

out = Path(sys.argv[sys.argv.index("--") + 1])
out.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=100, location=(0, 0, 0))
bpy.ops.object.camera_add(location=(0, 0, 5))
camera = bpy.context.object
camera.rotation_euler = Vector((0, 0, -1)).to_track_quat("-Z", "Y").to_euler()
camera.data.lens = 25
camera.data.sensor_width = 36
camera.data.sensor_fit = "HORIZONTAL"
scene = bpy.context.scene
scene.camera = camera
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 8
scene.cycles.use_denoising = False
scene.render.resolution_x = 64
scene.render.resolution_y = 64
scene.render.resolution_percentage = 100
scene.view_layers[0].use_pass_z = True
scene.view_layers[0].use_pass_position = True
scene.use_nodes = True
tree = scene.node_tree
tree.nodes.clear()
layers = tree.nodes.new("CompositorNodeRLayers")
output = tree.nodes.new("CompositorNodeOutputFile")
output.base_path = str(out)
output.format.file_format = "OPEN_EXR"
output.format.color_depth = "32"
output.format.color_mode = "RGB"
output.file_slots[0].path = "position_"
tree.links.new(layers.outputs["Position"], output.inputs[0])
output.file_slots.new("depth")
output.file_slots[-1].path = "depth_"
tree.links.new(layers.outputs["Depth"], output.inputs[1])
bpy.ops.render.render()
arrays = {}
for label in ("position", "depth"):
    image = bpy.data.images.load(str(out / f"{label}_0001.exr"), check_existing=False)
    values = np.array(image.pixels[:], dtype=np.float32).reshape(64, 64, 4)[::-1].copy()
    arrays[label] = values
    bpy.data.images.remove(image)
focal = 64 * 25 / 36
y, x = np.mgrid[:64, :64]
expected_x = (x + 0.5 - 32) * 5 / focal
expected_y = -(y + 0.5 - 32) * 5 / focal
position = arrays["position"]
results = {
    "blender": bpy.app.version_string,
    "plane_z_max_error_m": float(np.max(np.abs(position[:, :, 2]))),
    "pixel_x_max_error_px": float(np.max(np.abs(position[:, :, 0] - expected_x)) * focal / 5),
    "pixel_y_max_error_px": float(np.max(np.abs(position[:, :, 1] - expected_y)) * focal / 5),
    "range_corner": float(arrays["depth"][0, 0, 0]),
    "range_center": float(arrays["depth"][32, 32, 0]),
    "position_center": position[32, 32].tolist(),
}
(out / "probe.json").write_text(json.dumps(results, indent=2))
print("LOCUS_PROBE", json.dumps(results))
