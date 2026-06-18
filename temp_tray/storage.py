from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DATA_FOLDER = "TrayTempsData"


def app_base_dir() -> Path:
    """Folder where TrayTemps is running from.

    In a release build this is the extracted TrayTemps folder. In source runs it
    is the project root. User-facing exports stay under this folder so they do
    not clutter the desktop.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    path = app_base_dir() / APP_DATA_FOLDER
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_dir(name: str) -> Path:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in ("_", "-")) or "exports"
    path = data_dir() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_file(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass
