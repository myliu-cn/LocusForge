"""Install the calibrated official Blender build inside the active Conda prefix."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "4.5.3"
CHECKSUM = "975c58fcb244273838534bba771e64ad87739216b0f9b39a888531a49a72d845"
NAME = f"blender-{VERSION}-linux-x64"
URL = f"https://download.blender.org/release/Blender4.5/{NAME}.tar.xz"
MARKER = "# LocusForge managed Blender launcher"


def main() -> None:
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise SystemExit(
            "Automated setup supports Linux x86_64 / WSL. Install Blender 4.5 LTS manually on other platforms."
        )
    prefix = Path(sys.prefix).resolve()
    if not (prefix / "conda-meta").is_dir():
        raise SystemExit("Activate the locusforge Conda environment before running this installer.")
    target = prefix / "opt" / NAME
    launcher = prefix / "bin" / "blender"
    if launcher.exists() and MARKER not in launcher.read_text(errors="replace")[:1024]:
        raise SystemExit(
            "An unmanaged Blender launcher already exists in this Conda environment; it was not overwritten."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (target / "blender").is_file():
        with tempfile.TemporaryDirectory(prefix="locus-blender-", dir=target.parent) as temporary:
            stage = Path(temporary)
            archive = stage / "blender.tar.xz"
            print(f"Downloading official Blender {VERSION} (one time)...", flush=True)
            request = urllib.request.Request(
                URL, headers={"User-Agent": "LocusForge/0.2 Blender installer"}
            )
            for attempt in range(3):
                try:
                    with (
                        urllib.request.urlopen(request, timeout=120) as response,
                        archive.open("wb") as output,
                    ):
                        shutil.copyfileobj(response, output, length=1024 * 1024)
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as error:
                    status = (
                        error.code
                        if isinstance(error, urllib.error.HTTPError)
                        else type(error).__name__
                    )
                    if attempt == 2:
                        raise SystemExit(
                            f"Blender download failed ({status}); rerun setup to retry."
                        ) from None
                    print(f"Download attempt failed ({status}); retrying...", flush=True)
                    time.sleep(2**attempt)
            with archive.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if digest != CHECKSUM:
                raise SystemExit("Blender checksum mismatch. Nothing was installed.")
            with tarfile.open(archive) as bundle:
                bundle.extractall(stage, filter="data")
            os.replace(stage / NAME, target)
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        f"{MARKER}\n"
        "set -euo pipefail\n"
        'PREFIX="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"\n'
        'export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        f'exec "$PREFIX/opt/{NAME}/blender" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    launcher.chmod(0o755)
    subprocess.run(
        [
            str(launcher),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python-expr",
            "import bpy; assert bpy.app.version[:2] == (4, 5); print('LocusForge Blender runtime verified')",
        ],
        check=True,
        timeout=90,
    )
    print("Conda-local Blender is ready. No other project's environment is required.")


if __name__ == "__main__":
    main()
