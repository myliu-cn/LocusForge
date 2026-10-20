from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import uuid
from pathlib import Path
from typing import Any

from .config import PACKAGE_DIR, Settings
from .io import sha256_file, utc_now
from .models import SceneInput

SUPPORTED_REQUIRED_EXTENSIONS = {
    "KHR_mesh_quantization",
    "KHR_materials_ior",
    "KHR_materials_specular",
    "KHR_materials_clearcoat",
    "KHR_materials_unlit",
    "KHR_texture_transform",
    "KHR_lights_punctual",
}

BUILTINS: list[dict[str, Any]] = [
    {
        "id": "courtyard",
        "title": "拱廊庭院",
        "subtitle": "THE ARCADE COURTYARD",
        "description": "原创石灰岩拱廊、陶土墙面与雕塑庭院。28 × 28 米，静态不透明材质。",
        "source_url": "original:locusforge/arcade-courtyard-v1",
        "license": "CC0-1.0",
        "attribution": "LocusForge original procedural scene, CC0-1.0",
        "source_group": "lf-arcade-courtyard-v1",
        "meters_per_unit": 1.0,
        "commercial_use_confirmed": True,
        "training_use_confirmed": True,
        "tags": ["建筑", "室外", "原创 CC0"],
        "default_trajectory": {
            "radius": 5.5,
            "height": 1.7,
            "phase_deg": 225.0,
            "target": [0.0, 0.0, 1.3],
        },
    },
    {
        "id": "gallery",
        "title": "静谧展厅",
        "subtitle": "THE MONOLITH GALLERY",
        "description": "原创现代展厅、木质地面与错落展陈。26 × 22 米，固定自然光。",
        "source_url": "original:locusforge/monolith-gallery-v1",
        "license": "CC0-1.0",
        "attribution": "LocusForge original procedural scene, CC0-1.0",
        "source_group": "lf-monolith-gallery-v1",
        "meters_per_unit": 1.0,
        "commercial_use_confirmed": True,
        "training_use_confirmed": True,
        "tags": ["建筑", "室内", "原创 CC0"],
        "default_trajectory": {
            "radius": 5.5,
            "height": 1.7,
            "phase_deg": 225.0,
            "target": [0.0, 0.0, 1.5],
        },
    },
]


