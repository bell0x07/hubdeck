"""Keeps the modules the config needs open, and copes with modules being
plugged in, swapped or dropping off USB while hubdeck runs."""

from __future__ import annotations

import logging
from typing import Any, Callable

from .base import DeviceError
from .hid import MODULE_TYPES, enumerate_modules
from .modules import DRIVERS, Event

log = logging.getLogger("hubdeck.devices")

RETRY_SECONDS = 3


def default_factories() -> dict[str, Callable[[], Any]]:
    from .m4315 import M4315
    return {"keys": M4315, **DRIVERS}


def present_kinds() -> set[str]:
    """Kinds of modules currently on USB (one hidapi enumerate)."""
    return {mtype.aliases[0] for mtype, _ in enumerate_modules()}


class DeviceManager:
    def __init__(self, factories: dict[str, Callable[[], Any]] | None = None,
                 present: Callable[[], set[str]] | None = present_kinds):
        self.factories = factories if factories is not None else default_factories()
        self.present = present          # None: just try every factory (tests)
        self.devices: dict[str, Any] = {}
        self.wanted: set[str] = set()
        self._retry_at = 0.0
        self._last_error: dict[str, str] = {}

    def scan(self, now: float, force: bool = False) -> list[str]:
        """Open wanted modules that aren't open yet. Returns the kinds that just connected."""
        missing = [k for k in self.wanted if k not in self.devices and k in self.factories]
        if not missing or (now < self._retry_at and not force):
            return []
        self._retry_at = now + RETRY_SECONDS
        on_usb = self.present() if self.present else None
        connected = []
        for kind in missing:
            if on_usb is not None and kind not in on_usb:
                self._report(kind, f"{kind} module not connected")
                continue
            try:
                self.devices[kind] = self.factories[kind]()
            except DeviceError as e:
                self._report(kind, str(e))
                continue
            self._last_error.pop(kind, None)
            log.info("%s module connected", kind)
            connected.append(kind)
        return connected

    def _report(self, kind: str, msg: str):
        if self._last_error.get(kind) != msg:
            log.warning("%s - retrying every %ss", msg, RETRY_SECONDS)
            self._last_error[kind] = msg

    def poll(self) -> tuple[list[tuple[str, Event]], list[tuple[str, str]]]:
        """(events, lost) where lost = [(kind, error)] for modules that dropped off."""
        events, lost = [], []
        for kind, dev in list(self.devices.items()):
            try:
                for e in dev.poll():
                    if isinstance(e, tuple):          # key module reports (index, pressed)
                        e = Event("key", e[0], e[1])
                    events.append((kind, e))
            except DeviceError as err:
                lost.append((kind, str(err)))
                self.drop(kind)
        return events, lost

    def defer(self, now: float):
        """Wait a full retry interval before reopening (after a module dropped off)."""
        self._retry_at = now + RETRY_SECONDS

    def drop(self, kind: str):
        dev = self.devices.pop(kind, None)
        if dev:
            try:
                dev.close()
            except Exception:
                pass
            log.warning("%s module lost", kind)

    def close_all(self):
        for kind in list(self.devices):
            dev = self.devices.pop(kind)
            try:
                dev.close()
            except Exception:
                pass


KINDS = tuple(t.aliases[0] for t in MODULE_TYPES.values())
