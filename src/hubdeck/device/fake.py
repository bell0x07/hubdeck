"""In-memory key module for tests and dry runs."""

from __future__ import annotations

from PIL import Image


class FakeKeyModule:
    key_count = 15
    image_size = 72

    def __init__(self):
        self.images: dict[int, bytes] = {}
        self.writes: list[int] = []      # key index of every set_image call
        self.brightness: int | None = None
        self.software_mode = False
        self.closed = False
        self._edges: list[tuple[int, bool]] = []

    # test helpers --------------------------------------------------------
    def press(self, index: int):
        self._edges.append((index, True))

    def release(self, index: int):
        self._edges.append((index, False))

    # KeyModule -----------------------------------------------------------
    def set_software_mode(self, on: bool = True):
        self.software_mode = on

    def set_brightness(self, percent: int):
        self.brightness = percent

    def encode(self, img: Image.Image) -> bytes:
        # cheap and deterministic: raw pixels at device size
        return img.convert("RGB").resize((self.image_size, self.image_size)).tobytes()

    def set_image(self, index: int, data: bytes):
        self.images[index] = data
        self.writes.append(index)

    def poll(self) -> list[tuple[int, bool]]:
        edges, self._edges = self._edges, []
        return edges

    def close(self):
        self.closed = True


class FakeControls:
    """Fake dial / knobs / rollers / faders / base: queue events, record LED writes."""

    def __init__(self, kind: str):
        from .modules import Event
        self._Event = Event
        self.kind = kind
        self.image_size = 240
        self.shape = "round" if kind == "dial" else "square"
        self.images: dict[int, bytes] = {}
        self.writes: list[int] = []
        self.software_mode = False
        self.brightness = None
        self.leds: list[tuple] = []
        self.closed = False
        self._events = []

    def push(self, kind: str, index: int, value):
        self._events.append(self._Event(kind, index, value))

    def set_software_mode(self, on=True):
        self.software_mode = on

    def set_brightness(self, percent):
        self.brightness = percent

    def encode(self, img: Image.Image) -> bytes:
        return img.convert("RGB").tobytes()

    def set_image(self, index, data):
        self.images[index] = data
        self.writes.append(index)

    def set_led_mode(self, control, mode, save=False):
        self.leds.append(("mode", control, mode))

    def set_led_color(self, control, rgb, save=False):
        self.leds.append(("color", control, tuple(rgb)))

    def set_led_brightness(self, control, percent):
        self.leds.append(("brightness", control, percent))

    def poll(self):
        events, self._events = self._events, []
        return events

    def close(self):
        self.closed = True
