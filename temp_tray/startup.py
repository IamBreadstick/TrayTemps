from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    from win32com.client import Dispatch  # type: ignore
except Exception:  # pragma: no cover - Windows-only dependency
    Dispatch = None

APP_NAME = "TrayTemps"
TASK_NAME = "TrayTemps"
STARTUP_ARG = "--startup"


def startup_shortcut_path() -> Path:
    startup = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / f"{APP_NAME}.lnk"


def executable_path() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    return str(Path(sys.executable).resolve())


def working_directory() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parents[1])


def script_args(include_startup: bool = True) -> str:
    args: list[str] = []
    if not getattr(sys, "frozen", False):
        launcher = Path(__file__).resolve().parents[1] / "launcher.py"
        args.append(f'"{launcher}"')
    if include_startup:
        args.append(STARTUP_ARG)
    return " ".join(args)


def launch_command() -> str:
    args = script_args(include_startup=True)
    exe = executable_path()
    if args:
        return f'"{exe}" {args}'
    return f'"{exe}" {STARTUP_ARG}'


def is_startup_launch(argv: list[str] | None = None) -> bool:
    argv = sys.argv if argv is None else argv
    return any(arg.lower() == STARTUP_ARG for arg in argv[1:])


def is_enabled() -> bool:
    # If either the scheduled task or the old shortcut exists, show the menu item as
    # enabled so one click can remove/repair stale startup registrations.
    return _scheduled_task_exists() or startup_shortcut_path().exists()


def set_enabled(enabled: bool) -> bool:
    if enabled:
        # Prefer a scheduled task. TrayTemps is built with an admin manifest for the
        # best hardware access. Windows Startup-folder shortcuts are unreliable for
        # elevated apps; a logon task with highest privileges is the normal fix.
        _delete_startup_shortcut()
        if _create_scheduled_task(highest=True):
            return True
        if _create_scheduled_task(highest=False):
            return True
        return _create_shortcut(startup_shortcut_path())

    ok_task = _delete_scheduled_task()
    ok_shortcut = _delete_startup_shortcut()
    return ok_task and ok_shortcut


def _scheduled_task_exists() -> bool:
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:
        return False


def _create_scheduled_task(highest: bool) -> bool:
    cmd = [
        "schtasks",
        "/Create",
        "/TN", TASK_NAME,
        "/SC", "ONLOGON",
        "/TR", launch_command(),
        "/F",
    ]
    if highest:
        cmd.extend(["/RL", "HIGHEST"])
    else:
        cmd.extend(["/RL", "LIMITED"])
    # Delay slightly so Explorer/tray and hardware services are ready after logon.
    cmd.extend(["/DELAY", "0000:15"])

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:
        return False


def _delete_scheduled_task() -> bool:
    try:
        if not _scheduled_task_exists():
            return True
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:
        return False


def _delete_startup_shortcut() -> bool:
    path = startup_shortcut_path()
    try:
        if path.exists():
            path.unlink()
        return True
    except Exception:
        return False


def _create_shortcut(path: Path) -> bool:
    if Dispatch is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(path))
        shortcut.Targetpath = executable_path()
        shortcut.Arguments = script_args(include_startup=True)
        shortcut.WorkingDirectory = working_directory()
        shortcut.IconLocation = executable_path()
        shortcut.save()
        return True
    except Exception:
        return False
