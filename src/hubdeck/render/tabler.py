"""Tabler icons (https://tabler.io/icons, MIT) from the bundled webfont.

Refresh the font with tools/update_tabler.py.
"""

from __future__ import annotations

import difflib
import json
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "tabler"
_FONT_FILES = {False: "tabler-icons.ttf", True: "tabler-icons-filled.ttf"}


@lru_cache(maxsize=1)
def _index() -> dict:
    return json.loads((ASSETS / "icons.json").read_text(encoding="utf-8"))


def version() -> str:
    return _index()["version"]


def names(filled: bool = False) -> list[str]:
    return sorted(_index()["filled" if filled else "outline"])


def codepoint(name: str, filled: bool = False) -> int | None:
    return _index()["filled" if filled else "outline"].get(name)


def suggest(name: str, filled: bool = False, n: int = 3) -> list[str]:
    return difflib.get_close_matches(name, names(filled), n=n, cutoff=0.6)


def search(term: str, filled: bool = False) -> list[str]:
    term = term.lower()
    return [n for n in names(filled) if term in n]


@lru_cache(maxsize=32)
def font(size: int, filled: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / _FONT_FILES[filled]), size)
