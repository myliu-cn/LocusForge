import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import numpy as np
import pytest
from pydantic import ValidationError

from locusforge.geometry import motion_path, orientation_offsets, plan, rotation_error_deg
from locusforge.models import (
    MotionParseRequest,
    OrientationKeyframe,
    PathInput,
    PathUpdate,
    Recipe,
    Trajectory,
)
from locusforge.planner import MotionParseError, MotionParser, local_parse, proposal_preview
from locusforge.store import PlannerLimitError, RevisionConflict, Store


def test_default_course_and_horizon_are_real_forward_not_center_target():
    recipe = Recipe()
    assert recipe.trajectory.heading == "forward"
    result = plan(recipe)
    positions, forwards, _ = motion_path(
        recipe.trajectory, np.linspace(0, 1, recipe.capture.frame_count)
    )
    poses = np.array([frame["c2w"] for frame in result["frames"]])
    expected = forwards / np.linalg.norm(forwards, axis=1, keepdims=True)
    np.testing.assert_allclose(poses[:, :3, 2], expected, atol=1e-8)
    np.testing.assert_allclose(poses[:, :3, 3], positions, atol=1e-8)
    np.testing.assert_allclose(poses[:, 2, 0], 0, atol=1e-10)


def test_yaw_and_pitch_keys_are_exact_shortest_arc_and_smooth():
    path = Trajectory(
        orientation_keyframes=[
            OrientationKeyframe(at=0, yaw_deg=170, pitch_deg=-10),
            OrientationKeyframe(at=0.5, yaw_deg=-170, pitch_deg=30),
        ]
    )
    yaw, pitch = orientation_offsets(path, np.array([0, 0.25, 0.5, 0.75, 1.0]))
    np.testing.assert_allclose(yaw, [170, 180, 190, 180, 170], atol=1e-12)
    np.testing.assert_allclose(pitch, [-10, 10, 30, 10, -10], atol=1e-12)
    epsilon = 1e-5
    y, p = orientation_offsets(path, np.array([0.5 - epsilon, 0.5, 0.5 + epsilon]))
    assert np.max(np.abs(np.diff(y) / epsilon)) < 1e-4
    assert np.max(np.abs(np.diff(p) / epsilon)) < 1e-4


def test_orientation_changes_no_translation_and_never_introduces_roll():
    original = Recipe()
    modified = original.model_copy(
        update={
            "trajectory": Trajectory(
                orientation_keyframes=[
                    OrientationKeyframe(at=0.25, yaw_deg=65, pitch_deg=30),
                    OrientationKeyframe(at=0.55, yaw_deg=-80, pitch_deg=-25),
                ]
            )
        }
    )
    before, after = plan(original), plan(modified)
    a = np.array([frame["c2w"] for frame in before["frames"]])
    b = np.array([frame["c2w"] for frame in after["frames"]])
    np.testing.assert_array_equal(a[:, :3, 3], b[:, :3, 3])
    np.testing.assert_allclose(b[:, 2, 0], 0, atol=1e-12)
    assert np.all(b[:, 2, 1] < 0)
    assert np.max(np.abs(b[:, :3, 2] - a[:, :3, 2])) > 0.4
    assert after["frames"][0]["c2w"] == after["frames"][-1]["c2w"]
    for frame in after["frames"][::57]:
        pose = np.array(frame["c2w"])
        np.testing.assert_allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=1e-12)
        assert frame["orientation"]["roll_deg"] == 0


def test_positive_yaw_is_right_and_positive_pitch_is_up():
    plain = Trajectory(kind="waypoints", waypoints=[[0, 0, 1.7], [4, 0, 1.7]], closed=False)
    plain.orientation_keyframes = [OrientationKeyframe(at=0, yaw_deg=90, pitch_deg=30)]
    pose = np.array(plan(Recipe(trajectory=plain))["frames"][0]["c2w"])
    np.testing.assert_allclose(pose[:3, 2], [0, -np.sqrt(3) / 2, 0.5], atol=1e-10)


