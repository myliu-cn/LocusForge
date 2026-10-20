"""Build original, redistributable scene assets and actual Cycles thumbnails."""

import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import configure_render, light_scene, linear_color, make_camera, reset, write_json


def material(name, color, roughness=0.65, metallic=0):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    shader = result.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*linear_color(color), 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    result.diffuse_color = (*linear_color(color), 1)
    return result


def box(name, location, size, surface, bevel=0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(surface)
    if bevel:
        modifier = obj.modifiers.new("Crafted edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 2
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    return obj


def arch(x, y, rotation, stone):
    inner, outer, spring, depth = 1.65, 2.02, 2.65, 0.65
    vertices, faces = [], []
    for step in range(25):
        angle = step * math.pi / 24
        for radius, dy in (
            (inner, -depth / 2),
            (outer, -depth / 2),
            (inner, depth / 2),
            (outer, depth / 2),
        ):
            px, pz = radius * math.cos(angle), spring + radius * math.sin(angle)
            vertices.append(
                (
                    x + px * math.cos(rotation) - dy * math.sin(rotation),
                    y + px * math.sin(rotation) + dy * math.cos(rotation),
                    pz,
                )
            )
    for index in range(24):
        a, b = index * 4, (index + 1) * 4
        faces.extend(
            (
                (a, b, b + 1, a + 1),
                (a + 2, a + 3, b + 3, b + 2),
                (a, a + 2, b + 2, b),
                (a + 1, b + 1, b + 3, a + 3),
            )
        )
    faces.extend(((0, 1, 3, 2), (96, 98, 99, 97)))
    mesh = bpy.data.meshes.new("Limestone voussoirs")
    mesh.from_pydata(vertices, [], faces)
    editable = bmesh.new()
    editable.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(editable, faces=list(editable.faces))
    editable.to_mesh(mesh)
    editable.free()
    mesh.update()
    obj = bpy.data.objects.new("Arched colonnade", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(stone)
    for offset in (-1.84, 1.84):
        pillar = box(
            "Stone pier",
            (x + offset * math.cos(rotation), y + offset * math.sin(rotation), 1.32),
            (0.38, 0.65, 2.65),
            stone,
            0.025,
        )
        pillar.rotation_euler.z = rotation
        plinth = box(
            "Pier base",
            (x + offset * math.cos(rotation), y + offset * math.sin(rotation), 0.12),
            (0.52, 0.78, 0.24),
            stone,
            0.02,
        )
        plinth.rotation_euler.z = rotation


def tree(x, y, palette, rng):
    box("Terracotta planter", (x, y, 0.42), (1.6, 1.6, 0.84), palette["clay"], 0.09)
    box("Planter soil", (x, y, 0.85), (1.42, 1.42, 0.05), palette["soil"])
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.1, depth=2.9, location=(x, y, 2.1))
    bpy.context.object.name = "Tree trunk"
    bpy.context.object.data.materials.append(palette["bark"])
    for index in range(7):
        angle = index * 2.4
        location = (
            x + math.cos(angle) * 0.48,
            y + math.sin(angle) * 0.48,
            3.4 + rng.uniform(-0.3, 0.75),
        )
        bpy.ops.mesh.primitive_ico_sphere_add(
            subdivisions=2, radius=rng.uniform(0.65, 1.0), location=location
        )
        obj = bpy.context.object
        obj.name = "Olive crown"
        obj.scale = (1, 1, 0.8)
        obj.data.materials.append(palette["leaves"][index % 3])
        for polygon in obj.data.polygons:
            polygon.use_smooth = True


def courtyard():
    rng = random.Random(421)
    stone = material("Warm limestone", "e5d8bf", 0.75)
    clay = material("Fired terracotta", "b06a50")
    dark = material("Patinated bronze", "657564", 0.33, 0.72)
    grout = material("Limestone grout", "bdb9a8", 0.9)
    tiles = [
        material(f"Sandstone {index}", color, 0.8)
        for index, color in enumerate(("ddd7c5", "e2dccb", "d6d0bd", "e8dfcc"))
    ]
    box("Courtyard foundation", (0, 0, -0.17), (28, 28, 0.3), grout)
    for x in range(-7, 7):
        for y in range(-7, 7):
            box(
                "Dressed stone paver",
                (x * 2 + 1, y * 2 + 1, 0),
                (1.982, 1.982, 0.035),
                rng.choice(tiles),
            )
    for y in (-13, 13):
        box("Perimeter stucco", (0, y, 3.0), (26.8, 0.35, 6.0), clay, 0.035)
        box("Wall coping", (0, y, 6.05), (27.0, 0.5, 0.16), stone, 0.025)
        box("Colonnade roof", (0, y * 0.84, 4.92), (25.8, 4.1, 0.2), stone, 0.02)
        for x in (-8, -4, 0, 4, 8):
            arch(x, y * 0.70, 0, stone)
    for x in (-13, 13):
        box("Lateral stucco", (x, 0, 2.7), (0.35, 26.0, 5.4), clay, 0.025)
        for y in (-6, 0, 6):
            box("Wall pilaster", (x * 0.985, y, 2.6), (0.55, 0.7, 5.2), stone, 0.015)
    box("Sculpture plinth", (0, 0, 0.28), (2.45, 2.45, 0.54), stone, 0.04)
    bpy.ops.mesh.primitive_torus_add(
        major_radius=0.93,
        minor_radius=0.22,
        major_segments=64,
        minor_segments=16,
        location=(0, 0, 1.85),
        rotation=(math.pi / 2, 0, math.radians(25)),
    )
    sculpture = bpy.context.object
    sculpture.name = "Continuum - original bronze sculpture"
    sculpture.data.materials.append(dark)
    for polygon in sculpture.data.polygons:
        polygon.use_smooth = True
    box("Sculpture stem", (0, 0, 0.75), (0.26, 0.28, 0.6), dark, 0.02)
    palette = {
        "clay": clay,
        "soil": material("Soil", "514938"),
        "bark": material("Bark", "6b6148"),
        "leaves": [material(f"Olive {i}", c) for i, c in enumerate(("788764", "5d7255", "91a07c"))],
    }
    for x, y in ((-8.0, -6.3), (8.2, 6.2), (-7.9, 6.2), (8.1, -6.0)):
        tree(x, y, palette, rng)
    wood = material("Oiled walnut", "79614c", 0.45)
    for x, y in ((-7, 1.8), (7, -2.4)):
        box("Bench stone support", (x, y, 0.25), (0.6, 2.6, 0.5), stone, 0.025)
        for offset in (-0.24, 0, 0.24):
            box("Bench walnut slat", (x + offset, y, 0.58), (0.2, 2.8, 0.12), wood, 0.02)
    accent = material("Ochre artwork", "d6a755", 0.7)
    box("North gallery artwork", (4.1, 12.77, 2.5), (2.5, 0.12, 2.2), accent, 0.03)


def gallery():
    rng = random.Random(884)
    chalk = material("Chalk plaster", "e9e5dc", 0.88)
    blue = material("Mineral blue", "667e86", 0.8)
    ink = material("Basalt", "343d3a", 0.43)
    bronze = material("Brushed brass", "b79a62", 0.3, 0.65)
    wood = [
        material(f"Oak {index}", color, 0.62)
        for index, color in enumerate(("b69872", "c2a580", "b89e7d", "c6ad8c"))
    ]
    box("Foundation", (0, 0, -0.1), (26, 22, 0.2), chalk)
    for x in range(-13, 13):
        for y in range(-5, 6):
            box("Oak floorboard", (x + 0.5, y * 2, 0.035), (0.975, 1.98, 0.06), rng.choice(wood))
    for x in (-12.7, 12.7):
        box("Gallery side wall", (x, 0, 3.8), (0.4, 22, 7.6), chalk, 0.025)
    box("Blue gallery wall", (0, 10.8, 3.8), (26, 0.4, 7.6), blue, 0.025)
    box("Entrance wall", (0, -10.8, 3.4), (26, 0.4, 6.8), chalk, 0.025)
    for y in (-8, 8):
        box("Ceiling light shelf", (0, y, 6.8), (26, 4, 0.35), chalk, 0.02)
    for x in (-10, -5, 0, 5, 10):
        box("Skylight mullion", (x, 0, 7.0), (0.24, 14, 0.25), ink, 0.015)
    box("Central exhibit base", (0, 0, 0.25), (2.8, 2.6, 0.5), chalk, 0.025)
    lower = box("Monolith lower", (0, 0, 1.2), (1.15, 1.1, 1.4), ink, 0.08)
    lower.rotation_euler.z = 0.3
    upper = box("Monolith upper", (0.05, 0, 2.35), (1.45, 0.9, 1.0), bronze, 0.07)
    upper.rotation_euler.z = -0.25
    colors = ("a56b52", "d2b578", "567b74", "abb5a5")
    for index, x in enumerate((-8.5, -3.5, 3.5, 8.5)):
        box("Artwork frame", (x, 10.48, 3.1), (3.2, 0.16, 3.5), ink, 0.02)
        box(
            "Abstract color field",
            (x, 10.37, 3.1),
            (2.96, 0.06, 3.26),
            material(f"Painting {index}", colors[index]),
        )
        box("Abstract composition", (x - 0.4, 10.32, 3.4), (0.72, 0.04, 1.75), chalk)
    for x, y, height in ((-8.6, -5, 1.5), (8.5, 3.2, 1.7), (8.5, -5.7, 1.3)):
        box("Peripheral plinth", (x, y, height / 2), (1.4, 1.4, height), chalk, 0.03)
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32,
            ring_count=16,
            radius=0.6,
            location=(x, y, height + 0.6),
        )
        obj = bpy.context.object
        obj.name = "Peripheral study"
        obj.data.materials.append(bronze if x < 0 else ink)
        for face in obj.data.polygons:
            face.use_smooth = True
    for x in (-7.8, 7.8):
        box("Gallery seat", (x, 0, 0.55), (1.0, 3.2, 0.32), ink, 0.07)
        for y in (-1.1, 1.1):
            box("Seat leg", (x, y, 0.24), (0.7, 0.24, 0.48), bronze, 0.01)


def main():
    output_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    for name, builder in (("courtyard", courtyard), ("gallery", gallery)):
        target = output_root / name
        target.mkdir(parents=True, exist_ok=True)
        reset()
        builder()
        bpy.ops.object.select_all(action="DESELECT")
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.join()
        bpy.context.object.name = f"LocusForge {name}"
        bpy.ops.export_scene.gltf(
            filepath=str(target / "scene.glb"),
            export_format="GLB",
            export_animations=False,
            export_cameras=False,
            export_lights=False,
            export_copyright="Original LocusForge procedural geometry. CC0-1.0.",
        )
        light_scene()
        capture = {
            "engine": "CYCLES",
            "device": "CPU",
            "samples": 64,
            "seed": 42,
            "width": 1440,
            "height": 900,
            "fps": 24,
            "fov_deg": 68,
        }
        configure_render(capture)
        camera = make_camera(capture)
        camera.location = (-9.5, -9.0, 7.6) if name == "courtyard" else (-9.8, -9.0, 6.4)
        target_point = Vector((1.0, 1.5, 1.4))
        camera.rotation_euler = (target_point - camera.location).to_track_quat("-Z", "Y").to_euler()
        bpy.context.scene.render.filepath = str(target / "thumbnail.png")
        bpy.ops.render.render(write_still=True)
        write_json(
            target / "provenance.json",
            {
                "name": name,
                "license": "CC0-1.0",
                "generator": "seed_scenes.py",
                "blender_version": bpy.app.version_string,
                "thumbnail": "actual Cycles render",
                "third_party_assets": False,
            },
        )
        print(f"LOCUS_SEEDED {name}", flush=True)


if __name__ == "__main__":
    main()
