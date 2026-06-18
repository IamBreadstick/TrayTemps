from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class CoreTempReading:
    cpu_name: str = "Unknown"
    cpu_temp_c: Optional[float] = None
    sensor_name: str = "Core Temp shared memory"
    status: str = "unavailable"
    error: Optional[str] = None
    core_count: int = 0
    cpu_count: int = 0


class _CoreTempSharedDataEx(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("uiLoad", ctypes.c_uint32 * 256),
        ("uiTjMax", ctypes.c_uint32 * 128),
        ("uiCoreCnt", ctypes.c_uint32),
        ("uiCPUCnt", ctypes.c_uint32),
        ("fTemp", ctypes.c_float * 256),
        ("fVID", ctypes.c_float),
        ("fCPUSpeed", ctypes.c_float),
        ("fFSBSpeed", ctypes.c_float),
        ("fMultiplier", ctypes.c_float),
        ("sCPUName", ctypes.c_char * 100),
        ("ucFahrenheit", ctypes.c_ubyte),
        ("ucDeltaToTjMax", ctypes.c_ubyte),
        ("ucTdpSupported", ctypes.c_ubyte),
        ("ucPowerSupported", ctypes.c_ubyte),
        ("uiStructVersion", ctypes.c_uint32),
        ("uiTdp", ctypes.c_uint32 * 128),
        ("fPower", ctypes.c_float * 128),
        ("fMultipliers", ctypes.c_float * 256),
    ]


class _CoreTempSharedDataOld(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("uiLoad", ctypes.c_uint32 * 256),
        ("uiTjMax", ctypes.c_uint32 * 128),
        ("uiCoreCnt", ctypes.c_uint32),
        ("uiCPUCnt", ctypes.c_uint32),
        ("fTemp", ctypes.c_float * 256),
        ("fVID", ctypes.c_float),
        ("fCPUSpeed", ctypes.c_float),
        ("fFSBSpeed", ctypes.c_float),
        ("fMultiplier", ctypes.c_float),
        ("sCPUName", ctypes.c_char * 100),
        ("ucFahrenheit", ctypes.c_ubyte),
        ("ucDeltaToTjMax", ctypes.c_ubyte),
    ]


class CoreTempSharedMemoryReader:
    """Optional CPU fallback using Core Temp's shared memory block.

    This backend does not bundle or start Core Temp. It only reads the shared
    memory area if the user already has Core Temp running with shared memory
    enabled. That keeps TrayTemps independent while giving a fallback for PCs
    where LibreHardwareMonitorLib returns null/0 CPU temperatures.
    """

    FILE_MAP_READ = 0x0004
    _MAPPINGS = (
        ("CoreTempMappingObjectEx", _CoreTempSharedDataEx),
        ("Global\\CoreTempMappingObjectEx", _CoreTempSharedDataEx),
        ("CoreTempMappingObject", _CoreTempSharedDataOld),
        ("Global\\CoreTempMappingObject", _CoreTempSharedDataOld),
    )

    def __init__(self) -> None:
        self.available = os.name == "nt"
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if self.available else None
        if self._kernel32 is not None:
            self._kernel32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
            self._kernel32.OpenFileMappingW.restype = ctypes.c_void_p
            self._kernel32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
            self._kernel32.MapViewOfFile.restype = ctypes.c_void_p
            self._kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
            self._kernel32.UnmapViewOfFile.restype = ctypes.c_int
            self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            self._kernel32.CloseHandle.restype = ctypes.c_int

    def read(self) -> CoreTempReading:
        if not self.available or self._kernel32 is None:
            return CoreTempReading(status="unsupported-os")

        errors: list[str] = []
        for mapping_name, struct_type in self._MAPPINGS:
            reading = self._read_mapping(mapping_name, struct_type)
            if reading.status == "ok":
                return reading
            if reading.error:
                errors.append(f"{mapping_name}: {reading.error}")

        error = "; ".join(errors) if errors else "Core Temp shared memory not found"
        return CoreTempReading(status="not-running-or-shared-memory-disabled", error=error)

    def _read_mapping(self, mapping_name: str, struct_type: type[ctypes.Structure]) -> CoreTempReading:
        handle = self._kernel32.OpenFileMappingW(self.FILE_MAP_READ, False, mapping_name)
        if not handle:
            err = ctypes.get_last_error()
            return CoreTempReading(status="mapping-open-failed", error=f"OpenFileMapping failed: {err}")

        view = None
        try:
            size = ctypes.sizeof(struct_type)
            view = self._kernel32.MapViewOfFile(handle, self.FILE_MAP_READ, 0, 0, size)
            if not view:
                err = ctypes.get_last_error()
                return CoreTempReading(status="mapping-view-failed", error=f"MapViewOfFile failed: {err}")

            data = struct_type()
            ctypes.memmove(ctypes.byref(data), view, size)
            return self._convert(data, mapping_name)
        finally:
            if view:
                self._kernel32.UnmapViewOfFile(view)
            self._kernel32.CloseHandle(handle)

    def _convert(self, data: ctypes.Structure, mapping_name: str) -> CoreTempReading:
        try:
            core_count = int(getattr(data, "uiCoreCnt", 0) or 0)
            cpu_count = int(getattr(data, "uiCPUCnt", 0) or 0)
            sample_count = core_count * max(cpu_count, 1)
            if sample_count <= 0 or sample_count > 256:
                sample_count = min(max(core_count, 1), 256)

            temps: list[float] = []
            for i in range(sample_count):
                raw = float(data.fTemp[i])
                if raw <= -1000 or raw >= 1000:
                    continue

                value = raw
                if int(getattr(data, "ucDeltaToTjMax", 0) or 0):
                    tjmax = float(data.uiTjMax[i] if i < 128 and data.uiTjMax[i] else data.uiTjMax[0])
                    if tjmax > 0:
                        value = tjmax - raw
                if int(getattr(data, "ucFahrenheit", 0) or 0):
                    value = (value - 32.0) * 5.0 / 9.0
                if 5.0 < value < 125.0:
                    temps.append(value)

            if not temps:
                return CoreTempReading(status="no-valid-temperature", error=f"{mapping_name} had no plausible CPU temps", core_count=core_count, cpu_count=cpu_count)

            name = bytes(data.sCPUName).split(b"\x00", 1)[0].decode("mbcs", errors="replace").strip() or "Unknown"
            return CoreTempReading(
                cpu_name=name,
                cpu_temp_c=max(temps),
                sensor_name=f"Core Temp shared memory ({mapping_name})",
                status="ok",
                core_count=core_count,
                cpu_count=cpu_count,
            )
        except Exception as exc:
            return CoreTempReading(status="parse-error", error=f"{type(exc).__name__}: {exc}")
