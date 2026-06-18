from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .brands import cpu_color, detect_cpu_vendor, detect_gpu_vendor, gpu_color
from .models import DeviceInfo, TemperatureSnapshot
from .backend import LibreHardwareMonitorBackend, WMI_NAMESPACE
from .coretemp import CoreTempReading, CoreTempSharedMemoryReader
from .storage import export_dir, open_file

try:
    import wmi  # type: ignore
except Exception:  # pragma: no cover - Windows-only dependency
    wmi = None


@dataclass
class HelperReading:
    cpu_name: str = "Unknown"
    gpu_name: str = "Unknown"
    cpu_temp_c: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    cpu_power_w: Optional[float] = None
    gpu_power_w: Optional[float] = None
    cpu_sensor_name: Optional[str] = None
    cpu_sensor_hardware: Optional[str] = None
    gpu_sensor_name: Optional[str] = None
    gpu_sensor_hardware: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None


def app_base_dir() -> Path:
    """Return the directory containing the frozen app or source launcher."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def helper_exe_path() -> Path:
    """Locate the bundled sensor helper in source and PyInstaller builds.

    In source runs, build_sensor_helper.ps1 publishes to:
        <project>/sensor_helper/publish/TempTray.SensorHelper.exe

    In PyInstaller onedir builds, data files are placed under:
        <dist>/TrayTemps/_internal/sensor_helper/TempTray.SensorHelper.exe

    The exact base directory differs between source, frozen executable, and
    PyInstaller runtime extraction, so check all known layouts.
    """
    bases = []
    app_base = app_base_dir()
    bases.append(app_base)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))

    bases.append(app_base / "_internal")

    candidates = []
    for base in bases:
        candidates.extend([
            base / "sensor_helper" / "publish" / "TempTray.SensorHelper.exe",
            base / "sensor_helper" / "TempTray.SensorHelper.exe",
            base / "sensor_helper" / "publish" / "TrayTemps.SensorHelper.exe",
            base / "sensor_helper" / "TrayTemps.SensorHelper.exe",
        ])

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Return the normal source path as a clear default for error/debug behavior.
    return app_base / "sensor_helper" / "publish" / "TempTray.SensorHelper.exe"


@dataclass
class WmiReading:
    cpu_name: str = "Unknown"
    gpu_name: str = "Unknown"
    cpu_temp_c: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    cpu_power_w: Optional[float] = None
    gpu_power_w: Optional[float] = None
    cpu_sensor_name: Optional[str] = None
    gpu_sensor_name: Optional[str] = None
    status: str = "unavailable"


class SensorHelperReader:
    """Reads CPU/GPU temperatures from a bundled .NET helper.

    The helper uses LibreHardwareMonitorLib directly. This avoids the fragile WMI bridge
    and avoids opening the LibreHardwareMonitor GUI.
    """

    def __init__(self) -> None:
        self.exe_path = helper_exe_path()
        self.process: Optional[subprocess.Popen] = None
        self.lock = threading.RLock()
        self.cpu_info = DeviceInfo()
        self.gpu_info = DeviceInfo()
        self.lhm_wmi_backend = LibreHardwareMonitorBackend(startup_timeout_seconds=15.0)
        self.coretemp_reader = CoreTempSharedMemoryReader()
        self._refresh_device_info_from_windows()
        self._start_helper()

    def close(self) -> None:
        with self.lock:
            if self.process is None:
                return
            try:
                if self.process.poll() is None and self.process.stdin:
                    self.process.stdin.write("quit\n")
                    self.process.stdin.flush()
            except Exception:
                pass
            try:
                if self.process.poll() is None:
                    self.process.terminate()
            except Exception:
                pass
            self.process = None


    def export_sensor_debug_dump(self) -> Optional[Path]:
        """Write a full helper sensor dump under TrayTempsData/sensor_dumps."""
        with self.lock:
            if not self.exe_path.exists():
                return None

            # Stop the long-lived helper first so the one-shot dump owns the hardware backend.
            self.close()

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out_path = export_dir("sensor_dumps") / f"TrayTemps-sensor-dump-{stamp}.txt"

            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            header = [
                "TrayTemps sensor debug dump",
                f"Created: {datetime.now().isoformat(timespec='seconds')}",
                f"Helper: {self.exe_path}",
                "",
            ]

            try:
                completed = subprocess.run(
                    [str(self.exe_path)],
                    input="dump\nquit\n",
                    cwd=str(self.exe_path.parent),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=45,
                    startupinfo=startupinfo,
                    creationflags=creationflags,
                )
                body = completed.stdout or ""
                if completed.stderr:
                    body += "\nSTDERR:\n" + completed.stderr
                if not body.strip():
                    body = f"No output from helper. Exit code: {completed.returncode}\n"
                body += self._coretemp_debug_section()
                out_path.write_text("\n".join(header) + body, encoding="utf-8", errors="replace")
                open_file(out_path)
                return out_path
            except Exception as exc:
                try:
                    out_path.write_text("\n".join(header) + f"Failed to create dump: {type(exc).__name__}: {exc}\n", encoding="utf-8", errors="replace")
                    open_file(out_path)
                    return out_path
                except Exception:
                    return None
            finally:
                self._start_helper()

    def read(self) -> TemperatureSnapshot:
        reading = self._read_from_helper()
        cpu_temp: Optional[float] = None
        gpu_temp: Optional[float] = None
        cpu_power: Optional[float] = None
        gpu_power: Optional[float] = None
        cpu_backend = "unavailable"
        gpu_backend = "unavailable"
        cpu_status = "helper-unavailable"
        gpu_status = "helper-unavailable"

        if reading is not None:
            cpu_name = reading.cpu_name or self.cpu_info.name or "Unknown"
            gpu_name = reading.gpu_name or self.gpu_info.name or "Unknown"
            cpu_temp = reading.cpu_temp_c
            gpu_temp = reading.gpu_temp_c
            cpu_power = reading.cpu_power_w
            gpu_power = reading.gpu_power_w
            self._set_device_info(cpu_name, gpu_name)

            helper_status = reading.status or "unknown"
            if cpu_temp is not None:
                cpu_backend = "LHM"
                cpu_status = helper_status
            else:
                cpu_status = helper_status if helper_status else "lhm-cpu-unavailable"

            if gpu_temp is not None:
                gpu_backend = "LHM"
                gpu_status = helper_status
            else:
                gpu_status = helper_status if helper_status else "lhm-gpu-unavailable"
        else:
            # Keep hardware names usable even when the helper is missing/failing.
            self._refresh_device_info_from_windows()

        # If the direct helper cannot get CPU temps, try the WMI bridge exposed by
        # a bundled or already-running LibreHardwareMonitor GUI. This gives us a
        # second backend path for machines where LHM's library sensor objects exist
        # but their direct Value fields stay null/0.
        if cpu_temp is None:
            fallback = self._read_from_lhm_wmi()
            if fallback is not None:
                if fallback.cpu_name != "Unknown" or fallback.gpu_name != "Unknown":
                    self._set_device_info(
                        fallback.cpu_name if fallback.cpu_name != "Unknown" else self.cpu_info.name,
                        fallback.gpu_name if fallback.gpu_name != "Unknown" else self.gpu_info.name,
                    )
                if fallback.cpu_temp_c is not None:
                    cpu_temp = fallback.cpu_temp_c
                    cpu_backend = "LHM WMI"
                    cpu_status = fallback.status
                if gpu_temp is None and fallback.gpu_temp_c is not None:
                    gpu_temp = fallback.gpu_temp_c
                    gpu_backend = "LHM WMI"
                    gpu_status = fallback.status

        # Final CPU-only fallback: Core Temp shared memory. This requires Core Temp
        # to be installed/running with shared memory enabled. It is intentionally
        # optional and never replaces LHM when LHM already has a valid CPU temp.
        if cpu_temp is None:
            coretemp = self._read_from_coretemp()
            if coretemp is not None:
                self._set_device_info(
                    coretemp.cpu_name if coretemp.cpu_name != "Unknown" else self.cpu_info.name,
                    self.gpu_info.name,
                )
                cpu_temp = coretemp.cpu_temp_c
                cpu_backend = "Core Temp"
                cpu_status = "ok"
            else:
                cpu_status = "lhm-cpu-unavailable-coretemp-not-running"

        return TemperatureSnapshot(
            timestamp=datetime.now(),
            cpu_c=cpu_temp,
            gpu_c=gpu_temp,
            cpu=self.cpu_info,
            gpu=self.gpu_info,
            cpu_power_w=cpu_power,
            gpu_power_w=gpu_power,
            cpu_backend=cpu_backend,
            gpu_backend=gpu_backend,
            cpu_status=cpu_status,
            gpu_status=gpu_status,
        )




    def _read_from_coretemp(self) -> Optional[CoreTempReading]:
        try:
            reading = self.coretemp_reader.read()
            if reading.status == "ok" and reading.cpu_temp_c is not None:
                return reading
        except Exception:
            return None
        return None

    def _coretemp_debug_section(self) -> str:
        try:
            reading = self.coretemp_reader.read()
            cpu_name = reading.cpu_name if reading.cpu_name and reading.cpu_name != "Unknown" else (self.cpu_info.name or "Unknown")
            lines = [
                "",
                "Core Temp fallback",
                f"CORETEMP|status|{reading.status}",
                f"CORETEMP|cpu_name|{reading.cpu_name}",
                f"CORETEMP|sensor|{reading.sensor_name}",
                f"CORETEMP|cpu_temp_c|{reading.cpu_temp_c if reading.cpu_temp_c is not None else 'null'}",
                f"CORETEMP|core_count|{reading.core_count}",
                f"CORETEMP|cpu_count|{reading.cpu_count}",
                f"CORETEMP|error|{reading.error or ''}",
                "",
                "TrayTemps effective backend selection",
            ]
            if reading.status == "ok" and reading.cpu_temp_c is not None:
                lines.append(
                    f"EFFECTIVE_SELECTED|CPU|{cpu_name}|Core Temp|{reading.sensor_name}|{reading.cpu_temp_c}|ok"
                )
            else:
                lines.append(
                    f"EFFECTIVE_SELECTED|CPU|{cpu_name}|none|none|null|lhm-cpu-unavailable-coretemp-not-running"
                )
            return "\n".join(lines) + "\n"
        except Exception as exc:
            return f"\nCore Temp fallback\nCORETEMP|status|error\nCORETEMP|error|{type(exc).__name__}: {exc}\n"


    def _read_from_lhm_wmi(self) -> Optional[WmiReading]:
        if wmi is None:
            return None
        try:
            if not self.lhm_wmi_backend.ensure_running():
                return None
            conn = wmi.WMI(namespace=WMI_NAMESPACE)
            sensors = list(conn.Sensor())
            hardware = {str(getattr(h, "Identifier", "")): str(getattr(h, "Name", "") or "") for h in getattr(conn, "Hardware")()}
        except Exception:
            return None

        cpu_choice = self._select_wmi_temp(sensors, hardware, component="cpu")
        gpu_choice = self._select_wmi_temp(sensors, hardware, component="gpu")

        if cpu_choice is None and gpu_choice is None:
            return None

        cpu_name = cpu_choice[2] if cpu_choice is not None else self.cpu_info.name
        gpu_name = gpu_choice[2] if gpu_choice is not None else self.gpu_info.name
        return WmiReading(
            cpu_name=cpu_name or "Unknown",
            gpu_name=gpu_name or "Unknown",
            cpu_temp_c=cpu_choice[0] if cpu_choice is not None else None,
            gpu_temp_c=gpu_choice[0] if gpu_choice is not None else None,
            cpu_sensor_name=cpu_choice[1] if cpu_choice is not None else None,
            gpu_sensor_name=gpu_choice[1] if gpu_choice is not None else None,
            status="lhm-wmi",
        )

    def _select_wmi_temp(self, sensors, hardware: dict[str, str], component: str):
        choices = []
        for sensor in sensors:
            try:
                if str(getattr(sensor, "SensorType", "")).lower() != "temperature":
                    continue
                value = self._as_float(getattr(sensor, "Value", None))
                if value is None:
                    continue
                if component == "cpu" and not (5.0 < value < 125.0):
                    continue
                if component == "gpu" and not (5.0 < value < 130.0):
                    continue

                name = str(getattr(sensor, "Name", "") or "")
                parent = str(getattr(sensor, "Parent", "") or "")
                identifier = str(getattr(sensor, "Identifier", "") or "")
                text = f"{name} {parent} {identifier}".lower()
                hw_name = hardware.get(parent, "") or self._hardware_name_from_parent(parent, hardware)

                score = 0
                if component == "cpu":
                    if "cpu" in parent.lower() or "intelcpu" in parent.lower() or "amdcpu" in parent.lower():
                        score += 80
                    if "cpu package" in text:
                        score += 90
                    elif "core max" in text:
                        score += 80
                    elif "core average" in text:
                        score += 75
                    elif "tctl/tdie" in text or "tctl" in text or "tdie" in text:
                        score += 75
                    elif "ccd" in text:
                        score += 55
                    elif "p-core" in text or "e-core" in text or "core #" in text:
                        score += 45
                    elif "cpu" in text:
                        score += 40
                    elif any(x in parent.lower() for x in ("lpc", "superio", "ec", "motherboard")) and "gpu" not in text:
                        # Motherboard fallback. This is intentionally lower priority because
                        # unlabeled board temps can be chipset/VRM/ambient rather than CPU.
                        score += 12
                    if "distance to tjmax" in text or "distance to t-jmax" in text:
                        score = 0
                else:
                    if "gpu" in parent.lower() or "nvidia" in text or "radeon" in text or "geforce" in text:
                        score += 70
                    if "gpu core" in text:
                        score += 90
                    elif "core" in text:
                        score += 70
                    elif "hot spot" in text or "hotspot" in text:
                        score += 45
                    elif "memory junction" in text:
                        score += 35

                if score > 0:
                    choices.append((score, value, name, hw_name or "Unknown"))
            except Exception:
                continue

        if not choices:
            return None
        choices.sort(key=lambda x: (x[0], x[1]), reverse=True)
        _, value, sensor_name, hw_name = choices[0]
        return value, sensor_name, hw_name

    @staticmethod
    def _hardware_name_from_parent(parent: str, hardware: dict[str, str]) -> str:
        if not parent:
            return ""
        # Some WMI providers return parent paths that include a child suffix. Walk
        # upward until a Hardware.Identifier match is found.
        current = parent
        while current:
            if current in hardware:
                return hardware[current]
            if "/" not in current.strip("/"):
                break
            current = current.rsplit("/", 1)[0]
        return ""

    def _start_helper(self) -> None:
        if not self.exe_path.exists():
            return
        if self.process is not None and self.process.poll() is None:
            return

        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self.process = subprocess.Popen(
                [str(self.exe_path)],
                cwd=str(self.exe_path.parent),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except Exception:
            self.process = None

    def _read_from_helper(self) -> Optional[HelperReading]:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self._start_helper()
            if self.process is None or self.process.stdin is None or self.process.stdout is None:
                return None
            try:
                self.process.stdin.write("read\n")
                self.process.stdin.flush()
                line = self.process.stdout.readline()
                if not line:
                    return None
                data = json.loads(line)
                return HelperReading(
                    cpu_name=str(data.get("cpu_name") or "Unknown"),
                    gpu_name=str(data.get("gpu_name") or "Unknown"),
                    cpu_temp_c=self._as_float(data.get("cpu_temp_c")),
                    gpu_temp_c=self._as_float(data.get("gpu_temp_c")),
                    cpu_power_w=self._as_float(data.get("cpu_power_w")),
                    gpu_power_w=self._as_float(data.get("gpu_power_w")),
                    cpu_sensor_name=data.get("cpu_sensor_name"),
                    cpu_sensor_hardware=data.get("cpu_sensor_hardware"),
                    gpu_sensor_name=data.get("gpu_sensor_name"),
                    gpu_sensor_hardware=data.get("gpu_sensor_hardware"),
                    status=data.get("status"),
                    error=data.get("error"),
                )
            except Exception:
                try:
                    if self.process.poll() is None:
                        self.process.kill()
                except Exception:
                    pass
                self.process = None
                return None

    @staticmethod
    def _as_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _set_device_info(self, cpu_name: str, gpu_name: str) -> None:
        cpu_vendor = detect_cpu_vendor(cpu_name)
        gpu_vendor = detect_gpu_vendor(gpu_name)
        self.cpu_info = DeviceInfo(cpu_name, cpu_vendor, cpu_color(cpu_vendor))
        self.gpu_info = DeviceInfo(gpu_name, gpu_vendor, gpu_color(gpu_vendor))

    def _refresh_device_info_from_windows(self) -> None:
        cpu_name = self._windows_cpu_name() or self.cpu_info.name or "Unknown"
        gpu_name = self._windows_gpu_name() or self.gpu_info.name or "Unknown"
        self._set_device_info(cpu_name, gpu_name)

    def _windows_cpu_name(self) -> Optional[str]:
        if wmi is None:
            return None
        try:
            cim = wmi.WMI(namespace="root\\cimv2")
            cpus = cim.Win32_Processor()
            if cpus:
                return str(cpus[0].Name).strip()
        except Exception:
            return None
        return None

    def _windows_gpu_name(self) -> Optional[str]:
        if wmi is None:
            return None
        try:
            cim = wmi.WMI(namespace="root\\cimv2")
            gpus = cim.Win32_VideoController()
            names = [str(g.Name).strip() for g in gpus if getattr(g, "Name", None)]
            for name in names:
                if detect_gpu_vendor(name) != "unknown":
                    return name
            return names[0] if names else None
        except Exception:
            return None


# Backwards-compatible name used by main.py.
LibreHardwareMonitorReader = SensorHelperReader
