from __future__ import annotations

from typing import Tuple

Color = Tuple[int, int, int]

CPU_COLORS = {
    "amd": (255, 140, 0),      # orange
    "intel": (0, 120, 255),    # blue
    "unknown": (0, 0, 0),
}

GPU_COLORS = {
    "nvidia": (0, 180, 0),     # green
    "amd": (220, 0, 0),        # red
    "intel": (0, 120, 255),    # blue
    "unknown": (0, 0, 0),
}


def detect_cpu_vendor(name: str) -> str:
    n = (name or "").lower()
    if "amd" in n or "ryzen" in n or "threadripper" in n or "epyc" in n:
        return "amd"
    if "intel" in n or "core(tm)" in n or "core ultra" in n or "xeon" in n:
        return "intel"
    return "unknown"


def detect_gpu_vendor(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("nvidia", "geforce", "rtx", "gtx", "quadro")):
        return "nvidia"
    if any(k in n for k in ("amd", "radeon", "ati", "vega")):
        return "amd"
    if any(k in n for k in ("intel", "arc", "iris", "uhd graphics", "hd graphics")):
        return "intel"
    return "unknown"


def cpu_color(vendor: str) -> Color:
    return CPU_COLORS.get(vendor, CPU_COLORS["unknown"])


def gpu_color(vendor: str) -> Color:
    return GPU_COLORS.get(vendor, GPU_COLORS["unknown"])