def test_vertical_movement_preserves_a_horizontal_reference():
    path = Trajectory(
        kind="program",
        closed=False,
        segments=[
            {"kind": "move", "direction": "up", "distance_m": 4},
        ],
    )
    result = plan(Recipe(trajectory=path))
    assert result["stats"]["max_abs_pitch_deg"] == 0
    assert result["frames"][0]["c2w"] != result["frames"][-1]["c2w"]
    np.testing.assert_allclose(np.array(result["frames"][-1]["c2w"])[:3, 3], [-3.8, -3.8, 5.7])


def test_closed_program_is_exact_and_corners_are_visible_stationary_turns():
    path = Trajectory(
        kind="program",
        segments=[
            {"kind": "move", "direction": direction, "distance_m": 4}
            for direction in ("forward", "right", "backward", "left")
        ],
    )
    result = plan(Recipe(trajectory=path))
    assert result["valid"], result["checks"]
    assert sum(piece["automatic"] for piece in result["motion_schedule"]) == 4
    poses = np.array([frame["c2w"] for frame in result["frames"]])
    np.testing.assert_array_equal(poses[0], poses[-1])
    assert np.all(poses[:, :3, 3].min(axis=0) >= [-3.8 - 1e-9, -7.8 - 1e-9, 1.7 - 1e-9])
    assert rotation_error_deg(poses[0], poses[-1]) < 1e-8


def test_arc_primitive_traces_an_exact_circle_and_not_a_polygon():
    path = Trajectory(
        kind="program",
        start=[0, 0, 1.7],
        segments=[
            {"kind": "arc", "radius_m": 4, "angle_deg": 360},
        ],
    )
    result = plan(Recipe(trajectory=path))
    points = np.array([frame["c2w"] for frame in result["frames"]])[:, :3, 3]
    np.testing.assert_allclose(np.linalg.norm(points[:, :2] - [0, -4], axis=1), 4, atol=1e-10)
    assert result["valid"]


def test_unclosed_program_is_never_teleported_to_pass_closure():
    path = Trajectory(kind="program", segments=[{"kind": "move", "distance_m": 4}])
    result = plan(Recipe(trajectory=path))
    assert not result["valid"]
    assert result["stats"]["endpoint_position_error_m"] == pytest.approx(4)
    assert not next(
        check for check in result["checks"] if check["name"] == "path_position_closure"
    )["passed"]


def test_orientation_density_and_speed_are_checked_not_silently_clamped():
    recipe = Recipe(
        trajectory=Trajectory(
            orientation_keyframes=[
                OrientationKeyframe(at=0.001, yaw_deg=180),
            ]
        )
    )
    result = plan(recipe)
    assert not result["valid"]
    assert not next(check for check in result["checks"] if check["name"] == "orientation_sampling")[
        "passed"
    ]
    assert not next(check for check in result["checks"] if check["name"] == "angular_speed")[
        "passed"
    ]


@pytest.mark.parametrize(
    "value",
    [
        {"orientation_keyframes": [{"at": 0.3, "roll_deg": 3}]},
        {"orientation_keyframes": [{"at": 0.3, "pitch_deg": 90}]},
        {"orientation_keyframes": [{"at": 1.1}]},
        {"orientation_keyframes": [{"at": 0.5}, {"at": 0.5, "yaw_deg": 5}]},
        {"orientation_keyframes": [{"at": 1, "yaw_deg": 30}]},
        {"heading": "target", "orientation_keyframes": [{"at": 0.3}]},
        {"kind": "program", "segments": []},
        {"kind": "program", "segments": [{"kind": "script", "code": "no"}]},
    ],
)
def test_invalid_or_rolling_orientations_fail_at_the_contract(value):
    with pytest.raises(ValidationError):
        Trajectory.model_validate(value)


