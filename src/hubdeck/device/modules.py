"""Drivers for the non-key modules: Dial (M131E) and the M0 family
(Base, Knobs, Rollers, Faders). Byte layouts: docs/protocol/."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from .hid import MODULE_TYPES, HidModule, build_chunked_reports


@dataclass(frozen=True)
class Event:
    kind: str     # "key" (value: pressed bool), "turn" (value: +/-steps), "slide" (value: 0-100)
    index: int    # 0-based control index as the module reports it
    value: int | bool


# LED modes (docs/protocol/m0-family.md)
LED_MODES = {"off": 0, "static": 1, "breathing": 2, "strobe": 3, "spring": 4,
             "cycling": 5, "random": 6, "wave": 7, "water": 8, "rainbow": 9}


class DialModule:
    """1xDial w/ IPS: 240x240 round screen, rotary encoder with push."""
    kind = "dial"
    image_size = 240
    shape = "round"

    def __init__(self, serial: str | None = None):
        self.hid = HidModule(MODULE_TYPES[0x0206], serial=serial)

    def set_software_mode(self, on: bool = True):
        self.hid.set_feature(bytes([0x03, 0x49, 1 if on else 0]))

    def set_brightness(self, percent: int):
        self.hid.set_feature(bytes([0x03, 0x44, max(0, min(100, int(percent)))]))

    def encode(self, img: Image.Image) -> bytes:
        img = img.convert("RGB")
        if img.size != (self.image_size, self.image_size):
            img = img.resize((self.image_size, self.image_size), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, subsampling=0, progressive=False, optimize=False)
        return out.getvalue()

    def set_image(self, index: int, data: bytes):
        self.hid.write_many(build_chunked_reports(0x45, data, 0, self.hid.type.out_len))

    def poll(self) -> list[Event]:
        events = []
        for f in self.hid.read_frames():
            if f.channel != 0 or not f.data:
                continue
            if f.event == 0x03:      # turn: 00 = clockwise
                events.append(Event("turn", 0, 1 if f.data[0] == 0 else -1))
            elif f.event == 0x04:    # press
                events.append(Event("key", 0, f.data[0] == 1))
        return events

    def close(self):
        self.hid.close()


class M0Module:
    """Common part of Base, Knobs, Rollers and Faders."""
    pid: int
    kind: str
    led_module: int | None = None   # LED module id in 02 2c/2d/2e commands
    led_count: int = 0
    led_order: tuple[int, ...] = ()  # control index -> LED index (identity if empty)

    def __init__(self, serial: str | None = None):
        self.hid = HidModule(MODULE_TYPES[self.pid], serial=serial)

    def _led_index(self, control: int | None) -> int:
        if control is None:
            return 0xFF
        return self.led_order[control] if self.led_order else control

    def set_led_mode(self, control: int | None, mode: str, save: bool = False):
        self.hid.write(bytes([0x02, 0x2C, self.led_module, self._led_index(control), LED_MODES[mode], int(save)]))

    def set_led_color(self, control: int | None, rgb: tuple[int, int, int], save: bool = False):
        self.hid.write(bytes([0x02, 0x2D, self.led_module, self._led_index(control), *rgb, int(save)]))

    def set_led_brightness(self, control: int | None, percent: int):
        self.hid.write(bytes([0x02, 0x2E, self.led_module, self._led_index(control),
                              max(0, min(100, int(percent)))]))

    def poll(self) -> list[Event]:
        events = []
        for f in self.hid.read_frames():
            if f.channel == 0:
                events += self._decode(f.event, f.data)
        return events

    def _decode(self, event: int | None, data: bytes) -> list[Event]:
        return []

    def close(self):
        self.hid.close()


class BaseModule(M0Module):
    pid, kind = 0x01F6, "base"
    led_module, led_count = 0x00, 1


class KnobsModule(M0Module):
    pid, kind = 0x0202, "knobs"
    led_module, led_count = 0x02, 3
    led_order = (2, 0, 1)   # top, bottom-left, bottom-right (docs/protocol/m003k.md)

    def __init__(self, serial=None):
        super().__init__(serial)
        self.pressed = [False] * 3

    def _decode(self, event, data):
        out = []
        if event == 0x00:        # rotate: 01 cw, 00 ccw, ff none
            out = [Event("turn", i, 1 if b == 1 else -1) for i, b in enumerate(data[:3]) if b in (0, 1)]
        elif event == 0x03:      # press state of all three
            for i, b in enumerate(data[:3]):
                if (b == 1) != self.pressed[i]:
                    self.pressed[i] = b == 1
                    out.append(Event("key", i, b == 1))
        return out


class RollersModule(M0Module):
    pid, kind = 0x0204, "rollers"

    def _decode(self, event, data):
        if event == 0x01:        # 00 = up (+1), 01 = down (-1), ff none
            return [Event("turn", i, 1 if b == 0 else -1) for i, b in enumerate(data[:2]) if b in (0, 1)]
        return []


class FadersModule(M0Module):
    pid, kind = 0x0200, "faders"
    led_module, led_count = 0x01, 5

    def __init__(self, serial=None):
        super().__init__(serial)
        self.values: list[int | None] = [None] * 5

    def _decode(self, event, data):
        out = []
        if event == 0x02:        # absolute positions 0-100
            for i, v in enumerate(data[:5]):
                if v != self.values[i]:
                    self.values[i] = v
                    out.append(Event("slide", i, v))
        return out


DRIVERS = {d.kind: d for d in (DialModule, BaseModule, KnobsModule, RollersModule, FadersModule)}
