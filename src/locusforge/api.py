from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .assets import builtin_records, install_asset, scene_record
from .config import Settings
from .geometry import plan
from .io import read_json, safe_child
from .models import Capture, MotionParseRequest, PathInput, PathUpdate, Quality, Recipe, SceneInput
from .planner import MotionParseError, MotionParser
from .store import PlannerLimitError, QueueFullError, RevisionConflict, Store
from .worker import Worker

logger = logging.getLogger(__name__)
SESSION_COOKIE = "locus_session"
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
EXPORT_FILE = re.compile(
    r"^(?:rgb/\d{6}\.png|depth/\d{6}\.npy|video\.mp4|"
    r"cameras\.json|actions\.jsonl|loops\.json|quality\.json|recipe\.json|scene\.json|manifest\.json)$"
)


class BodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body exceeds the upload limit")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, upload_limit: int):
        self.app, self.upload_limit = app, upload_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        maximum = self.upload_limit if scope["path"] == "/api/scenes" else 2 * 1024**2
        headers = dict(scope["headers"])
        length = headers.get(b"content-length", b"0")
        if not length.isdigit() or int(length) > maximum:
            await JSONResponse({"detail": "Request body exceeds the upload limit"}, 413)(
                scope, receive, send
            )
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum:
                raise BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            await JSONResponse({"detail": "Request body exceeds the upload limit"}, 413)(
                scope, receive, send
            )


