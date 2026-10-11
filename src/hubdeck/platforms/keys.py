"""Key names used in configs ('ctrl+shift+m'), the same on every OS.

Each Keyboard backend maps the canonical names to its own key codes. Names an
OS has no key for (e.g. `menu` on macOS) fail when pressed, not when the
config is loaded.
"""

from __future__ import annotations

MEDIA = ("play_pause", "next", "prev", "stop", "vol_up", "vol_down", "mute")

KEYS: frozenset[str] = frozenset({
    "ctrl", "lctrl", "rctrl", "shift", "lshift", "rshift", "alt", "lalt", "ralt",
    "win", "rwin", "menu",           # win = the Windows key, Command on macOS, Super on Linux
    "esc", "enter", "tab", "space", "backspace", "delete", "insert",
    "home", "end", "pgup", "pgdn", "up", "down", "left", "right",
    "capslock", "numlock", "scrolllock", "printscreen", "pause",
    "equals", "minus", "comma", "period", "slash", "backslash", "semicolon", "quote", "backtick",
    "lbracket", "rbracket",
    "num_multiply", "num_add", "num_subtract", "num_decimal", "num_divide",
    *MEDIA,
    *"0123456789", *"abcdefghijklmnopqrstuvwxyz",
    *(f"f{i}" for i in range(1, 25)), *(f"num{i}" for i in range(10)),
})

ALIASES = {
    "control": "ctrl", "lwin": "win", "cmd": "win", "command": "win", "super": "win", "meta": "win",
    "rcmd": "rwin", "option": "alt", "opt": "alt", "altgr": "ralt", "apps": "menu",
    "escape": "esc", "return": "enter", "del": "delete", "ins": "insert",
    "pageup": "pgup", "pagedown": "pgdn", "prtsc": "printscreen", "plus": "equals", "grave": "backtick",
}


def parse_combo(combo: str) -> list[str]:
    """'Cmd+Shift+M' -> ['win', 'shift', 'm']. Raises ValueError."""
    if not isinstance(combo, str) or not combo.strip():
        raise ValueError("expected a key combination like 'ctrl+shift+m'")
    keys = []
    for part in combo.lower().replace(" ", "").split("+"):
        name = ALIASES.get(part, part)
        if name not in KEYS:
            raise ValueError(f"unknown key {part!r} in {combo!r}")
        keys.append(name)
    return keys
