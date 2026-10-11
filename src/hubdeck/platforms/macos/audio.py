"""macOS audio for the audio plugin: CoreAudio (through ctypes) for the default
devices, AppleScript for per-app volume.

macOS has no per-app volume mixer. Apps whose AppleScript dictionary has a
`sound volume` property (Spotify, Music) are controlled through it; for other
apps the value is unavailable.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import byref, c_float, c_uint32, c_void_p, sizeof
from pathlib import Path

from ..base import AudioBackend
from . import osascript, quote

log = logging.getLogger("hubdeck.audio")
RUNNING_CACHE_SECONDS = 2
APP_VOLUME_CACHE_SECONDS = 1.5   # how long a read app volume counts as current

_CA = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_CF = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_CA.AudioObjectHasProperty.restype = ctypes.c_ubyte
_CF.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_long, c_uint32]
_CF.CFRelease.argtypes = [c_void_p]


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


class _Address(ctypes.Structure):
    _fields_ = [("selector", c_uint32), ("scope", c_uint32), ("element", c_uint32)]


SYSTEM_OBJECT = 1
SCOPES = {"speakers": _fourcc("outp"), "microphone": _fourcc("inpt")}
GLOBAL = _fourcc("glob")
DEFAULT_DEVICE = {"speakers": _fourcc("dOut"), "microphone": _fourcc("dIn ")}
DEVICES, NAME, STREAMS, CAN_BE_DEFAULT = (_fourcc(c) for c in ("dev#", "lnam", "stm#", "dflt"))
MAIN_VOLUME, VOLUME, MUTE = (_fourcc(c) for c in ("vmvc", "volm", "mute"))


def _addr(selector: int, scope: int = GLOBAL, element: int = 0) -> _Address:
    return _Address(selector, scope, element)


def _has(obj: int, addr: _Address) -> bool:
    return bool(_CA.AudioObjectHasProperty(c_uint32(obj), byref(addr)))


def _get(obj: int, addr: _Address, ctype=c_uint32):
    value, size = ctype(), c_uint32(sizeof(ctype))
    status = _CA.AudioObjectGetPropertyData(c_uint32(obj), byref(addr), 0, None, byref(size), byref(value))
    if status:
        raise OSError(f"CoreAudio get {addr.selector.to_bytes(4, 'big').decode()} failed ({status})")
    return value.value


def _set(obj: int, addr: _Address, value):
    status = _CA.AudioObjectSetPropertyData(c_uint32(obj), byref(addr), 0, None, c_uint32(sizeof(value)), byref(value))
    if status:
        raise OSError(f"CoreAudio set {addr.selector.to_bytes(4, 'big').decode()} failed ({status})")


def _size(obj: int, addr: _Address) -> int:
    size = c_uint32(0)
    _CA.AudioObjectGetPropertyDataSize(c_uint32(obj), byref(addr), 0, None, byref(size))
    return size.value


def _name(dev: int) -> str:
    ref = _get(dev, _addr(NAME), c_void_p)
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(512)
    _CF.CFStringGetCString(ref, buf, len(buf), 0x08000100)  # kCFStringEncodingUTF8
    _CF.CFRelease(ref)
    return buf.value.decode("utf-8", "replace")


def _devices() -> list[int]:
    addr = _addr(DEVICES)
    ids = (c_uint32 * (_size(SYSTEM_OBJECT, addr) // 4))()
    size = c_uint32(sizeof(ids))
    _CA.AudioObjectGetPropertyData(c_uint32(SYSTEM_OBJECT), byref(addr), 0, None, byref(size), ids)
    return list(ids)


def _is_output(dev: int) -> bool:
    scope = SCOPES["speakers"]
    if not _size(dev, _addr(STREAMS, scope)):
        return False
    addr = _addr(CAN_BE_DEFAULT, scope)
    return not _has(dev, addr) or bool(_get(dev, addr))


class MacAudio(AudioBackend):
    def __init__(self):
        self._premute: dict[str, float] = {}   # volume before an emulated mute
        self._scriptable: dict[str, bool] = {}  # app bundle path -> has `sound volume`
        self._running_at, self._running_apps = 0.0, set()
        self._apps = AppVolumes()

    def _device(self, kind: str) -> int:
        dev = _get(SYSTEM_OBJECT, _addr(DEFAULT_DEVICE[kind]))
        if not dev:
            raise OSError(f"no default {kind} device")
        return dev

    def _volume_addrs(self, dev: int, scope: int) -> list[_Address]:
        """Where this device's volume lives: the virtual main volume, else the
        main element, else the individual channels."""
        for addrs in ([_addr(MAIN_VOLUME, scope)], [_addr(VOLUME, scope)],
                      [_addr(VOLUME, scope, ch) for ch in (1, 2)]):
            addrs = [a for a in addrs if _has(dev, a)]
            if addrs:
                return addrs
        return []

    def _mute_addrs(self, dev: int, scope: int) -> list[_Address]:
        for addrs in ([_addr(MUTE, scope)], [_addr(MUTE, scope, ch) for ch in (1, 2)]):
            addrs = [a for a in addrs if _has(dev, a)]
            if addrs:
                return addrs
        return []

    def get_volume(self, kind: str) -> float:
        dev = self._device(kind)
        addrs = self._volume_addrs(dev, SCOPES[kind])
        if not addrs:
            raise OSError(f"the {kind} device has no volume control")
        return max(_get(dev, a, c_float) for a in addrs) * 100

    def set_volume(self, kind: str, level: float):
        dev = self._device(kind)
        for a in self._volume_addrs(dev, SCOPES[kind]):
            _set(dev, a, c_float(max(0.0, min(100.0, level)) / 100))

    def get_mute(self, kind: str) -> bool:
        dev = self._device(kind)
        addrs = self._mute_addrs(dev, SCOPES[kind])
        if addrs:
            return any(_get(dev, a) for a in addrs)
        return self.get_volume(kind) < 0.5   # no mute switch: muted = volume at 0

    def set_mute(self, kind: str, mute: bool):
        dev = self._device(kind)
        addrs = self._mute_addrs(dev, SCOPES[kind])
        if addrs:
            for a in addrs:
                _set(dev, a, c_uint32(int(mute)))
        elif mute:   # many USB mics have no mute switch: turn the volume to 0 and remember it
            current = self.get_volume(kind)
            if current >= 0.5:
                self._premute[kind] = current
            self.set_volume(kind, 0)
        elif self.get_volume(kind) < 0.5:
            self.set_volume(kind, self._premute.pop(kind, 50))

    def output_name(self) -> str:
        return _name(self._device("speakers"))

    def outputs(self) -> list[str]:
        return sorted({_name(d) for d in _devices() if _is_output(d)} - {""}, key=str.lower)

    def set_output(self, want: str) -> str | None:
        """Make the first output device whose name contains `want` the default; returns its name."""
        for dev in _devices():
            name = _name(dev)
            if want.lower() in name.lower() and _is_output(dev):
                _set(SYSTEM_OBJECT, _addr(DEFAULT_DEVICE["speakers"]), c_uint32(dev))
                return name
        return None

    # -- per-app volume through AppleScript --------------------------------
    def _running(self) -> set[tuple[str, Path]]:
        """(bundle name, bundle path) of running apps, rescanned every few seconds. psutil rather
        than NSWorkspace, whose list only refreshes while an AppKit run loop is running."""
        if time.monotonic() - self._running_at < RUNNING_CACHE_SECONDS:
            return self._running_apps
        import psutil
        out = set()
        for proc in psutil.process_iter(["exe"]):
            bundle, sep, _ = (proc.info["exe"] or "").partition(".app/Contents/MacOS/")
            if sep and ".app/" not in bundle:          # skip helper apps nested in another bundle
                out.add((Path(bundle).name, Path(bundle + ".app")))
        self._running_at, self._running_apps = time.monotonic(), out
        return out

    def _has_sound_volume(self, bundle: Path) -> bool:
        key = str(bundle)
        if key not in self._scriptable:
            from Foundation import NSBundle
            b = NSBundle.bundleWithPath_(key)
            sdef = b.objectForInfoDictionaryKey_("OSAScriptingDefinition") if b else None
            try:
                text = (bundle / "Contents" / "Resources" / str(sdef)).read_text(errors="replace") if sdef else ""
            except OSError:
                text = ""
            self._scriptable[key] = 'name="sound volume"' in text
        return self._scriptable[key]

    def _find_app(self, app: str) -> str | None:
        """Name of the running app matching `app` (Windows-style 'spotify.exe' works too)."""
        want = app.lower().removesuffix(".exe").removesuffix(".app")
        for name, bundle in self._running():
            if want == name.lower() and self._has_sound_volume(bundle):
                return name
        return None

    def apps(self) -> list[str]:
        return sorted({name for name, bundle in self._running() if self._has_sound_volume(bundle)}, key=str.lower)

    def get_app_volume(self, app: str) -> float | None:
        name = self._find_app(app)
        return None if name is None else self._apps.get(name)

    def set_app_volume(self, app: str, level: float):
        name = self._find_app(app)
        if name is not None:
            self._apps.set(name, round(max(0.0, min(100.0, level))))


class AppVolumes:
    """App volumes over AppleScript without ever making the caller wait: each osascript call
    takes ~0.2 s, or hangs while macOS asks for permission, and the audio thread also serves
    the Dial and mute. `get` returns what's known and refreshes it in the background when
    stale; `set` records the wanted level and returns. A worker sends only the latest wanted
    level per app, so a fast turn costs one call.

    osascript rather than in-process NSAppleScript: NSAppleScript is main-thread only, and off
    it, compiling `tell application "Music"` hung inside hubdeck."""

    def __init__(self, script_timeout: float = 30):
        self.script_timeout = script_timeout
        self._lock = threading.Lock()
        self._known: dict[str, tuple[float, float | None]] = {}   # app -> (when read, volume)
        self._wanted: dict[str, int] = {}                          # app -> level not sent yet
        self._stale: set[str] = set()                              # apps to read again
        self._problem: dict[str, str] = {}                         # app -> last problem logged
        self._busy = False
        self._wake = threading.Event()
        threading.Thread(target=self._work, name="hubdeck-app-volume", daemon=True).start()

    def get(self, name: str) -> float | None:
        with self._lock:
            if name in self._wanted:
                return float(self._wanted[name])
            when, volume = self._known.get(name, (float("-inf"), None))
            if time.monotonic() - when >= APP_VOLUME_CACHE_SECONDS:
                self._stale.add(name)
                self._wake.set()
            return volume

    def set(self, name: str, level: int):
        with self._lock:
            self._wanted[name] = level
            self._wake.set()

    def idle(self) -> bool:
        with self._lock:
            return not (self._wanted or self._stale or self._wake.is_set() or self._busy)

    def _work(self):
        while True:
            self._wake.wait()
            with self._lock:
                self._wake.clear()
                jobs = list(self._wanted.items()) + [(n, None) for n in self._stale - self._wanted.keys()]
                self._stale.clear()
                self._busy = True
            for name, level in jobs:
                command = "get sound volume" if level is None else f"set sound volume to {level}"
                result = self._tell(name, command)
                with self._lock:
                    if level is None:
                        try:
                            self._known[name] = (time.monotonic(), float(result or ""))
                        except ValueError:
                            self._known[name] = (time.monotonic(), None)   # retried once stale
                    else:
                        if self._wanted.get(name) == level:
                            del self._wanted[name]
                        if result is not None:
                            self._known[name] = (time.monotonic(), float(level))
            with self._lock:
                self._busy = False

    def _tell(self, name: str, command: str) -> str | None:
        """Send `command` to app `name`; None (logged once) when it can't be scripted right now."""
        try:
            result = osascript(f"tell application {quote(name)} to {command}", timeout=self.script_timeout)
        except OSError as e:
            problem = (f"{name} didn't answer AppleScript ({e}). If macOS asks whether to allow controlling "
                       f"{name}, click Allow; System Settings > Privacy & Security > Automation lists it")
            if self._problem.get(name) != problem:
                log.warning("%s", problem)
                self._problem[name] = problem
            return None
        self._problem.pop(name, None)
        return result