def session_value(token: str) -> str:
    payload = f"{int(time.time()) + 8 * 3600}.{secrets.token_hex(16)}"
    signature = hmac.new(token.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def valid_session(value: str, token: str) -> bool:
    if re.fullmatch(r"[0-9]{10}\.[0-9a-f]{32}\.[0-9a-f]{64}", value) is None:
        return False
    parts = value.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or int(parts[0]) < time.time():
        return False
    payload = ".".join(parts[:2])
    expected = hmac.new(token.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(parts[2], expected)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for record in await run_in_threadpool(builtin_records, settings):
            store.add_scene(record)
        worker = Worker(settings, store) if settings.embedded_worker else None
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                await run_in_threadpool(worker.stop)

    app = FastAPI(
        title="LocusForge",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.settings = settings
    app.state.motion_parser = MotionParser(settings, store)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
    )
    app.add_middleware(
        BodyLimitMiddleware, upload_limit=settings.max_asset_mb * 1024**2 + 256 * 1024
    )

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        request_id = uuid.uuid4().hex[:16]
        origin = request.headers.get("origin")
        own_origin = str(request.base_url).rstrip("/")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and origin.rstrip("/") not in (*settings.allowed_origins, own_origin)
        ):
            return JSONResponse({"detail": "Cross-origin mutation is not allowed"}, status_code=403)
        if (
            request.url.path.startswith("/api/")
            and request.url.path != "/api/health"
            and request.method != "OPTIONS"
            and settings.api_token
        ):
            bearer = request.headers.get("authorization", "")
            authorized = hmac.compare_digest(
                bearer.encode("utf-8"), f"Bearer {settings.api_token}".encode()
            )
            authorized |= valid_session(request.cookies.get(SESSION_COOKIE, ""), settings.api_token)
            if not authorized:
                return JSONResponse({"detail": "An API token is required"}, status_code=401)
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; "
            "connect-src 'self'; worker-src 'self' blob:; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception("Unhandled API error on %s", request.url.path, exc_info=error)
        return JSONResponse(
            {"detail": "Unexpected server error; inspect the server log"}, status_code=500
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "authentication_required": bool(settings.api_token),
        }

    @app.get("/api/openapi.json")
    def schema() -> dict[str, Any]:
        document = app.openapi()
        document.setdefault("components", {}).setdefault("schemas", {})["SceneInput"] = (
            SceneInput.model_json_schema()
        )
        return document

    @app.post("/api/session")
    def authenticate(request: Request, response: Response) -> dict[str, str]:
        if settings.api_token:
            response.set_cookie(
                SESSION_COOKIE,
                session_value(settings.api_token),
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="strict",
                max_age=8 * 3600,
                path="/api",
            )
        return {"status": "authenticated"}

    @app.delete("/api/session")
    def sign_out(response: Response) -> dict[str, str]:
        response.delete_cookie(SESSION_COOKIE, path="/api")
        return {"status": "signed_out"}

    @app.get("/api/system")
    def system() -> dict[str, Any]:
        heartbeat = store.worker_status()
        online = bool(
            heartbeat
            and heartbeat["state"] in ("idle", "busy")
            and (datetime.now(UTC) - datetime.fromisoformat(heartbeat["timestamp"])).total_seconds()
            < 15
        )
        disk = shutil.disk_usage(settings.data_dir)
        return {
            "version": __version__,
            "blender_available": settings.blender_executable is not None,
            "worker_online": online,
            "worker": heartbeat,
            "render_threads": settings.render_threads,
            "free_disk_bytes": disk.free,
            "total_disk_bytes": disk.total,
            "max_asset_mb": settings.max_asset_mb,
            "max_queued_jobs": settings.max_queued_jobs,
            "authentication": "token" if settings.api_token else "loopback-only",
            "counts": store.counts(),
            "scene_count": len(store.scenes()),
            "statistics": store.statistics(),
            "renderer_contract": "Blender 4.5 LTS; static opaque surfaces; exact engine camera poses",
            "gpu": "Explicit device selection; availability is checked inside the actual renderer.",
        }

    @app.get("/api/scenes")
    def scenes() -> list[dict[str, Any]]:
        return store.scenes()

    @app.post("/api/scenes", status_code=201)
    async def upload_scene(
        file: Annotated[UploadFile, File()],
        metadata: Annotated[str, Form(max_length=16000)],
    ) -> dict[str, Any]:
        temporary = settings.data_dir / "incoming" / f"{uuid.uuid4().hex}.glb"
        try:
            if not file.filename or not file.filename.lower().endswith(".glb"):
                raise HTTPException(422, "Only self-contained .glb scene assets are accepted")
            try:
                values = SceneInput.model_validate_json(metadata)
            except ValidationError as error:
                raise HTTPException(422, str(error)) from error
            size = 0
            with temporary.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_asset_mb * 1024**2:
                        raise HTTPException(413, "Asset exceeds LOCUS_MAX_ASSET_MB")
                    destination.write(chunk)
            digest, info = await run_in_threadpool(install_asset, settings, temporary)
            record = scene_record(
                f"asset-{digest[:12]}-{uuid.uuid4().hex[:6]}", values, digest, info
            )
            await run_in_threadpool(store.add_scene, record)
            return record
        except ValueError as error:
            logger.info("Asset rejected: %s", error)
            raise HTTPException(422, str(error)) from error
        finally:
            temporary.unlink(missing_ok=True)
            await file.close()

    @app.get("/api/scenes/{scene_id}/{resource}")
    def scene_resource(scene_id: str, resource: str) -> FileResponse:
        scene = store.scene(scene_id)
        if not scene or resource not in ("model", "thumbnail"):
            raise HTTPException(404, "Scene resource not found")
        filename = "scene.glb" if resource == "model" else "thumbnail.png"
        path = settings.data_dir / "assets" / scene["asset_sha256"] / filename
        if not path.is_file():
            raise HTTPException(404, "No thumbnail yet; use the geometry viewport")
        return FileResponse(
            path, media_type="model/gltf-binary" if resource == "model" else "image/png"
        )

    @app.post("/api/plan")
    def plan_recipe(recipe: Recipe) -> dict[str, Any]:
        if not store.scene(recipe.scene_id):
            raise HTTPException(404, "Scene not found")
        try:
            return {**plan(recipe), "recipe": recipe.model_dump()}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/planner/capabilities")
    def planner_capabilities() -> dict[str, Any]:
        return app.state.motion_parser.capabilities()

    @app.post("/api/planner/parse")
    async def parse_motion(value: MotionParseRequest) -> dict[str, Any]:
        try:
            return await app.state.motion_parser.parse(value)
        except PlannerLimitError as error:
            raise HTTPException(429, str(error)) from error
        except (MotionParseError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    def validate_path_design(value: PathInput) -> None:
        if store.scene(value.scene_id) is None:
            raise HTTPException(404, "Scene not found")
        try:
            plan(
                Recipe(
                    scene_id=value.scene_id,
                    trajectory=value.trajectory,
                    capture=Capture(duration_seconds=value.duration_seconds),
                    quality=Quality(min_gap_seconds=min(5, value.duration_seconds - 1 / 24)),
                )
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/paths")
    def list_paths(
        scene_id: Annotated[str, Query(min_length=1, max_length=64)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        if store.scene(scene_id) is None:
            raise HTTPException(404, "Scene not found")
        return store.paths(scene_id, limit, offset)

    @app.post("/api/paths", status_code=201)
    def create_path(value: PathInput) -> dict[str, Any]:
        validate_path_design(value)
        try:
            return store.save_path(value)
        except PlannerLimitError as error:
            raise HTTPException(429, str(error)) from error

    def existing_path(path_id: str) -> dict[str, Any]:
        if not JOB_ID.fullmatch(path_id):
            raise HTTPException(404, "Saved path not found")
        saved = store.path(path_id)
        if saved is None:
            raise HTTPException(404, "Saved path not found")
        return saved

    @app.get("/api/paths/{path_id}")
    def get_path(path_id: str) -> dict[str, Any]:
        return existing_path(path_id)

    @app.put("/api/paths/{path_id}")
    def update_path(path_id: str, value: PathUpdate) -> dict[str, Any]:
        existing_path(path_id)
        validate_path_design(value)
        try:
            return store.save_path(value, path_id)
        except RevisionConflict as error:
            raise HTTPException(409, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, "Saved path no longer exists") from error

    @app.delete("/api/paths/{path_id}")
    def delete_path(path_id: str, revision: Annotated[int, Query(ge=1)]) -> dict[str, str]:
        existing_path(path_id)
        try:
            store.delete_path(path_id, revision)
        except RevisionConflict as error:
            raise HTTPException(409, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, "Saved path no longer exists") from error
        return {"status": "deleted"}

    def enqueue(
        recipe: Recipe, parent: str | None = None, key: str | None = None
    ) -> dict[str, Any]:
        scene = store.scene(recipe.scene_id)
        if scene is None:
            raise HTTPException(404, "Scene not found")
        if settings.blender_executable is None:
            raise HTTPException(503, "Blender is unavailable; configure LOCUS_BLENDER")
        try:
            planned = plan(recipe)
            if not planned["valid"]:
                failed = [check["name"] for check in planned["checks"] if not check["passed"]]
                raise ValueError(f"Trajectory gates failed: {', '.join(failed)}")
            return store.enqueue(recipe, scene, parent, key)
        except QueueFullError as error:
            raise HTTPException(429, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/jobs", status_code=202)
    def create_job(
        recipe: Recipe,
        idempotency_key: Annotated[str | None, Header(min_length=8, max_length=128)] = None,
    ) -> dict[str, Any]:
        return enqueue(recipe, key=idempotency_key)

    @app.get("/api/jobs")
    def jobs(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        return store.jobs(limit, offset)

    def existing_job(job_id: str) -> dict[str, Any]:
        if not JOB_ID.fullmatch(job_id):
            raise HTTPException(404, "Job not found")
        job = store.job(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return {**existing_job(job_id), "events": store.events(job_id)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        existing_job(job_id)
        try:
            return store.request_cancel(job_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str) -> dict[str, Any]:
        job = existing_job(job_id)
        if job["state"] not in ("failed", "cancelled", "rejected"):
            raise HTTPException(409, "Only failed, cancelled or rejected jobs can be retried")
        return enqueue(Recipe.model_validate(job["recipe"]), parent=job_id)

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str) -> dict[str, str]:
        existing_job(job_id)
        path = settings.data_dir / "jobs" / job_id / "worker.log"
        if not path.exists():
            return {"log": "The renderer has not written a log yet."}
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 24000))
            text = stream.read().decode("utf-8", errors="replace")
        return {"log": text}

    @app.get("/api/jobs/{job_id}/loops")
    def job_loops(job_id: str) -> dict[str, Any]:
        existing_job(job_id)
        path = settings.data_dir / "jobs" / job_id / "dataset" / "loops.json"
        if not path.is_file():
            raise HTTPException(409, "Loop certification is not finished yet")
        return read_json(path)

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str) -> FileResponse:
        job = existing_job(job_id)
        if job["state"] != "succeeded":
            raise HTTPException(409, "Only completely verified jobs have a published export")
        path = settings.data_dir / "jobs" / job_id / "dataset.zip"
        if not path.is_file():
            raise HTTPException(410, "Published archive is missing; restore it from backup")
        return FileResponse(
            path, media_type="application/zip", filename=f"locusforge-{job_id[:12]}.zip"
        )

    @app.get("/api/jobs/{job_id}/files/{filename:path}")
    def job_file(job_id: str, filename: str) -> FileResponse:
        job = existing_job(job_id)
        if not EXPORT_FILE.fullmatch(filename):
            raise HTTPException(404, "Export file not found")
        if filename.startswith(("rgb/", "depth/")):
            index = int(Path(filename).stem)
            if index >= job["completed_frames"]:
                raise HTTPException(404, "Frame has not been rendered yet")
        root = settings.data_dir / "jobs" / job_id / "dataset"
        path = safe_child(root, filename)
        if not path.is_file():
            raise HTTPException(404, "Export file is not available yet")
        if filename == "video.mp4" and job["state"] != "succeeded":
            raise HTTPException(409, "Video is not verified yet")
        return FileResponse(path)

    @app.get("/{route:path}", response_model=None)
    def studio(route: str) -> FileResponse | JSONResponse:
        if route.startswith("api/"):
            raise HTTPException(404, "API endpoint not found")
        try:
            path = (
                safe_child(settings.static_dir, route)
                if route
                else settings.static_dir / "index.html"
            )
        except ValueError as error:
            raise HTTPException(404, "Page not found") from error
        if path.is_file():
            return FileResponse(path)
        index = settings.static_dir / "index.html"
        if index.is_file() and "." not in Path(route).name:
            return FileResponse(index)
        if not index.is_file():
            return JSONResponse(
                {
                    "detail": "Studio is not built. Run npm ci and npm run build in web, then restart."
                },
                status_code=503,
            )
        raise HTTPException(404, "Resource not found")

    return app
