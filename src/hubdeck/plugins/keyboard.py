"""Keystrokes, media keys and typing via Win32 SendInput."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from .base import Plugin

MEDIA = {
    "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2,
    "vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD,
}

VK: dict[str, int] = {
    "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "alt": 0x12, "lalt": 0xA4, "ralt": 0xA5, "altgr": 0xA5,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "menu": 0x5D, "apps": 0x5D,
    "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23, "pgup": 0x21, "pageup": 0x21, "pgdn": 0x22, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
    "printscreen": 0x2C, "prtsc": 0x2C, "pause": 0x13,
    "plus": 0xBB, "equals": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE,
    "backtick": 0xC0, "grave": 0xC0, "lbracket": 0xDB, "rbracket": 0xDD,
    "num_multiply": 0x6A, "num_add": 0x6B, "num_subtract": 0x6D, "num_decimal": 0x6E, "num_divide": 0x6F,
    **MEDIA,
}
VK.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
VK.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
VK.update({f"num{i}": 0x60 + i for i in range(10)})

# keys that need KEYEVENTF_EXTENDEDKEY
EXTENDED = {0xA3, 0xA5, 0x5B, 0x5C, 0x5D, 0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22,
            0x26, 0x28, 0x25, 0x27, 0x90, 0x2C, 0x6F, *MEDIA.values()}


def parse_combo(combo: str) -> list[int]:
    """'ctrl+shift+m' -> [VK_CONTROL, VK_SHIFT, 'M']. Raises ValueError."""
    if not isinstance(combo, str) or not combo.strip():
        raise ValueError("expected a key combination like 'ctrl+shift+m'")
    vks = []
    for part in combo.lower().replace(" ", "").split("+"):
        if part not in VK:
            raise ValueError(f"unknown key {part!r} in {combo!r}")
        vks.append(VK[part])
    return vks


# -- SendInput ---------------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    if vk in EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    return _INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags)))


def _send(inputs: list[_INPUT]):
    if sys.platform != "win32":
        raise OSError("keyboard actions are only supported on Windows")
    arr = (_INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    if sent != len(inputs):
        raise OSError(f"SendInput sent {sent}/{len(inputs)} events (blocked by a higher-privilege window?)")


def press_combo(vks: list[int]):
    _send([_key_input(v) for v in vks] + [_key_input(v, flags=KEYEVENTF_KEYUP) for v in reversed(vks)])


def key_down(vks: list[int]):
    _send([_key_input(v) for v in vks])


def key_up(vks: list[int]):
    _send([_key_input(v, flags=KEYEVENTF_KEYUP) for v in reversed(vks)])


def type_text(text: str):
    events = []
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        unit = int.from_bytes(data[i:i + 2], "little")
        events += [_key_input(scan=unit, flags=KEYEVENTF_UNICODE),
                   _key_input(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    _send(events)


class KeyboardPlugin(Plugin):
    name = "keyboard"
    actions = {
        "keys": "press and release a combination, e.g. {keys: ctrl+shift+m}",
        "key_down": "hold a combination down (pair with key_up in on_up)",
        "key_up": "release a combination",
        "media": f"media key: {', '.join(MEDIA)}",
        "type": "type text, e.g. {type: 'hello'}",
    }

    @classmethod
    def check_action(cls, action, arg):
        try:
            if action in ("keys", "key_down", "key_up"):
                parse_combo(arg)
            elif action == "media" and arg not in MEDIA:
                return f"unknown media key {arg!r}; use one of: {', '.join(MEDIA)}"
            elif action == "type" and not isinstance(arg, str):
                return "keyboard.type needs text"
        except ValueError as e:
            return str(e)
        return None

    def invoke(self, action, arg):
        if action == "keys":
            press_combo(parse_combo(arg))
        elif action == "key_down":
            key_down(parse_combo(arg))
        elif action == "key_up":
            key_up(parse_combo(arg))
        elif action == "media":
            press_combo([MEDIA[arg]])
        elif action == "type":
            type_text(arg)
