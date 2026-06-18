from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .models import AppState, DeviceInfo, TemperatureSnapshot
from .storage import app_base_dir

TTGRAPH_EXTENSION = ".ttgraph"
FILE_TYPE_NAME = "TrayTemps.Graph"


def _device_to_dict(device: DeviceInfo) -> dict:
    return {
        "name": device.name,
        "vendor": device.vendor,
        "color": list(device.color),
    }


def _device_from_dict(data: dict | None) -> DeviceInfo:
    if not isinstance(data, dict):
        return DeviceInfo()
    color = data.get("color", [0, 0, 0])
    try:
        color_tuple = (int(color[0]), int(color[1]), int(color[2]))
    except Exception:
        color_tuple = (0, 0, 0)
    return DeviceInfo(
        name=str(data.get("name") or "Unknown"),
        vendor=str(data.get("vendor") or "unknown"),
        color=color_tuple,
    )


def _snapshot_to_dict(snap: TemperatureSnapshot, session_started: datetime) -> dict:
    return {
        "timestamp": snap.timestamp.isoformat(timespec="milliseconds"),
        "elapsed_seconds": round((snap.timestamp - session_started).total_seconds(), 3),
        "cpu_c": snap.cpu_c,
        "gpu_c": snap.gpu_c,
        "cpu_power_w": snap.cpu_power_w,
        "gpu_power_w": snap.gpu_power_w,
        "cpu": _device_to_dict(snap.cpu),
        "gpu": _device_to_dict(snap.gpu),
        "cpu_status": snap.cpu_status,
        "gpu_status": snap.gpu_status,
    }


def _parse_dt(value: str, fallback: datetime) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return fallback


def save_ttgraph(path: Path, history: Iterable[TemperatureSnapshot], session_started: datetime) -> Path:
    rows = list(history)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "TrayTempsGraph",
        "version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session_started": session_started.isoformat(timespec="seconds"),
        "samples": [_snapshot_to_dict(s, session_started) for s in rows],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_ttgraph(path: Path) -> tuple[AppState, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "TrayTempsGraph":
        raise ValueError("Not a TrayTemps graph file")
    now = datetime.now()
    session_started = _parse_dt(str(payload.get("session_started") or ""), now)
    state = AppState()
    state.session_started = session_started
    state.clear_history()
    state.session_started = session_started
    samples = payload.get("samples") or []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        timestamp = _parse_dt(str(sample.get("timestamp") or ""), session_started)
        snap = TemperatureSnapshot(
            timestamp=timestamp,
            cpu_c=_optional_float(sample.get("cpu_c")),
            gpu_c=_optional_float(sample.get("gpu_c")),
            cpu=_device_from_dict(sample.get("cpu")),
            gpu=_device_from_dict(sample.get("gpu")),
            cpu_power_w=_optional_float(sample.get("cpu_power_w")),
            gpu_power_w=_optional_float(sample.get("gpu_power_w")),
            cpu_backend="saved",
            gpu_backend="saved",
            cpu_status=str(sample.get("cpu_status") or "saved"),
            gpu_status=str(sample.get("gpu_status") or "saved"),
        )
        state.add_snapshot(snap)
    return state, payload


def _optional_float(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def graph_open_command() -> Optional[str]:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" "%1"'
    launcher = app_base_dir() / "launcher.py"
    if launcher.exists():
        return f'"{Path(sys.executable).resolve()}" "{launcher}" "%1"'
    return None


def register_ttgraph_file_type() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Graph file registration is only supported on Windows."
    command = graph_open_command()
    if not command:
        return False, "Could not determine how to register TrayTemps graph files."
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.ttgraph") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, FILE_TYPE_NAME)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{FILE_TYPE_NAME}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "TrayTemps graph")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{FILE_TYPE_NAME}\DefaultIcon") as key:
            exe = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(sys.executable).resolve()
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{FILE_TYPE_NAME}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        return True, "TrayTemps graph files are registered."
    except Exception as exc:
        return False, f"Failed to register TrayTemps graph files: {type(exc).__name__}: {exc}"
