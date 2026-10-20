from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

from .config import PACKAGE_DIR, Settings
from .export import encoder_command, package_dataset, verify_video
from .geometry import plan
from .io import atomic_json, read_json, sha256_file
from .models import Recipe
from .quality import validate_dataset
from .store import Store

logger = logging.getLogger(__name__)


class RenderCancelled(RuntimeError):
    pass


class WorkerLock:
    def __init__(self, path: Path):
        self.path = path
        self.stream: BinaryIO | None = None

    def acquire(self) -> None:
        self.stream = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                self.stream.seek(0)
                self.stream.write(b"\0")
                self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            self.stream = None
            raise RuntimeError(
                "Another worker owns this data directory; run only one coordinator"
            ) from error

    def release(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None


class Worker:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store
        self.stopping = threading.Event()
        self.lock = WorkerLock(settings.data_dir / "worker.lock")
        self.thread: threading.Thread | None = None
        self.current_job: str | None = None
        self.deadline = float("inf")
        self.last_check = 0.0

    def start(self) -> None:
        self.lock.acquire()
        self.store.recover_interrupted()
        self.thread = threading.Thread(
            target=self._loop, name="locus-render-coordinator", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=20)
            if self.thread.is_alive():
                raise RuntimeError("Renderer did not stop within the graceful shutdown window")

    def _loop(self) -> None:
        try:
            while not self.stopping.is_set():
                self.store.heartbeat("idle")
                job = self.store.claim()
                if job:
                    self._execute(job)
                else:
                    self.stopping.wait(0.5)
        except Exception:
            logger.exception("Render coordinator stopped unexpectedly")
            self.store.heartbeat("error")
            raise
        finally:
            self.store.heartbeat("stopped")
            self.lock.release()

    def _check(self, force: bool = False) -> None:
        now = time.monotonic()
        if self.stopping.is_set():
            raise RenderCancelled("Worker stopped by the operator")
        if now > self.deadline:
            raise TimeoutError("Render job exceeded LOCUS_JOB_TIMEOUT_SECONDS")
        if not force and now - self.last_check < 0.25:
            return
        self.last_check = now
        if self.current_job:
            job = self.store.job(self.current_job)
            if job is None or job["cancel_requested"]:
                raise RenderCancelled("Job cancelled by the operator")
        self.store.heartbeat("busy")

    def _process(self, command: list[str], root: Path, render: bool = False) -> None:
        allowed = {
            "PATH",
            "HOME",
            "USERPROFILE",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "TMPDIR",
            "LD_LIBRARY_PATH",
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XDG_RUNTIME_DIR",
            "CUDA_VISIBLE_DEVICES",
            "HIP_VISIBLE_DEVICES",
            "NVIDIA_VISIBLE_DEVICES",
            "NVIDIA_DRIVER_CAPABILITIES",
            "LIBGL_ALWAYS_SOFTWARE",
            "PYTHONIOENCODING",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        environment.update(
            OMP_NUM_THREADS=str(self.settings.render_threads),
            OPENBLAS_NUM_THREADS=str(self.settings.render_threads),
            PYTHONUNBUFFERED="1",
        )
        with (root / "worker.log").open("ab") as log:
            self._check(force=True)
            process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, env=environment
            )
            last_frame, rendering = -1, False
            try:
                while process.poll() is None:
                    self._check()
                    progress_file = root / "progress.json"
                    if render and progress_file.exists():
                        progress = read_json(progress_file)
                        if progress["stage"] == "rendering":
                            if not rendering and self.current_job:
                                self.store.transition(
                                    self.current_job,
                                    "rendering",
                                    "Geometry passed; rendering RGB and metric depth",
                                    progress=0.05,
                                )
                                rendering = True
                            frames = progress["completed_frames"]
                            if frames != last_frame and self.current_job:
                                self.store.update_progress(
                                    self.current_job,
                                    frames,
                                    0.05 + 0.75 * frames / progress["total_frames"],
                                )
                                last_frame = frames
                    self.stopping.wait(0.2)
                if process.returncode != 0:
                    raise RuntimeError(
                        f"{Path(command[0]).name} exited with code {process.returncode}. "
                        "Open the job log for the renderer's exact error."
                    )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def _execute(self, job: dict[str, Any]) -> None:
        self.current_job = job["id"]
        self.deadline = time.monotonic() + self.settings.job_timeout_seconds
        self.last_check = 0
        root = self.settings.data_dir / "jobs" / job["id"]
        root.mkdir(parents=True, exist_ok=False)
        try:
            self._check(force=True)
            recipe = Recipe.model_validate(job["recipe"])
            planned = plan(recipe)
            if not planned["valid"]:
                raise ValueError("Trajectory violates kinematic gates; re-plan before rendering")
            asset = self.settings.data_dir / "assets" / job["scene"]["asset_sha256"] / "scene.glb"
            if sha256_file(asset) != job["scene"]["asset_sha256"]:
                raise ValueError("Scene integrity failed; the immutable source asset changed")
            required_space = int(planned["stats"]["raw_rgb_depth_bytes"] * 2.2 + 512 * 1024**2)
            if required_space > shutil.disk_usage(root).free * 0.9:
                raise ValueError(
                    "Insufficient free disk for RGB-D frames, encoding and atomic packaging"
                )
            executable = self.settings.blender_executable
            if executable is None:
                raise RuntimeError("Blender is unavailable; configure LOCUS_BLENDER and retry")
            atomic_json(
                root / "spec.json",
                {
                    "recipe": recipe.model_dump(),
                    "scene": job["scene"],
                    "asset_path": str(asset),
                    "plan": planned,
                },
            )
            self._process(
                [
                    executable,
                    "--background",
                    "--factory-startup",
                    "--disable-autoexec",
                    "--threads",
                    str(self.settings.render_threads),
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(PACKAGE_DIR / "blender" / "capture.py"),
                    "--",
                    str(root / "spec.json"),
                ],
                root,
                render=True,
            )
            self._check(force=True)
            preflight = read_json(root / "preflight.json")
            if not preflight["passed"]:
                report = {
                    "passed": False,
                    "training_eligible": False,
                    "certified_pairs": 0,
                    "geometry": preflight,
                    "checks": [
                        {
                            "name": "swept_camera_clearance",
                            "passed": False,
                            "measured": preflight["violations"],
                            "required": recipe.quality.camera_radius_m,
                        }
                    ],
                }
                self.store.transition(
                    job["id"],
                    "rejected",
                    "Geometry preflight rejected the trajectory before RGB rendering",
                    report=report,
                    error="Camera intersects geometry or an inside/inverted surface was detected",
                )
                return
            self.store.update_progress(job["id"], recipe.capture.frame_count, 0.8)
            if sha256_file(asset) != job["scene"]["asset_sha256"]:
                raise ValueError(
                    "Scene asset changed during rendering; outputs cannot be certified"
                )
            self.store.transition(
                job["id"], "validating", "Checking clocks, depth, camera poses and revisits"
            )
            report, _ = validate_dataset(root, recipe, self._check)
            atomic_json(root / "dataset" / "quality.json", report)
            if not report["passed"]:
                self.store.transition(
                    job["id"],
                    "rejected",
                    "Capture retained for diagnostics; dataset quality gates failed",
                    report=report,
                    error="One or more quality gates failed; inspect the quality report",
                )
                return
            self.store.transition(
                job["id"],
                "packaging",
                "Encoding and fully decoding the H.264 review proxy",
                progress=0.9,
            )
            self._process(
                encoder_command(recipe, root / "dataset", self.settings.render_threads), root
            )
            report["video"] = verify_video(root / "dataset" / "video.mp4", recipe, self._check)
            self._check(force=True)
            report["archive"] = package_dataset(root, job, report, self._check)
            self._check(force=True)
            self.store.transition(
                job["id"],
                "succeeded",
                "All gates passed; immutable export published",
                progress=1.0,
                report=report,
            )
        except RenderCancelled as error:
            logger.info("Job %s cancelled: %s", job["id"], error)
            self.store.transition(job["id"], "cancelled", str(error), error=str(error))
        except Exception as error:
            logger.exception("Job %s failed", job["id"])
            atomic_json(
                root / "failure.json", {"type": type(error).__name__, "message": str(error)}
            )
            self.store.transition(
                job["id"], "failed", "Job failed; no training export published", error=str(error)
            )
        finally:
            self.current_job = None
            self.deadline = float("inf")


def run_worker(settings: Settings) -> None:
    import signal

    worker = Worker(settings, Store(settings))
    worker.start()

    def stop_signal(signum: int, frame: Any) -> None:
        worker.stopping.set()

    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)
    try:
        while worker.thread and worker.thread.is_alive():
            worker.thread.join(timeout=1)
    finally:
        worker.stop()
