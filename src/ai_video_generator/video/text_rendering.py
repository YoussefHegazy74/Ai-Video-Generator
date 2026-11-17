"""Small Pillow text/layout helpers shared by the renderer and assembly."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from PIL import ImageColor, ImageDraw, ImageFont

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def parse_color(value: str, fallback: str = "#111827") -> tuple[int, int, int]:
    candidate = value if _HEX_RE.fullmatch(value) else fallback
    rgb = ImageColor.getrgb(candidate)
    return int(rgb[0]), int(rgb[1]), int(rgb[2])


def contrasting_text_color(background: str) -> tuple[int, int, int]:
    red, green, blue = parse_color(background)
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
    return (17, 24, 39) if luminance > 0.55 else (248, 250, 252)


def _font_candidates(weight: str) -> list[Path]:
    env_name = {
        "regular": "AIVG_FONT_REGULAR",
        "bold": "AIVG_FONT_BOLD",
        "mono": "AIVG_FONT_MONO",
    }[weight]
    candidates: list[Path] = []
    configured = os.environ.get(env_name)
    if configured:
        candidates.append(Path(configured).expanduser())

    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    if weight == "bold":
        candidates.extend(
            [
                windir / "Fonts" / "segoeuib.ttf",
                windir / "Fonts" / "arialbd.ttf",
            ]
        )
    elif weight == "mono":
        candidates.extend(
            [
                windir / "Fonts" / "consola.ttf",
                windir / "Fonts" / "cour.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                windir / "Fonts" / "segoeui.ttf",
                windir / "Fonts" / "arial.ttf",
            ]
        )

    candidates.extend(
        [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
            if weight == "bold"
            else Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
            if weight == "mono"
            else Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        ]
    )
    return candidates


@lru_cache(maxsize=64)
def load_font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _font_candidates(weight):
        try:
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    fill: tuple[int, int, int],
    *,
    line_spacing: int = 8,
    max_lines: int | None = None,
) -> int:
    """Draw wrapped text and return the bottom y coordinate."""

    lines = wrap_text(draw, text, font, max_width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = lines[-1].rstrip() + "…"
    x, y = xy
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + line_spacing
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def draw_grid(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    *,
    color: tuple[int, int, int],
    spacing: int = 48,
) -> None:
    width, height = size
    for x in range(0, width, spacing):
        draw.line([(x, 0), (x, height)], fill=color, width=1)
    for y in range(0, height, spacing):
        draw.line([(0, y), (width, y)], fill=color, width=1)
