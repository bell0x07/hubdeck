"""Keystrokes, media keys and typing. The OS side is platforms.current().keyboard()."""

from __future__ import annotations

from ..platforms import current
from ..platforms.keys import MEDIA, parse_combo
from .base import Plugin


class KeyboardPlugin(Plugin):
    name = "keyboard"
    actions = {
        "keys": "press and release a combination, e.g. {keys: ctrl+shift+m} (win = cmd on macOS)",
        "key_down": "hold a combination down (pair with key_up in on_up)",
        "key_up": "release a combination",
        "media": f"media key: {', '.join(MEDIA)}",
        "type": "type text, e.g. {type: 'hello'}",
    }

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self._kb = None

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
        if self._kb is None:
            self._kb = current().keyboard()
        if action == "keys":
            self._kb.press(parse_combo(arg))
        elif action == "key_down":
            self._kb.down(parse_combo(arg))
        elif action == "key_up":
            self._kb.up(parse_combo(arg))
        elif action == "media":
            self._kb.press([arg])
        elif action == "type":
            self._kb.type_text(arg)
