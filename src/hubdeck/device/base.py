"""Interface every MasterHub module driver implements.

Only key modules exist today (M4315). Knobs, dials and rollers will get
their own protocols once their wire format is known.
"""

from __future__ import annotations

from typing import Protocol

from PIL import Image


class DeviceError(Exception):
    """The module is missing, unplugged or rejected a report."""


class KeyModule(Protocol):
    key_count: int
    image_size: int  # square pixel size of a key face

    def set_software_mode(self, on: bool = True) -> None: ...

    def set_brightness(self, percent: int) -> None: ...

    def encode(self, img: Image.Image) -> bytes:
        """Turn a rendered face into the bytes set_image expects."""
        ...

    def set_image(self, index: int, data: bytes) -> None: ...

    def poll(self) -> list[tuple[int, bool]]:
        """Return [(key_index, pressed)] edges since the last poll. Never blocks."""
        ...

    def close(self) -> None: ...
