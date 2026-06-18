from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import wmi  # type: ignore
except Exception:  # pragma: no cover - Windows-only dependency
    wmi = None

APP_NAME = "TrayTemps"
LHM_EXE = "LibreHardwareMonitor.exe"
WMI_NAMESPACE = "root\\LibreHardwareMonitor"


def app_base_dir() -> Path:
    """Return the directory containing the frozen app or source launcher."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_backend_dir() -> Path:
    return app_base_dir() / "backend" / "LibreHardwareMonitor"


def bundled_backend_exe() -> Path:
    return bundled_backend_dir() / LHM_EXE


class LibreHardwareMonitorBackend:
    """Starts the bundled LibreHardwareMonitor backend if it is not already exposing WMI."""

    def __init__(self, startup_timeout_seconds: float = 12.0) -> None:
        self.startup_timeout_seconds = startup_timeout_seconds
        self.process: Optional[subprocess.Popen] = None
        self.exe_path = bundled_backend_exe()

    def ensure_running(self) -> bool:
        if self.wmi_available():
            return True
        if not self.exe_path.exists():
            return False
        self._launch_backend()
        return self.wait_for_wmi(self.startup_timeout_seconds)

    @staticmethod
    def wmi_available() -> bool:
        if wmi is None:
            return False
        try:
            conn = wmi.WMI(namespace=WMI_NAMESPACE)
            conn.Sensor()
            return True
        except Exception:
            return False

    @staticmethod
    def wait_for_wmi(timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if LibreHardwareMonitorBackend.wmi_available():
                return True
            time.sleep(0.5)
        return False

    def _launch_backend(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            # SW_MINIMIZE = 6. This is more reliable for GUI apps than CREATE_NO_WINDOW.
            startupinfo.wShowWindow = 6
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            self.process = subprocess.Popen(
                [str(self.exe_path)],
                cwd=str(self.exe_path.parent),
                startupinfo=startupinfo,
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except Exception:
            self.process = None

    def stop_if_started_by_temptray(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except Exception:
                    self.process.kill()
        except Exception:
            pass
