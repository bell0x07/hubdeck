"""The 15-key module as one screen: a picture, a chart or big text.

Pictures and charts are drawn on a canvas the size of all 15 key screens plus the bezels between them, then cut
into the 15 key pictures. The bezel strips are never shown, so lines and shapes continue across
the keys as if seen through a window. Text instead gets a key per letter (letter_cells).
The bezel is about 22 px at the keys' 72 px per screen
(measured with test patterns on the hardware, see docs/protocol/).
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .faces import Color, _draw_text, _fit_font, _font

KEY = 72                     # pixels per key screen
COLS, ROWS = 5, 3            # as mounted at rotate 0
SUPERSAMPLE = 2              # charts and text are drawn bigger, then scaled down: smooth edges
GRID = (60, 60, 70)
DEFAULT_COLORS = ["accent", "#ffffff", "#c81e28", "#e5a73c"]


def grid(rotate: int = 0) -> tuple[int, int]:
    """(columns, rows) of keys as mounted: turned sideways, the 5 x 3 module is 3 x 5."""
    return (ROWS, COLS) if rotate % 180 else (COLS, ROWS)


def canvas_size(cols: int, rows: int, gap: int, key: int = KEY) -> tuple[int, int]:
    return cols * key + (cols - 1) * gap, rows * key + (rows - 1) * gap


def tiles(canvas: Image.Image, cols: int, rows: int, gap: int, key: int = KEY) -> list[Image.Image]:
    """The key pictures, in reading order (config numbers 1, 2, ...), skipping the bezels."""
    return [canvas.crop((c * (key + gap), r * (key + gap), c * (key + gap) + key, r * (key + gap) + key))
            for r in range(rows) for c in range(cols)]


def draw_picture(size: tuple[int, int], picture: Image.Image | None, fit: str, bg: Color) -> Image.Image:
    """A picture over the whole canvas: `cover` fills it (cropping), `contain` shows all of it."""
    img = Image.new("RGB", size, bg)
    if picture is None:
        return img
    w, h = size
    src = picture.convert("RGBA")
    scale = (min if fit == "contain" else max)(w / src.width, h / src.height)
    sw, sh = max(1, round(src.width * scale)), max(1, round(src.height * scale))
    src = src.resize((sw, sh), Image.LANCZOS)
    img.paste(src, ((w - sw) // 2, (h - sh) // 2), src)
    return img


def draw_chart(size: tuple[int, int], series: list[list[tuple[float, float | None]]], now: float,
               history: float, value_range: tuple[float, float] | None, colors: list[Color],
               bg: Color, label: str | None = None, label_color: Color = (255, 255, 255)) -> Image.Image:
    """Lines of (time, value) over the last `history` seconds, newest at the right edge.
    `value_range` None fits the values shown. The label goes in the top-left key."""
    s = SUPERSAMPLE
    w, h = size[0] * s, size[1] * s
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img, "RGBA")
    values = [v for points in series for t, v in points if v is not None and now - t <= history]
    if value_range is None:
        lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
        pad = (hi - lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    else:
        lo, hi = value_range
    span = (hi - lo) or 1.0
    margin = 4 * s                                   # keep the line clear of the outer edges

    def xy(t: float, v: float) -> tuple[float, float]:
        x = w * (1 - (now - t) / history)
        y = margin + (h - 2 * margin) * (1 - (v - lo) / span)
        return x, min(max(y, margin), h - margin)

    for frac in (0.25, 0.5, 0.75):
        y = margin + (h - 2 * margin) * frac
        d.line([(0, y), (w, y)], fill=GRID, width=s)
    for i, points in enumerate(series):
        color = colors[i % len(colors)]
        runs, run = [], []                           # a missing value breaks the line
        for t, v in points:
            if now - t > history + 2:
                continue
            if v is None:
                if run:
                    runs.append(run)
                run = []
            else:
                run.append(xy(t, v))
        if run:
            runs.append(run)
        for run in runs:
            if i == 0 and len(run) > 1:              # a soft fill under the first line
                d.polygon(run + [(run[-1][0], h), (run[0][0], h)], fill=color + (48,))
            if len(run) > 1:
                d.line(run, fill=color, width=3 * s, joint="curve")
            else:
                x, y = run[0]
                d.ellipse([x - 2 * s, y - 2 * s, x + 2 * s, y + 2 * s], fill=color)
    img = img.resize(size, Image.LANCZOS)
    if label:
        _label_in_first_key(img, label, label_color)
    return img


def letter_cells(text: str, cols: int, rows: int) -> dict[int, str]:
    """Where each character of a text display goes, one per key (key number, reading order).
    Text across several keys loses too much behind the bezels to read, so every character
    gets a key of its own: lines (wrapped at the row's length) are centred across the rows,
    and the rows on the module. What doesn't fit is left out."""
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        while len(line) > cols:
            lines.append(line[:cols].rstrip())
            line = line[cols:].lstrip()
        lines.append(line)
    lines = lines[:rows]
    top = (rows - len(lines)) // 2
    cells = {}
    for r, line in enumerate(lines):
        left = (cols - len(line)) // 2
        for c, ch in enumerate(line):
            if not ch.isspace():
                cells[(top + r) * cols + left + c + 1] = ch
    return cells


def draw_letters(cols: int, rows: int, text: str, bg: Color, fg: Color) -> list[Image.Image]:
    """The key pictures of a text display (reading order): one big letter per key, all the
    same size, the largest that fits the widest of them."""
    cells = letter_cells(text, cols, rows)
    s = 4
    box = KEY * s * 0.84
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    size = min((getattr(_fit_font(probe, ch, box, box, int(KEY * s)), "size", 10) for ch in set(cells.values())),
               default=10)
    font = _font(size)
    out = []
    for number in range(1, cols * rows + 1):
        if number not in cells:
            out.append(Image.new("RGB", (KEY, KEY), bg))
            continue
        img = Image.new("RGB", (KEY * s, KEY * s), bg)
        ImageDraw.Draw(img).text((KEY * s / 2, KEY * s / 2), cells[number], font=font, fill=fg, anchor="mm")
        out.append(img.resize((KEY, KEY), Image.LANCZOS))
    return out


def _label_in_first_key(img: Image.Image, label: str, color: Color):
    """Small text stays readable only inside one key, so a label goes in the top-left one,
    on a dark plate so it shows over a chart."""
    s = 4
    plate = Image.new("RGBA", (KEY * s, KEY * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(plate)
    d.rounded_rectangle([2 * s, 2 * s, (KEY - 2) * s, (KEY - 2) * s], radius=8 * s, fill=(0, 0, 0, 150))
    _draw_text(d, label, (KEY * s / 2, KEY * s / 2), color, (KEY - 10) * s, (KEY - 12) * s, int(KEY * s * 0.3))
    small = plate.resize((KEY, KEY), Image.LANCZOS)
    img.paste(small, (0, 0), small)

