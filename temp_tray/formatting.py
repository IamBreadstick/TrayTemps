from __future__ import annotations

from typing import Optional


def c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def convert_temp(value_c: Optional[float], unit: str) -> Optional[float]:
    if value_c is None:
        return None
    if unit == "F":
        return c_to_f(value_c)
    return value_c


def temp_number(value_c: Optional[float], unit: str) -> str:
    value = convert_temp(value_c, unit)
    if value is None:
        return "--"
    return str(int(round(value)))


def temp_label(value_c: Optional[float], unit: str) -> str:
    value = convert_temp(value_c, unit)
    if value is None:
        return f"--°{unit}"
    return f"{int(round(value))}°{unit}"
