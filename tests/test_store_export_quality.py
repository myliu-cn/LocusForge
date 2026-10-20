import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from locusforge.assets import scene_record
from locusforge.export import verify_export
from locusforge.geometry import plan
from locusforge.io import atomic_json
from locusforge.models import Recipe, SceneInput
from locusforge.quality import export_actions, validate_dataset
from locusforge.store import QueueFullError, Store
from locusforge.worker import WorkerLock


def test_claim_is_atomic_and_capacity_is_bounded(settings, rights):
    store = Store(replace(settings, max_queued_jobs=12))
    scene = scene_record("courtyard", SceneInput.model_validate(rights), "a" * 64, {})
    for _ in range(12):
        store.enqueue(Recipe(), scene)
    with pytest.raises(QueueFullError):
        store.enqueue(Recipe(), scene)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(12)))
    assert len({item["id"] for item in claims}) == 12
    assert store.claim() is None
    store.recover_interrupted()
    assert store.counts() == {"failed": 12}
    assert all("interrupted" in job["error"] for job in store.jobs())


def test_terminal_immutability_and_cancel_wins_before_publication(settings, rights):
    store = Store(settings)
    scene = scene_record("courtyard", SceneInput.model_validate(rights), "b" * 64, {})
    job = store.enqueue(Recipe(), scene)
    store.claim()
    store.transition(job["id"], "rendering", "rendering")
    store.transition(job["id"], "validating", "validating")
    store.transition(job["id"], "packaging", "packaging")
    store.request_cancel(job["id"])
    store.transition(job["id"], "succeeded", "success candidate", report={"passed": True})
    assert store.job(job["id"])["state"] == "cancelled"
    with pytest.raises(ValueError, match="immutable"):
        store.transition(job["id"], "rendering", "must not reopen")


def test_worker_lock_has_single_owner(settings):
    settings.initialize()
    first = WorkerLock(settings.data_dir / "worker.lock")
    second = WorkerLock(settings.data_dir / "worker.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another worker"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_export_detects_byte_tampering_and_path_escape(tmp_path):
    payload = tmp_path / "data.bin"
    payload.write_bytes(b"original")
    manifest = {
        "job_id": "unit-test",
        "training_eligible": False,
        "files": [
            {"path": "data.bin", "bytes": 8, "sha256": hashlib.sha256(b"original").hexdigest()},
        ],
    }
    atomic_json(tmp_path / "manifest.json", manifest)
    assert verify_export(tmp_path)["files_checked"] == 1
    payload.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Checksum"):
        verify_export(tmp_path)
    manifest["files"][0]["path"] = "../outside.bin"
    atomic_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="outside"):
        verify_export(tmp_path)


def test_action_transforms_compose_to_camera_poses(tmp_path):
    result = plan(Recipe())
    path = tmp_path / "actions.jsonl"
    export_actions(result, path)
    actions = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(actions) == len(result["frames"]) - 1
    for action in actions[::71]:
        index = action["from_frame"]
        current = np.array(result["frames"][index]["c2w"])
        following = np.array(result["frames"][index + 1]["c2w"])
        np.testing.assert_allclose(
            current @ action["T_camera_from_next_camera"], following, atol=1e-12
        )
        assert action["dt_seconds"] == pytest.approx(1 / 24)


def test_frozen_rgb_is_rejected_even_when_png_metadata_changes(tmp_path):
    recipe = Recipe.model_validate(
        {
            "capture": {
                "width": 64,
                "height": 64,
                "fps": 1,
                "duration_seconds": 12,
                "purpose": "preview",
            }
        }
    )
    planned = plan(recipe)
    dataset = tmp_path / "dataset"
    (dataset / "rgb").mkdir(parents=True)
    (dataset / "depth").mkdir()
    frames = []
    for frame in planned["frames"]:
        index = frame["index"]
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("frame", str(index))
        Image.new("RGB", (64, 64), (120, 100, 90)).save(
            dataset / "rgb" / f"{index:06d}.png", pnginfo=metadata
        )
        np.save(
            dataset / "depth" / f"{index:06d}.npy",
            np.full((64, 64), 5, dtype=np.float32),
            allow_pickle=False,
        )
        frames.append({**frame, "rgb": f"rgb/{index:06d}.png", "depth": f"depth/{index:06d}.npy"})
    atomic_json(dataset / "cameras.json", {"frames": frames, "intrinsics": planned["intrinsics"]})
    atomic_json(tmp_path / "render_result.json", {"max_position_pass_reprojection_error_px": 0.0})
    atomic_json(tmp_path / "preflight.json", {"passed": True, "minimum_clearance_lower_bound_m": 1})
    report, _ = validate_dataset(tmp_path, recipe, lambda: None)
    frozen_gate = next(check for check in report["checks"] if check["name"] == "non_frozen_video")
    assert frozen_gate["passed"] is False
    assert frozen_gate["measured"] == 1
    assert report["training_eligible"] is False