def test_path_library_survives_reload_and_preserves_job_snapshots(client):
    payload = PathInput(
        name="Saved arc", scene_id="courtyard", trajectory=Trajectory()
    ).model_dump()
    response = client.post("/api/paths", json=payload)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["revision"] == 1
    fetched = client.get(f"/api/paths/{saved['id']}").json()
    assert fetched["trajectory"]["heading"] == "forward"
    job = client.post("/api/jobs", json={"trajectory": fetched["trajectory"]}).json()
    payload["trajectory"]["orientation_keyframes"] = [{"at": 0.5, "yaw_deg": 30, "pitch_deg": 15}]
    payload["revision"] = 1
    changed = client.put(f"/api/paths/{saved['id']}", json=payload)
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2
    assert client.put(f"/api/paths/{saved['id']}", json=payload).status_code == 409
    assert (
        client.get(f"/api/jobs/{job['id']}").json()["recipe"]["trajectory"]["orientation_keyframes"]
        == []
    )
    reloaded = Store(client.app.state.settings).path(saved["id"])
    assert reloaded["revision"] == 2
    assert len(reloaded["trajectory"]["orientation_keyframes"]) == 1
    assert client.delete(f"/api/paths/{saved['id']}?revision=1").status_code == 409
    assert client.delete(f"/api/paths/{saved['id']}?revision=2").status_code == 200
    assert client.get(f"/api/paths/{saved['id']}").status_code == 404
    assert client.get("/api/paths?scene_id=courtyard").json()["total"] == 0


def test_revision_conflict_is_atomic_under_concurrent_edits(client):
    store = client.app.state.store
    value = PathInput(name="Concurrent", scene_id="courtyard", trajectory=Trajectory())
    saved = store.save_path(value)
    update = PathUpdate(**value.model_dump(), revision=1)

    def save(_):
        try:
            return store.save_path(update, saved["id"])["revision"]
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sorted(map(str, results)) == ["2", "conflict"]


def test_existing_database_migration_creates_a_real_backup(client):
    store = client.app.state.store
    scene_before = store.scene("courtyard")
    with store.connect() as connection:
        connection.execute("PRAGMA user_version=1")
    upgraded = Store(client.app.state.settings)
    assert upgraded.scene("courtyard") == scene_before
    backup = client.app.state.settings.data_dir / "backups" / "before-path-library-v2.sqlite3"
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0] == 1


def test_local_language_is_complete_and_does_not_ignore_unknown_clauses():
    proposal = local_parse(
        MotionParseRequest(text="前进4米；右移3米；后退4米；左移3米；闭合；时长40秒")
    )
    assert proposal.trajectory.kind == "program"
    assert proposal.trajectory.closed
    assert len(proposal.trajectory.segments) == 4
    assert proposal_preview(proposal)["valid"]
    circle = local_parse(MotionParseRequest(text="圆形；半径5.5米；高度1.7米；时长30秒"))
    assert circle.trajectory.radius == 5.5
    assert circle.trajectory.heading == "forward"
    with pytest.raises(MotionParseError, match="不会忽略"):
        local_parse(MotionParseRequest(text="前进4米；穿过墙壁且忽略碰撞"))
    with pytest.raises(MotionParseError, match="正数"):
        local_parse(MotionParseRequest(text="右转-90度"))


