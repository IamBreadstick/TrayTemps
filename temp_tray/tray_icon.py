from __future__ import annotations

from typing import Literal, Tuple

from PIL import Image, ImageDraw, ImageFont

from .formatting import temp_number
from .models import TemperatureSnapshot

Color = Tuple[int, int, int]
Component = Literal["cpu", "gpu"]


def _load_font(size: int):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/seguisb.ttf",
        "arialbd.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_text_with_outline(draw: ImageDraw.ImageDraw, xy, text: str, font, fill: Color) -> None:
    # Strong outline improves readability on both dark and light taskbars.
    x, y = xy
    outline = (0, 0, 0)
    for dx, dy in (
        (-2, 0), (2, 0), (0, -2), (0, 2),
        (-1, -1), (1, -1), (-1, 1), (1, 1),
    ):
        draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def make_temp_icon(snapshot: TemperatureSnapshot, unit: str, component: Component, size: int = 64) -> Image.Image:
    """Create one tray icon for exactly one temperature.

    This intentionally uses one icon per component, Open-Hardware-Monitor style.
    Trying to render CPU/GPU in one icon makes the text unreadable on Windows.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if component == "cpu":
        text = temp_number(snapshot.cpu_c, unit)
        color = snapshot.cpu.color
    else:
        text = temp_number(snapshot.gpu_c, unit)
        color = snapshot.gpu.color

    # Fit the largest possible bold number into the icon square.
    # Most temps are 2 digits; Fahrenheit can be 3 digits, so this must shrink cleanly.
    font_size = 52
    while font_size >= 14:
        font = _load_font(font_size)
        w, h = _text_size(draw, text, font)
        if w <= size - 4 and h <= size - 4:
            break
        font_size -= 2

    font = _load_font(font_size)
    w, h = _text_size(draw, text, font)
    x = max(0, (size - w) // 2)
    # Slight upward offset compensates for font baseline so the number appears optically centered.
    y = max(0, (size - h) // 2 - 3)
    draw_text_with_outline(draw, (x, y), text, font, color)
    return image


# Compatibility for older code paths. Prefer make_temp_icon in new code.
def make_icon(snapshot: TemperatureSnapshot, unit: str, size: int = 64) -> Image.Image:
    return make_temp_icon(snapshot, unit, "gpu", size=size)
