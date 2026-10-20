import json
from dataclasses import replace

import pytest
from conftest import triangle_glb
from fastapi.testclient import TestClient

from locusforge.api import create_app, session_value, valid_session
from locusforge.assets import split_for_group, validate_glb
from locusforge.models import Recipe


def test_valid_glb_and_metadata_upload(client, rights):
    response = client.post(
        "/api/scenes",
        data={"metadata": json.dumps(rights)},
        files={"file": ("test.glb", triangle_glb(), "model/gltf-binary")},
    )
    assert response.status_code == 201, response.text
    scene = response.json()
    assert scene["asset_info"]["mesh_count"] == 1
    assert scene["split"] == split_for_group(rights["source_group"])
    content = client.get(f"/api/scenes/{scene['id']}/model")
    assert content.status_code == 200
    assert content.content == triangle_glb()
    assert client.get(f"/api/scenes/{scene['id']}/thumbnail").status_code == 404


@pytest.mark.parametrize(
    "metadata",
    [
        {"buffers": [{"byteLength": 44, "uri": "../../private.bin"}]},
        {"images": [{"uri": "https://example.invalid/texture.png"}]},
        {"materials": [{"alphaMode": "BLEND"}]},
        {
            "materials": [
                {"extensions": {"KHR_materials_transmission": {"transmissionFactor": 0.9}}}
            ]
        },
        {"extensionsRequired": ["KHR_draco_mesh_compression"]},
        {"meshes": [{"primitives": [{"mode": 1}]}]},
        {"animations": "not-an-array"},
        {"asset": "not-an-object"},
        {"nodes": [float("nan")]},
    ],
)
def test_invalid_glb_rejected_without_storage(tmp_path, metadata):
    path = tmp_path / "invalid.glb"
    path.write_bytes(triangle_glb(metadata))
    with pytest.raises(ValueError):
        validate_glb(path)


def test_truncated_glb_rejected(tmp_path):
    path = tmp_path / "truncated.glb"
    path.write_bytes(triangle_glb()[:-1])
    with pytest.raises(ValueError, match="complete"):
        validate_glb(path)


def test_license_is_an_explicit_ingestion_gate(client, rights):
    rights["training_use_confirmed"] = False
    response = client.post(
        "/api/scenes",
        data={"metadata": json.dumps(rights)},
        files={"file": ("test.glb", triangle_glb(), "model/gltf-binary")},
    )
    assert response.status_code == 422
    assert "training rights" in response.json()["detail"]


def test_same_asset_cannot_cross_source_groups(client, rights):
    for group, expected in (("scene-family-one", 201), ("scene-family-two", 422)):
        rights["source_group"] = group
        response = client.post(
            "/api/scenes",
            data={"metadata": json.dumps(rights)},
            files={"file": ("test.glb", triangle_glb(), "model/gltf-binary")},
        )
        assert response.status_code == expected, response.text


def test_upload_size_limit_and_wrong_file_type(client, rights):
    response = client.post(
        "/api/scenes",
        data={"metadata": json.dumps(rights)},
        files={"file": ("too-large.glb", b"x" * (2 * 1024**2), "model/gltf-binary")},
    )
    assert response.status_code == 413
    response = client.post(
        "/api/scenes",
        data={"metadata": json.dumps(rights)},
        files={"file": ("unsafe.blend", b"not a glb", "application/octet-stream")},
    )
    assert response.status_code == 422


