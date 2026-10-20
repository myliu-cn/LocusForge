import json
import struct
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from locusforge.api import create_app
from locusforge.assets import scene_record
from locusforge.config import Settings
from locusforge.models import SceneInput


def triangle_glb(overrides=None):
    binary = struct.pack("<9f3H", -1, 0, -1, 1, 0, -1, 0, 0, 1, 0, 1, 2) + b"\0\0"
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [-1, 0, -1],
                "max": [1, 0, 1],
            },
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1]}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    if overrides:
        document.update(overrides)
    encoded = json.dumps(document, allow_nan=True).encode()
    encoded += b" " * (-len(encoded) % 4)
    payload = struct.pack("<I4s", len(encoded), b"JSON") + encoded
    payload += struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(payload)) + payload


@pytest.fixture
def rights():
    return {
        "title": "Test original",
        "description": "Synthetic unit-test triangle",
        "source_url": "original:locusforge/test",
        "source_group": "unit-test-scene",
        "license": "CC0-1.0",
        "attribution": "Original unit-test fixture",
        "meters_per_unit": 1,
        "commercial_use_confirmed": True,
        "training_use_confirmed": True,
    }


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(
        data_dir=tmp_path,
        embedded_worker=False,
        blender=sys.executable,
        max_asset_mb=1,
        llm_api_key="",
        llm_key_file=tmp_path / "absent-test-key",
    )


@pytest.fixture
def client(settings, monkeypatch, rights):
    monkeypatch.setattr("locusforge.api.builtin_records", lambda _: [])
    app = create_app(settings)
    record = scene_record(
        "courtyard",
        SceneInput.model_validate(rights),
        "0" * 64,
        {"mesh_count": 1, "material_count": 1, "bytes": 100},
    )
    app.state.store.add_scene(record)
    with TestClient(app) as result:
        yield result
