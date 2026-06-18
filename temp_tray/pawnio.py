from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

APP_NAME = "TrayTemps"
PAWNIO_SERVICES = ("PawnIO", "PawnIo", "PawnIODriver")
PAWNIO_DRIVER_FILES = ("PawnIO.sys", "PawnIo.sys")
PAWNIO_RELEASES_URL = "https://github.com/namazso/PawnIO.Setup/releases/latest"
PAWNIO_SITE_URL = "https://pawnio.eu/"


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _system32_drivers_dir() -> Path:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    return Path(windir) / "System32" / "drivers"


def _service_exists(name: str) -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg  # type: ignore
        key_path = rf"SYSTEM\CurrentControlSet\Services\{name}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path):
            return True
    except Exception:
        return False


def is_pawnio_installed() -> bool:
    if any(_service_exists(name) for name in PAWNIO_SERVICES):
        return True
    drivers = _system32_drivers_dir()
    return any((drivers / name).exists() for name in PAWNIO_DRIVER_FILES)


def pawnio_status_text() -> str:
    services = ",".join(f"{name}={'present' if _service_exists(name) else 'missing'}" for name in PAWNIO_SERVICES)
    drivers_dir = _system32_drivers_dir()
    drivers = ",".join(f"{name}={'present' if (drivers_dir / name).exists() else 'missing'}" for name in PAWNIO_DRIVER_FILES)
    return f"services:{services}; drivers:{drivers}"


def bundled_installer_path() -> Optional[Path]:
    base = app_base_dir() / "third_party" / "PawnIO"
    if not base.exists():
        return None
    candidates = []
    for pattern in ("*PawnIO*.exe", "*pawnio*.exe", "*.msi"):
        candidates.extend(base.glob(pattern))
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.suffix.lower() != ".exe", p.name.lower()))
    return candidates[0]


def open_pawnio_download_page() -> None:
    # Prefer the official PawnIO website so users can choose the current installer/download.
    try:
        webbrowser.open(PAWNIO_SITE_URL)
    except Exception:
        webbrowser.open(PAWNIO_RELEASES_URL)


def install_pawnio_interactive() -> tuple[bool, str]:
    """Launch bundled PawnIO installer if present, else open official download page.

    Returns (started, message). This never performs a silent install.
    """
    installer = bundled_installer_path()
    if installer and installer.exists():
        if os.name == "nt":
            try:
                verb = "runas"
                rc = ctypes.windll.shell32.ShellExecuteW(None, verb, str(installer), None, str(installer.parent), 1)
                if int(rc) > 32:
                    return True, f"Started bundled PawnIO installer: {installer}"
                return False, f"Windows refused to start installer. ShellExecute return code: {rc}"
            except Exception as exc:
                return False, f"Failed to start bundled PawnIO installer: {type(exc).__name__}: {exc}"
        try:
            subprocess.Popen([str(installer)], cwd=str(installer.parent))
            return True, f"Started bundled PawnIO installer: {installer}"
        except Exception as exc:
            return False, f"Failed to start bundled PawnIO installer: {type(exc).__name__}: {exc}"

    open_pawnio_download_page()
    return True, "Opened the PawnIO download website."


@dataclass(frozen=True)
class PawnIoNeed:
    needed: bool
    reason: str


def needs_pawnio_for_cpu(cpu_temp_c: Optional[float], cpu_status: str, cpu_backend: str) -> PawnIoNeed:
    if is_pawnio_installed():
        return PawnIoNeed(False, "pawnio-installed")
    if cpu_temp_c is not None:
        return PawnIoNeed(False, "cpu-temperature-available")
    status = (cpu_status or "").lower()
    backend = (cpu_backend or "").lower()
    if "lhm-cpu-unavailable" in status or "cpu-unavailable" in status or backend in ("unavailable", "no backend"):
        return PawnIoNeed(True, "lhm-cpu-unavailable-and-pawnio-missing")
    return PawnIoNeed(False, "not-needed")