def test_chunked_body_limit_returns_explicit_413(client):
    response = client.post(
        "/api/plan",
        content=(b" " * (1024 * 1024) for _ in range(3)),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text


def test_origin_and_host_boundary(client):
    response = client.post(
        "/api/plan", json=Recipe().model_dump(), headers={"Origin": "https://untrusted.invalid"}
    )
    assert response.status_code == 403
    assert client.get("/api/health", headers={"Host": "untrusted.invalid"}).status_code == 400
    assert (
        client.post(
            "/api/plan", json=Recipe().model_dump(), headers={"Origin": "http://testserver"}
        ).status_code
        == 200
    )


def test_token_session_and_streaming_download_authentication(settings, monkeypatch):
    monkeypatch.setattr("locusforge.api.builtin_records", lambda _: [])
    token = "unit-test-only-not-a-real-secret-123456789"
    with TestClient(create_app(replace(settings, api_token=token))) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/scenes").status_code == 401
        assert client.get("/api/openapi.json").status_code == 401
        response = client.post("/api/session", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/api/scenes").status_code == 200
        assert client.delete("/api/session").status_code == 200
        assert client.get("/api/scenes").status_code == 401
    session = session_value(token)
    assert valid_session(session, token)
    assert not valid_session(session, "incorrect")
    assert not valid_session("1.not.signed", token)
    assert not valid_session("9" * 5000 + ".x.x", token)
    assert not valid_session("1799999999." + "a" * 32 + "." + "\u00e9" * 64, token)


def test_plan_is_normalized_and_rejects_bad_input(client):
    schema = client.get("/api/openapi.json").json()
    assert "SceneInput" in schema["components"]["schemas"]
    response = client.post("/api/plan", json={"scene_id": "courtyard"})
    assert response.status_code == 200
    value = response.json()
    assert value["recipe"]["capture"]["fps"] == 24
    assert value["geometry_checked"] is False
    assert len(value["frames"]) == 720
    assert client.post("/api/plan", json={"scene_id": "missing"}).status_code == 404
    assert client.post("/api/plan", json={"trajectory": {"radius": -1}}).status_code == 422
    assert client.post("/api/plan", json={"capture": {"width": 999}}).status_code == 422


def test_weak_or_non_ascii_tokens_rejected_at_startup(settings):
    with pytest.raises(ValueError, match="32 ASCII"):
        replace(settings, api_token="short")
    with pytest.raises(ValueError, match="32 ASCII"):
        replace(settings, api_token="\u00e9" * 40)


def test_job_idempotency_cancellation_retry_and_quarantine(client):
    recipe = Recipe().model_dump()
    headers = {"Idempotency-Key": "unit-test-key-001"}
    first = client.post("/api/jobs", json=recipe, headers=headers)
    assert first.status_code == 202, first.text
    job_id = first.json()["id"]
    same = client.post("/api/jobs", json=recipe, headers=headers)
    assert same.json()["id"] == job_id
    recipe["capture"]["seed"] = 100
    assert client.post("/api/jobs", json=recipe, headers=headers).status_code == 422
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["state"] == "cancelled"
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    retry = client.post(f"/api/jobs/{job_id}/retry")
    assert retry.status_code == 202
    assert retry.json()["id"] != job_id
    assert retry.json()["parent_id"] == job_id
    assert len(client.get(f"/api/jobs/{job_id}").json()["events"]) == 2
    assert client.get("/api/jobs?limit=1&offset=1").status_code == 200


def test_traversal_and_unpublished_partial_frames_blocked(client):
    job = client.post("/api/jobs", json=Recipe().model_dump()).json()
    assert client.get(f"/api/jobs/{job['id']}/files/rgb/000000.png").status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/files/worker.log").status_code == 404
    assert client.get("/api/jobs/..%5c..%5cprivate/download").status_code == 404
    assert client.get("/api/scenes/courtyard/private").status_code == 404
    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/%2e%2e%5cprivate").status_code in (404, 503)


def test_missing_renderer_returns_actionable_error(settings, monkeypatch, rights):
    monkeypatch.setattr("locusforge.api.builtin_records", lambda _: [])
    from locusforge.assets import scene_record
    from locusforge.models import SceneInput

    app = create_app(replace(settings, blender="definitely-not-a-blender-executable"))
    app.state.store.add_scene(
        scene_record("courtyard", SceneInput.model_validate(rights), "0" * 64, {})
    )
    with TestClient(app) as client:
        response = client.post("/api/jobs", json=Recipe().model_dump())
        assert response.status_code == 503
        assert "LOCUS_BLENDER" in response.json()["detail"]
