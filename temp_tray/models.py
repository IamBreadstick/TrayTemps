from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import List, Optional, Tuple

Color = Tuple[int, int, int]


@dataclass
class DeviceInfo:
    name: str = "Unknown"
    vendor: str = "unknown"
    color: Color = (0, 0, 0)


@dataclass
class TemperatureSnapshot:
    timestamp: datetime
    cpu_c: Optional[float]
    gpu_c: Optional[float]
    cpu: DeviceInfo
    gpu: DeviceInfo
    cpu_power_w: Optional[float] = None
    gpu_power_w: Optional[float] = None
    cpu_backend: str = "unavailable"
    gpu_backend: str = "unavailable"
    cpu_status: str = "unknown"
    gpu_status: str = "unknown"


class AppState:
    def __init__(self) -> None:
        now = datetime.now()
        self.session_started = now
        self.latest = TemperatureSnapshot(
            timestamp=now,
            cpu_c=None,
            gpu_c=None,
            cpu=DeviceInfo(),
            gpu=DeviceInfo(),
        )
        self.history: List[TemperatureSnapshot] = []
        self.lock = RLock()

    def add_snapshot(self, snapshot: TemperatureSnapshot) -> None:
        with self.lock:
            self.latest = snapshot
            self.history.append(snapshot)

    def get_latest(self) -> TemperatureSnapshot:
        with self.lock:
            return self.latest

    def get_history(self) -> List[TemperatureSnapshot]:
        with self.lock:
            return list(self.history)

    def clear_history(self) -> None:
        with self.lock:
            self.history.clear()
            self.session_started = datetime.now()
