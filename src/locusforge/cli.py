from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .assets import builtin_records
from .config import PACKAGE_DIR, Settings
from .export import verify_export
from .geometry import plan
from .io import atomic_json, read_json
from .models import Recipe
from .store import TERMINAL_STATES, Store


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="locusforge", description="Geometry-certified revisit data studio"
    )
    parser.add_argument("--data-dir", type=Path, help="Workspace data directory (default: ./data)")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve the studio and API")
    serve.add_argument("--host", default=os.environ.get("LOCUS_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("LOCUS_PORT", "8765")))
    commands.add_parser("worker", help="Run a standalone, single-owner render coordinator")
    commands.add_parser("init", help="Register the bundled original GLB scenes")
    commands.add_parser(
        "seed-assets", help="Rebuild the original assets and thumbnails with Blender"
    )
    plan_parser = commands.add_parser("plan", help="Validate a recipe without rendering")
    plan_parser.add_argument("recipe", type=Path)
    plan_parser.add_argument("--output", type=Path)
    capture = commands.add_parser(
        "capture", help="Enqueue a recipe; optionally run a worker until complete"
    )
    capture.add_argument("recipe", type=Path)
    capture.add_argument("--wait", action="store_true")
    verify = commands.add_parser(
        "verify", help="Verify every checksum in an exported directory or ZIP"
    )
    verify.add_argument("export", type=Path)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if args.data_dir:
        os.environ["LOCUS_DATA_DIR"] = str(args.data_dir.resolve())
    settings = Settings()
    if args.command == "serve":
        if args.host not in ("127.0.0.1", "localhost", "::1") and not settings.api_token:
            parser.error("Non-loopback binds require LOCUS_API_TOKEN; deploy behind TLS")
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1)
    elif args.command == "worker":
        from .worker import run_worker

        run_worker(settings)
    elif args.command == "seed-assets":
        executable = settings.blender_executable
        if executable is None:
            parser.error("Blender not found; set LOCUS_BLENDER")
        subprocess.run(
            [
                executable,
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--threads",
                str(settings.render_threads),
                "--python-exit-code",
                "1",
                "--python",
                str(PACKAGE_DIR / "blender" / "seed_scenes.py"),
                "--",
                str(PACKAGE_DIR / "assets"),
            ],
            check=True,
        )
    elif args.command == "init":
        store = Store(settings)
        records = builtin_records(settings)
        if not records:
            parser.error("Bundled assets are missing; run locusforge seed-assets")
        for record in records:
            store.add_scene(record)
        print(json.dumps({"scenes": [record["id"] for record in store.scenes()]}, indent=2))
    elif args.command == "plan":
        result = plan(Recipe.model_validate(read_json(args.recipe)))
        if args.output:
            atomic_json(args.output, result)
        print(json.dumps({key: result[key] for key in ("stats", "checks", "valid")}, indent=2))
        if not result["valid"]:
            sys.exit(2)
    elif args.command == "capture":
        store = Store(settings)
        for record in builtin_records(settings):
            store.add_scene(record)
        recipe = Recipe.model_validate(read_json(args.recipe))
        scene = store.scene(recipe.scene_id)
        if scene is None:
            parser.error("Scene not found; ingest it using the studio first")
        if not plan(recipe)["valid"]:
            parser.error("Trajectory does not pass kinematic gates; run locusforge plan")
        if settings.blender_executable is None:
            parser.error("Blender not found; set LOCUS_BLENDER")
        job = store.enqueue(recipe, scene)
        print(json.dumps({"id": job["id"], "state": job["state"]}), flush=True)
        if args.wait:
            from .worker import Worker

            worker = Worker(settings, store)
            worker.start()
            try:
                while True:
                    current = store.job(job["id"])
                    if current and current["state"] in TERMINAL_STATES:
                        print(json.dumps(current, ensure_ascii=False, indent=2))
                        if current["state"] != "succeeded":
                            sys.exit(1)
                        break
                    time.sleep(0.5)
            finally:
                worker.stop()
    elif args.command == "verify":
        print(json.dumps(verify_export(args.export), indent=2))


if __name__ == "__main__":
    main()
