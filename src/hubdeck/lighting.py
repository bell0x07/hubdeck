"""Turn the `lighting:` config + plugin states into LED writes.

Only fields the config sets are written, only when they change, always with
save = 0 (no flash wear). Software animations are computed here at ~30 fps.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable

from .config.schema import Animate, Light, LightZone, state_on
from .device import DeviceError
from .render.faces import parse_color

log = logging.getLogger("hubdeck.lighting")

FRAME_SECONDS = 1 / 30
Rgb = tuple[int, int, int]


@dataclass(frozen=True)
class LedState:
    mode: str | None = None
    rgb: Rgb | None = None
    brightness: int | None = None


def _mix(a: Rgb, b: Rgb, t: float) -> Rgb:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def animate_color(anim: Animate, now: float) -> Rgb:
    colors = [parse_color(c) for c in anim.colors]
    phase = (now % anim.period) / anim.period
    if anim.effect == "cycle":
        pos = phase * len(colors)
        i = int(pos)
        return _mix(colors[i % len(colors)], colors[(i + 1) % len(colors)], pos - i)
    on, off = colors[0], (colors[1] if len(colors) > 1 else (0, 0, 0))
    if anim.effect == "blink":
        return on if phase < 0.5 else off
    return _mix(off, on, (1 - math.cos(phase * 2 * math.pi)) / 2)   # pulse: smooth in/out


def gamma_rgb(rgb: Rgb, gamma: float) -> Rgb:
    """Screen colour -> LED drive level. LEDs are linear, colour codes assume a curve: without this
    the weaker channels come out far too strong (green looks turquoise, red looks pink)."""
    return tuple(round(255 * (c / 255) ** gamma) for c in rgb)


def _merged(zone: LightZone, overlay: str | None) -> Light:
    """The zone's own fields with its `on` or `unknown` light on top."""
    fields = zone.model_dump(exclude_unset=True, exclude={"state", "on", "unknown"})
    if overlay and getattr(zone, overlay) is not None:
        fields.update(getattr(zone, overlay).model_dump(exclude_unset=True))
    return Light.model_validate(fields)


def resolve(zone: LightZone, state_get: Callable[[str], Any], value: Any, now: float,
            gamma: float = 1.0, cache: dict | None = None) -> LedState:
    """Desired LED state of one zone right now (gamma 1 = no correction). `cache` keeps the
    merged lights by (id(zone), overlay); its owner keeps the zones alive."""
    overlay = None
    if zone.state:
        v = state_on(zone.state, state_get)
        overlay = "unknown" if v is None else "on" if v else None
    if cache is None:
        light = _merged(zone, overlay)
    elif (light := cache.get((id(zone), overlay))) is None:
        light = cache[id(zone), overlay] = _merged(zone, overlay)
    mode, rgb = light.mode, parse_color(light.color) if light.color else None
    if light.animate:
        mode, rgb = "static", animate_color(light.animate, now)
    elif rgb is not None and mode is None:
        mode = "static"           # a colour on its own means "show this colour"
    if rgb is not None and gamma != 1.0:
        rgb = gamma_rgb(rgb, gamma)
    brightness = light.brightness
    if brightness == "value":
        if value is None:
            brightness = light.min_brightness
        else:
            # perceived brightness isn't linear either: half power still looks almost full
            v = (max(0.0, min(100.0, float(value))) / 100) ** gamma
            brightness = round(light.min_brightness + (100 - light.min_brightness) * v)
    return LedState(mode, rgb, brightness)


class LightingController:
    def __init__(self):
        self.written: dict[tuple[str, int], LedState] = {}
        self._next_frame = 0.0
        self._zones = None                 # the zones list self._lights belongs to
        self._lights: dict = {}            # resolve()'s cache: pydantic merges at 30 fps cost ~0.5% of a core

    def forget(self, kind: str | None = None):
        """Rewrite everything (module reconnected / config changed)."""
        for key in [k for k in self.written if kind is None or k[0] == kind]:
            del self.written[key]

    def update(self, zones, devices: dict, state_get, value_for, now: float, force: bool = False,
               gamma: float = 1.0) -> list[str]:
        """Write changed zones. Returns module kinds whose writes failed (to be dropped)."""
        if now < self._next_frame and not force:
            return []
        self._next_frame = now + FRAME_SECONDS
        if zones is not self._zones:       # rebuilt: config reload or a runtime light change
            self._zones, self._lights = zones, {}
        failed = []
        for kind, idx, zone in zones:
            dev = devices.get(kind)
            if dev is None or kind in failed:
                continue
            want = resolve(zone, state_get, value_for(kind, idx), now, gamma, self._lights)
            have = self.written.get((kind, idx), LedState())
            try:
                mode_changed = want.mode is not None and want.mode != have.mode
                if mode_changed:
                    dev.set_led_mode(idx, want.mode)
                if want.rgb is not None and (want.rgb != have.rgb or mode_changed):
                    dev.set_led_color(idx, want.rgb)
                if want.brightness is not None and (want.brightness != have.brightness or mode_changed):
                    dev.set_led_brightness(idx, want.brightness)
            except DeviceError as e:
                log.warning("%s lighting write failed: %s", kind, e)
                failed.append(kind)
                continue
            self.written[(kind, idx)] = LedState(want.mode or have.mode, want.rgb or have.rgb,
                                                 want.brightness if want.brightness is not None else have.brightness)
        return failed