def split_for_group(source_group: str) -> str:
    bucket = (
        int(hashlib.sha256(f"locusforge-split-v1:{source_group}".encode()).hexdigest()[:8], 16)
        % 100
    )
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def validate_glb(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError("Not a GLB file: truncated header")
        magic, version, declared_size = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or declared_size != size:
            raise ValueError("Only complete, self-contained glTF 2.0 binary files are supported")
        chunk_header = stream.read(8)
        if len(chunk_header) != 8:
            raise ValueError("GLB JSON chunk is missing")
        length, kind = struct.unpack("<I4s", chunk_header)
        if kind != b"JSON" or length > 16 * 1024 * 1024 or length > size - 20 or length % 4:
            raise ValueError("GLB JSON chunk is invalid or exceeds 16 MiB")
        try:
            document = json.loads(stream.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("GLB metadata is not valid JSON") from error
        if (
            not isinstance(document, dict)
            or not isinstance(document.get("asset"), dict)
            or document["asset"].get("version") != "2.0"
        ):
            raise ValueError("GLB requires an asset.version of 2.0")
        pending = [document]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("GLB metadata contains non-finite numeric values")
        for field in (
            "buffers",
            "bufferViews",
            "accessors",
            "nodes",
            "meshes",
            "materials",
            "images",
            "textures",
            "animations",
            "skins",
            "scenes",
            "samplers",
            "cameras",
        ):
            group = document.get(field, [])
            if not isinstance(group, list) or any(not isinstance(item, dict) for item in group):
                raise ValueError(f"GLB {field} must be an array of objects")
        binary_bytes = 0
        binary_chunks = 0
        while stream.tell() < size:
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("Truncated GLB binary chunk")
            length, kind = struct.unpack("<I4s", header)
            if length % 4 or stream.tell() + length > size:
                raise ValueError("Invalid GLB binary chunk length")
            if kind == b"BIN\x00":
                binary_bytes += length
                binary_chunks += 1
            stream.seek(length, 1)
    try:
        if any(
            item.get("uri") and not str(item["uri"]).startswith("data:")
            for group in ("buffers", "images")
            for item in document.get(group, [])
        ):
            raise ValueError(
                "External files and remote URLs are forbidden; embed all GLB resources"
            )
        buffers = document.get("buffers", [])
        if len(buffers) != 1 or buffers[0].get("uri") or binary_chunks != 1:
            raise ValueError("Use exactly one embedded GLB binary buffer")
        if buffers[0].get("byteLength", 0) > binary_bytes:
            raise ValueError("GLB buffer length exceeds the actual binary payload")
        unsupported = set(document.get("extensionsRequired", [])) - SUPPORTED_REQUIRED_EXTENSIONS
        if unsupported:
            raise ValueError(f"Unsupported required extensions: {', '.join(sorted(unsupported))}")
        materials = document.get("materials", [])
        for material in materials:
            if material.get("alphaMode", "OPAQUE") != "OPAQUE":
                raise ValueError(
                    "Certified RGB-D requires opaque materials; remove MASK/BLEND surfaces"
                )
            transmission = material.get("extensions", {}).get("KHR_materials_transmission", {})
            if (
                transmission.get("transmissionFactor", 0) != 0
                or "transmissionTexture" in transmission
            ):
                raise ValueError(
                    "Transmissive glass is not supported by the opaque-surface contract"
                )
        primitives = [p for mesh in document.get("meshes", []) for p in mesh.get("primitives", [])]
        if not primitives or any(p.get("mode", 4) != 4 for p in primitives):
            raise ValueError("The scene must contain triangle meshes only")
        if len(document.get("nodes", [])) > 100000:
            raise ValueError("Scene exceeds the 100,000-node ingestion limit")
    except (AttributeError, TypeError, KeyError) as error:
        raise ValueError("Malformed GLB metadata structure") from error
    return {
        "mesh_count": len(document.get("meshes", [])),
        "material_count": len(materials),
        "node_count": len(document.get("nodes", [])),
        "animation_count": len(document.get("animations", [])),
        "bytes": size,
    }


def install_asset(settings: Settings, source: Path) -> tuple[str, dict[str, Any]]:
    info = validate_glb(source)
    digest = sha256_file(source)
    destination = settings.data_dir / "assets" / digest / "scene.glb"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_name(f".upload-{uuid.uuid4().hex}.glb")
        try:
            shutil.copyfile(source, temporary)
            if sha256_file(temporary) != digest:
                raise ValueError("Asset changed while being ingested")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    elif sha256_file(destination) != digest:
        raise ValueError("Content-addressed asset storage failed its integrity check")
    return digest, info


def scene_record(
    scene_id: str,
    metadata: SceneInput,
    digest: str,
    info: dict[str, Any],
) -> dict[str, Any]:
    return {
        **metadata.model_dump(),
        "id": scene_id,
        "asset_sha256": digest,
        "asset_info": info,
        "split": split_for_group(metadata.source_group),
        "created_at": utc_now(),
        "builtin": False,
        "tags": ["导入场景", metadata.license],
        "default_trajectory": {
            "radius": 5.5,
            "height": 1.7,
            "phase_deg": 225.0,
            "target": [0.0, 0.0, 1.3],
        },
    }


def builtin_records(settings: Settings) -> list[dict[str, Any]]:
    records = []
    for builtin in BUILTINS:
        source = PACKAGE_DIR / "assets" / builtin["id"] / "scene.glb"
        if not source.is_file():
            continue
        digest, info = install_asset(settings, source)
        metadata = SceneInput.model_validate(
            {key: value for key, value in builtin.items() if key in SceneInput.model_fields}
        )
        record = scene_record(builtin["id"], metadata, digest, info)
        record.update(
            builtin=True,
            tags=builtin["tags"],
            subtitle=builtin["subtitle"],
            default_trajectory=builtin["default_trajectory"],
        )
        thumbnail = source.with_name("thumbnail.png")
        if thumbnail.is_file():
            target = settings.data_dir / "assets" / digest / "thumbnail.png"
            if not target.exists():
                shutil.copyfile(thumbnail, target)
        records.append(record)
    return records
