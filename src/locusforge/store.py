from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import Settings
from .io import utc_now
from .models import PathInput, PathUpdate, Recipe

ACTIVE_STATES = ("preflight", "rendering", "validating", "packaging")
TERMINAL_STATES = ("succeeded", "rejected", "failed", "cancelled")


class QueueFullError(ValueError):
    pass


class RevisionConflict(ValueError):
    pass


class PlannerLimitError(ValueError):
    pass


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.initialize()
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError(f"Unsupported database schema version {version}")
            if version == 1:
                backup_path = settings.data_dir / "backups" / "before-path-library-v2.sqlite3"
                backup_path.parent.mkdir(exist_ok=True)
                if not backup_path.exists():
                    with sqlite3.connect(backup_path) as backup:
                        connection.backup(backup)
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS scenes (
                    id TEXT PRIMARY KEY, asset_sha256 TEXT NOT NULL,
                    source_group TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scenes_digest ON scenes(asset_sha256);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    recipe TEXT NOT NULL, scene TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0, completed_frames INTEGER NOT NULL DEFAULT 0,
                    error TEXT, report TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    parent_id TEXT, idempotency_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paths (
                    id TEXT PRIMARY KEY, scene_id TEXT NOT NULL REFERENCES scenes(id),
                    asset_sha256 TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS paths_scene ON paths(scene_id,updated_at);
                CREATE TABLE IF NOT EXISTS planner_calls (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, model TEXT NOT NULL,
                    created_at TEXT NOT NULL, state TEXT NOT NULL,
                    response TEXT, error_code TEXT, usage TEXT
                );
                CREATE INDEX IF NOT EXISTS planner_digest ON planner_calls(digest,state);
                CREATE INDEX IF NOT EXISTS planner_daily ON planner_calls(created_at,state);
                PRAGMA user_version=2;
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.settings.database, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add_scene(self, scene: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT asset_sha256,source_group FROM scenes WHERE id=?", (scene["id"],)
            ).fetchone()
            if existing and (
                existing["asset_sha256"] != scene["asset_sha256"]
                or existing["source_group"] != scene["source_group"]
            ):
                raise ValueError(
                    "Scene IDs are immutable; register changed assets under a new versioned ID"
                )
            same_asset = connection.execute(
                "SELECT source_group FROM scenes WHERE asset_sha256=? LIMIT 1",
                (scene["asset_sha256"],),
            ).fetchone()
            if same_asset and same_asset["source_group"] != scene["source_group"]:
                raise ValueError(
                    "Identical assets must share a source_group to prevent split leakage"
                )
            connection.execute(
                "INSERT OR IGNORE INTO scenes VALUES (?,?,?,?)",
                (scene["id"], scene["asset_sha256"], scene["source_group"], json.dumps(scene)),
            )

    def scenes(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT document FROM scenes ORDER BY rowid").fetchall()
        return [json.loads(row[0]) for row in rows]

    def scene(self, scene_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT document FROM scenes WHERE id=?", (scene_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _path(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        document = json.loads(record.pop("document"))
        return {**document, **record}

    def paths(self, scene_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paths WHERE scene_id=? ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                (scene_id, limit, offset),
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) FROM paths WHERE scene_id=?", (scene_id,)
            ).fetchone()[0]
        return {"items": [self._path(row) for row in rows], "total": total}

    def path(self, path_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM paths WHERE id=?", (path_id,)).fetchone()
        return self._path(row) if row else None

    def save_path(self, value: PathInput, path_id: str | None = None) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            scene = connection.execute(
                "SELECT asset_sha256 FROM scenes WHERE id=?", (value.scene_id,)
            ).fetchone()
            if scene is None:
                raise KeyError("Scene not found")
            if path_id is None:
                count = connection.execute("SELECT COUNT(*) FROM paths").fetchone()[0]
                if count >= 1000:
                    raise PlannerLimitError("Path library capacity reached (1,000 designs)")
                path_id = uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO paths VALUES (?,?,?,1,?,?,?)",
                    (
                        path_id,
                        value.scene_id,
                        scene["asset_sha256"],
                        now,
                        now,
                        value.model_dump_json(),
                    ),
                )
            else:
                if not isinstance(value, PathUpdate):
                    raise RevisionConflict("Updating a saved path requires its current revision")
                previous = connection.execute(
                    "SELECT * FROM paths WHERE id=?", (path_id,)
                ).fetchone()
                if previous is None:
                    raise KeyError("Path not found")
                if previous["revision"] != value.revision:
                    raise RevisionConflict(
                        "This path was changed in another session. Reload it or save a new copy."
                    )
                if (
                    previous["scene_id"] != value.scene_id
                    or previous["asset_sha256"] != scene["asset_sha256"]
                ):
                    raise RevisionConflict(
                        "A saved path is bound to its original scene; save a copy for another scene"
                    )
                connection.execute(
                    "UPDATE paths SET revision=revision+1,updated_at=?,document=? WHERE id=?",
                    (now, value.model_dump_json(exclude={"revision"}), path_id),
                )
        record = self.path(path_id)
        if record is None:
            raise RuntimeError("Saved path could not be read back")
        return record

    def delete_path(self, path_id: str, revision: int) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT revision FROM paths WHERE id=?", (path_id,)).fetchone()
            if row is None:
                raise KeyError("Path not found")
            if row["revision"] != revision:
                raise RevisionConflict("Path revision changed; reload before deleting")
            connection.execute("DELETE FROM paths WHERE id=?", (path_id,))

    def begin_planner_call(self, digest: str, model: str, daily_limit: int) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cached = connection.execute(
                "SELECT id,response FROM planner_calls WHERE digest=? AND state='succeeded' ORDER BY created_at DESC LIMIT 1",
                (digest,),
            ).fetchone()
            if cached:
                return {
                    "id": cached["id"],
                    "cached": True,
                    "result": json.loads(cached["response"]),
                }
            connection.execute(
                "UPDATE planner_calls SET state='failed',error_code='interrupted' WHERE state='pending' AND created_at<?",
                ((now - timedelta(seconds=180)).isoformat(),),
            )
            pending = connection.execute(
                "SELECT digest FROM planner_calls WHERE state='pending'"
            ).fetchall()
            if len(pending) >= 2 or any(row["digest"] == digest for row in pending):
                raise PlannerLimitError(
                    "A matching parse is already running, or both parser slots are busy"
                )
            count = connection.execute(
                "SELECT COUNT(*) FROM planner_calls WHERE created_at>=?",
                (now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),),
            ).fetchone()[0]
            if count >= daily_limit:
                raise PlannerLimitError(
                    "The persistent daily LLM call budget is exhausted; local parsing remains available"
                )
            call_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO planner_calls(id,digest,model,created_at,state) VALUES (?,?,?,?,'pending')",
                (call_id, digest, model, now.isoformat()),
            )
        return {"id": call_id, "cached": False}

    def finish_planner_call(
        self,
        call_id: str,
        result: dict[str, Any] | None,
        error_code: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE planner_calls SET state=?,response=?,error_code=?,usage=? WHERE id=?",
                (
                    "succeeded" if result is not None else "failed",
                    json.dumps(result) if result is not None else None,
                    error_code,
                    json.dumps(usage) if usage is not None else None,
                    call_id,
                ),
            )

    def enqueue(
        self,
        recipe: Recipe,
        scene: dict[str, Any],
        parent_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = utc_now()
        encoded = json.dumps(recipe.model_dump(), sort_keys=True, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                previous = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if previous:
                    if previous["recipe"] != encoded:
                        raise ValueError("Idempotency key is already bound to a different recipe")
                    return self._job(previous)
            count = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','preflight','rendering','validating','packaging')"
            ).fetchone()[0]
            if count >= self.settings.max_queued_jobs:
                raise QueueFullError("The render queue is full; wait for jobs to finish")
            connection.execute(
                """INSERT INTO jobs
                (id,name,state,created_at,updated_at,recipe,scene,parent_id,idempotency_key)
                VALUES (?,?,'queued',?,?,?,?,?,?)""",
                (
                    job_id,
                    recipe.name,
                    now,
                    now,
                    encoded,
                    json.dumps(scene),
                    parent_id,
                    idempotency_key,
                ),
            )
            self._event(connection, job_id, "queued", "Immutable recipe accepted into the queue")
        result = self.job(job_id)
        if result is None:
            raise RuntimeError("Enqueued job could not be read back")
        return result

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for name in ("recipe", "scene", "report"):
            result[name] = json.loads(result[name]) if result[name] else None
        result["cancel_requested"] = bool(result["cancel_requested"])
        result.pop("idempotency_key", None)
        return result

    def jobs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [self._job(row) for row in rows]

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row) if row else None

    @staticmethod
    def _event(connection: sqlite3.Connection, job_id: str, state: str, message: str) -> None:
        connection.execute(
            "INSERT INTO events(job_id,timestamp,state,message) VALUES (?,?,?,?)",
            (job_id, utc_now(), state, message),
        )

    def transition(
        self,
        job_id: str,
        state: str,
        message: str,
        *,
        progress: float | None = None,
        error: str | None = None,
        report: dict[str, Any] | None = None,
    ) -> None:
        if state not in (*ACTIVE_STATES, *TERMINAL_STATES):
            raise ValueError(f"Invalid job state: {state}")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT state,cancel_requested FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if current is None:
                raise KeyError(job_id)
            if current["state"] in TERMINAL_STATES:
                raise ValueError("Terminal jobs are immutable; create a retry instead")
            allowed = {
                "queued": {"preflight", "cancelled", "failed"},
                "preflight": {"rendering", "validating", "rejected", "failed", "cancelled"},
                "rendering": {"validating", "failed", "cancelled"},
                "validating": {"packaging", "rejected", "failed", "cancelled"},
                "packaging": {"succeeded", "failed", "cancelled"},
            }
            if state not in allowed[current["state"]]:
                raise ValueError(f"Invalid job transition: {current['state']} -> {state}")
            if state in ("succeeded", "rejected") and current["cancel_requested"]:
                state, message = (
                    "cancelled",
                    "Cancelled before publication; no dataset was released",
                )
            connection.execute(
                """UPDATE jobs SET state=?,updated_at=?,progress=COALESCE(?,progress),
                error=?,report=COALESCE(?,report) WHERE id=?""",
                (state, utc_now(), progress, error, json.dumps(report) if report else None, job_id),
            )
            self._event(connection, job_id, state, message)

    def update_progress(self, job_id: str, frame_count: int, progress: float) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET completed_frames=?,progress=?,updated_at=? WHERE id=?",
                (frame_count, progress, utc_now(), job_id),
            )

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] in TERMINAL_STATES:
                raise ValueError("The job is already terminal")
            connection.execute(
                "UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?", (utc_now(), job_id)
            )
            if row["state"] == "queued":
                connection.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job_id,))
            self._event(connection, job_id, row["state"], "Cancellation requested by the operator")
        result = self.job(job_id)
        if result is None:
            raise RuntimeError("Cancelled job could not be read back")
        return result

    def claim(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE state='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET state='preflight',updated_at=? WHERE id=?", (utc_now(), row["id"])
            )
            self._event(connection, row["id"], "preflight", "Geometry preflight started")
        return self.job(row["id"])

    def recover_interrupted(self) -> None:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs WHERE state IN ('preflight','rendering','validating','packaging')"
            ).fetchall()
            for row in rows:
                connection.execute(
                    """UPDATE jobs SET state='failed',updated_at=?,error=? WHERE id=?""",
                    (
                        utc_now(),
                        "Worker interrupted before atomic completion. Retry creates a new job.",
                        row["id"],
                    ),
                )
                self._event(
                    connection,
                    row["id"],
                    "failed",
                    "Recovered interrupted job; outputs not published",
                )

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT timestamp,state,message FROM events WHERE job_id=? ORDER BY id",
                    (job_id,),
                ).fetchall()
            ]

    def heartbeat(self, state: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('worker',?)",
                (json.dumps({"state": state, "timestamp": utc_now()}),),
            )

    def worker_status(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='worker'").fetchone()
        return json.loads(row[0]) if row else None

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                row["state"]: row["count"]
                for row in connection.execute(
                    "SELECT state,COUNT(*) AS count FROM jobs GROUP BY state"
                ).fetchall()
            }

    def statistics(self) -> dict[str, int]:
        with self.connect() as connection:
            row = connection.execute("""
                SELECT
                    COALESCE(SUM(json_extract(report, '$.certified_pairs')), 0) AS certified_pairs,
                    COALESCE(SUM(completed_frames), 0) AS completed_frames,
                    COALESCE(SUM(json_extract(report, '$.training_eligible')), 0) AS training_datasets
                FROM jobs WHERE state='succeeded'
            """).fetchone()
        return dict(row)
