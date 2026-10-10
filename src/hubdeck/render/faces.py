"""Draw key faces: Tabler icon and/or label, or free text, on a solid colour.

Everything is drawn on a 4x canvas and downscaled for smooth edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageColor, ImageDraw, ImageFont

from . import tabler

SUPERSAMPLE = 4
Color = tuple[int, int, int]


@dataclass(frozen=True)
class RenderFace:
    bg: Color
    fg: Color                    # icon colour
    label_color: Color           # label / text colour
    icon: str | None = None
    filled: bool = False
    label: str | None = None
    text: str | None = None
    font_size: int | None = None  # in device pixels; None = fit automatically


def parse_color(value: str) -> Color:
    """'#c81e28', 'red', 'rgb(200,30,40)' -> (r, g, b). Raises ValueError."""
    return ImageColor.getrgb(value)[:3]


@lru_cache(maxsize=64)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fit_font(d: ImageDraw.ImageDraw, text: str, max_w: float, max_h: float, start: int):
    size = start
    while size > 8:
        f = _font(size)
        l, t, r, b = d.multiline_textbbox((0, 0), text, font=f, align="center", spacing=size // 6)
        if r - l <= max_w and b - t <= max_h:
            return f
        size = int(size * 0.9)
    return _font(8)


def _draw_text(d, text: str, center: tuple[float, float], color: Color, max_w: float, max_h: float,
               start: int, fixed: int | None = None):
    f = _font(fixed) if fixed else _fit_font(d, text, max_w, max_h, start)
    d.multiline_text(center, text, font=f, fill=color, anchor="mm", align="center",
                     spacing=getattr(f, "size", 10) // 6)


def _draw_icon(d, face: RenderFace, center: tuple[float, float], size: int):
    cp = tabler.codepoint(face.icon, face.filled) if face.icon else None
    if cp is None:  # unknown icon: show a visible marker instead of failing
        d.text(center, "?", font=_font(size), fill=face.fg, anchor="mm")
        return
    d.text(center, chr(cp), font=tabler.font(size, face.filled), fill=face.fg, anchor="mm")


def render(face: RenderFace, size: int = 72, rotate: int = 0) -> Image.Image:
    s = size * SUPERSAMPLE
    img = Image.new("RGB", (s, s), face.bg)
    d = ImageDraw.Draw(img)
    fixed = face.font_size * SUPERSAMPLE if face.font_size else None
    caption = face.label if face.label is not None else None
    body = face.text

    if face.icon and (caption or body):
        _draw_icon(d, face, (s / 2, s * 0.40), int(s * 0.56))
        _draw_text(d, caption or body, (s / 2, s * 0.84), face.label_color, s * 0.92, s * 0.24, int(s * 0.18), fixed)
    elif face.icon:
        _draw_icon(d, face, (s / 2, s / 2), int(s * 0.74))
    elif body and caption:
        _draw_text(d, body, (s / 2, s * 0.40), face.label_color, s * 0.90, s * 0.58, int(s * 0.42), fixed)
        _draw_text(d, caption, (s / 2, s * 0.84), face.label_color, s * 0.92, s * 0.24, int(s * 0.18))
    elif body or caption:
        _draw_text(d, body or caption, (s / 2, s / 2), face.label_color, s * 0.88, s * 0.88, int(s * 0.42), fixed)

    img = img.resize((size, size), Image.LANCZOS)
    if rotate:
        img = img.rotate(-rotate)
    return img


BLANK = RenderFace(bg=(0, 0, 0), fg=(0, 0, 0), label_color=(0, 0, 0))


def number_face(n: int, size: int = 72, rotate: int = 0) -> Image.Image:
    """Key number plus a yellow ^ in the top-left corner to show orientation."""
    s = size * SUPERSAMPLE
    img = Image.new("RGB", (s, s), (30, 60, 140))
    d = ImageDraw.Draw(img)
    d.text((s / 2, s * 0.55), str(n), font=_font(int(s * 0.5)), fill=(255, 255, 255), anchor="mm")
    d.text((s * 0.06, s * 0.02), "^", font=_font(int(s * 0.2)), fill=(255, 220, 0))
    img = img.resize((size, size), Image.LANCZOS)
    return img.rotate(-rotate) if rotate else img