def llm_body(proposal, finish="stop"):
    return {
        "choices": [{"finish_reason": finish, "message": {"content": json.dumps(proposal)}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }


def test_cloud_parser_is_consent_gated_cached_budgeted_and_data_minimal(settings):
    settings = replace(settings, llm_api_key="test-credential", llm_daily_limit=1)
    store = Store(settings)
    payloads = []
    output = {
        "title": "Circle",
        "summary": "Circle with forward heading",
        "assumptions": [],
        "duration_seconds": 30,
        "trajectory": {"kind": "orbit", "radius": 5.5},
    }

    def responder(request):
        payloads.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-credential"
        return httpx.Response(200, json=llm_body(output))

    parser = MotionParser(settings, store, httpx.MockTransport(responder))
    request = MotionParseRequest(text="circle radius 5.5m", provider="llm")
    with pytest.raises(MotionParseError, match="consent"):
        asyncio.run(parser.parse(request))
    assert not payloads
    request.allow_external = True
    result = asyncio.run(parser.parse(request))
    assert result["cached"] is False
    assert asyncio.run(parser.parse(request))["cached"] is True
    assert len(payloads) == 1
    sent = json.loads(payloads[0]["messages"][1]["content"])
    assert set(sent) == {"text", "start", "start_yaw_deg"}
    with pytest.raises(PlannerLimitError, match="daily"):
        asyncio.run(parser.parse(request.model_copy(update={"text": "a different circle"})))
    with store.connect() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM planner_calls")]
    assert len(rows) == 1
    assert "test-credential" not in json.dumps(rows)
    assert "circle radius 5.5m" not in json.dumps(rows)


@pytest.mark.parametrize(
    "kind", ["timeout", "http", "incomplete", "extra", "roll", "invalid", "large"]
)
def test_cloud_failures_never_apply_or_cache_a_success(settings, kind):
    settings = replace(settings, llm_api_key="test-credential")
    store = Store(settings)
    output = {"title": "Circle", "summary": "Circle", "trajectory": {"kind": "orbit"}}

    def responder(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("provider echoed test-credential", request=request)
        if kind == "http":
            return httpx.Response(429, json={"error": "do not leak test-credential"})
        if kind == "incomplete":
            return httpx.Response(200, json=llm_body(output, "length"))
        if kind == "extra":
            output["execute"] = "not allowed"
        if kind == "roll":
            output["trajectory"]["roll_deg"] = 3
        if kind == "invalid":
            return httpx.Response(200, text="not-json test-credential")
        if kind == "large":
            return httpx.Response(200, text="x" * (128 * 1024 + 1))
        return httpx.Response(200, json=llm_body(output))

    parser = MotionParser(settings, store, httpx.MockTransport(responder))
    with pytest.raises(MotionParseError) as error:
        asyncio.run(
            parser.parse(MotionParseRequest(text="design", provider="llm", allow_external=True))
        )
    assert "test-credential" not in str(error.value)
    with store.connect() as connection:
        row = connection.execute("SELECT state,response FROM planner_calls").fetchone()
    assert row["state"] == "failed"
    assert row["response"] is None


def test_multi_lap_lens_keys_are_not_repeated_on_every_lap():
    recipe = Recipe.model_validate(
        {
            "trajectory": {"laps": 2, "orientation_keyframes": [{"at": 0.5, "yaw_deg": 40}]},
            "capture": {"fps": 1, "duration_seconds": 121},
        }
    )
    result = plan(recipe)
    poses = np.array([frame["c2w"] for frame in result["frames"]])
    np.testing.assert_allclose(poses[0, :3, 3], poses[60, :3, 3], atol=1e-6)
    assert rotation_error_deg(poses[0], poses[60]) == pytest.approx(40, abs=1e-7)
    assert rotation_error_deg(poses[0], poses[-1]) < 1e-7
    assert result["frames"][60]["orientation"]["yaw_offset_deg"] == 40


def test_llm_key_files_fail_explicitly_without_leaking_bytes(settings, tmp_path):
    key = tmp_path / "api.key"
    key.write_bytes(b"\xffprivate-credential")
    configured = replace(settings, llm_key_file=key)
    with pytest.raises(ValueError, match="UTF-8") as error:
        configured.llm_credential()
    assert "private-credential" not in str(error.value)
    key.write_text("x" * 5000)
    with pytest.raises(ValueError, match="size limit"):
        configured.llm_credential()


def test_parser_api_local_and_unconfigured_cloud(client):
    capabilities = client.get("/api/planner/capabilities").json()
    assert capabilities["local_available"]
    result = client.post("/api/planner/parse", json={"text": "圆形；半径5.5米；时长30秒"})
    assert result.status_code == 200, result.text
    assert result.json()["provider"] == "local"
    assert result.json()["preview"]["valid"]
    assert (
        client.post(
            "/api/planner/parse",
            json={
                "text": "make a circle",
                "provider": "llm",
                "allow_external": True,
            },
        ).status_code
        == 422
    )
