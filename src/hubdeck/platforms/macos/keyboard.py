"""Keystrokes, media keys and typing via Quartz event posting.

Needs the Accessibility permission for the app hubdeck runs in (Terminal,
or Python itself for the tray); without it macOS silently drops the events.
"""

from __future__ import annotations

from ..base import Keyboard, Unsupported
from . import accessibility_trusted

# virtual key codes (kVK_*, Carbon HIToolbox/Events.h); letters and symbols are ANSI key positions
KEYCODES: dict[str, int] = {
    "ctrl": 0x3B, "lctrl": 0x3B, "rctrl": 0x3E, "shift": 0x38, "lshift": 0x38, "rshift": 0x3C,
    "alt": 0x3A, "lalt": 0x3A, "ralt": 0x3D, "win": 0x37, "rwin": 0x36,
    "esc": 0x35, "enter": 0x24, "tab": 0x30, "space": 0x31, "backspace": 0x33, "delete": 0x75,
    "insert": 0x72,  # Help, where PC keyboards have Insert
    "home": 0x73, "end": 0x77, "pgup": 0x74, "pgdn": 0x79,
    "up": 0x7E, "down": 0x7D, "left": 0x7B, "right": 0x7C,
    "capslock": 0x39, "numlock": 0x47,  # keypad Clear
    "printscreen": 0x69, "scrolllock": 0x6B, "pause": 0x71,  # F13-F15, as macOS maps PC keyboards
    "equals": 0x18, "minus": 0x1B, "comma": 0x2B, "period": 0x2F, "slash": 0x2C, "backslash": 0x2A,
    "semicolon": 0x29, "quote": 0x27, "backtick": 0x32, "lbracket": 0x21, "rbracket": 0x1E,
    "num_multiply": 0x43, "num_add": 0x45, "num_subtract": 0x4E, "num_decimal": 0x41, "num_divide": 0x4B,
    **dict(zip("asdfhgzxcv", range(0x00, 0x0A))), "b": 0x0B,
    **dict(zip("qweryt", range(0x0C, 0x12))), "o": 0x1F, "u": 0x20, "i": 0x22, "p": 0x23,
    "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16, "7": 0x1A, "8": 0x1C, "9": 0x19, "0": 0x1D,
    **dict(zip((f"f{i}" for i in range(1, 21)),
               (0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D,
                0x67, 0x6F, 0x69, 0x6B, 0x71, 0x6A, 0x40, 0x4F, 0x50, 0x5A))),
    **dict(zip((f"num{i}" for i in range(10)), (0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5B, 0x5C))),
}

# kCGEventFlagMask* for the modifier keys
FLAGS = {0x38: 0x20000, 0x3C: 0x20000, 0x3B: 0x40000, 0x3E: 0x40000,
         0x3A: 0x80000, 0x3D: 0x80000, 0x37: 0x100000, 0x36: 0x100000}

# NX_KEYTYPE_* (IOKit hidsystem/ev_keymap.h), sent as system-defined events
MEDIA = {"vol_up": 0, "vol_down": 1, "mute": 7, "play_pause": 16, "next": 17, "prev": 18}

UNICODE_CHUNK = 20   # UTF-16 units per event; longer strings get truncated by macOS


class QuartzKeyboard(Keyboard):
    def __init__(self):
        import Quartz
        self.q = Quartz
        self.source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        self.flags = 0   # modifiers we're holding down

    def _check(self, keys: list[str]):
        if not accessibility_trusted(prompt=True):
            raise OSError("macOS blocked the keystroke: allow the app running hubdeck (Terminal, or Python "
                          "for the tray) in System Settings > Privacy & Security > Accessibility")
        for k in keys:
            if k not in KEYCODES and k not in MEDIA:
                raise Unsupported(f"there is no {k!r} key on macOS")

    def _post(self, event):
        self.q.CGEventPost(self.q.kCGHIDEventTap, event)

    def _key(self, key: str, down: bool):
        if key in MEDIA:
            from AppKit import NSEvent
            state = 0xA if down else 0xB
            ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                14, (0, 0), state << 8, 0, 0, None, 8, (MEDIA[key] << 16) | (state << 8), -1)   # NSSystemDefined
            self._post(ev.CGEvent())
            return
        code = KEYCODES[key]
        if down:
            self.flags |= FLAGS.get(code, 0)
        else:
            self.flags &= ~FLAGS.get(code, 0)
        ev = self.q.CGEventCreateKeyboardEvent(self.source, code, down)
        self.q.CGEventSetFlags(ev, self.flags)
        self._post(ev)

    def down(self, keys):
        self._check(keys)
        for k in keys:
            self._key(k, True)

    def up(self, keys):
        self._check(keys)
        for k in reversed(keys):
            self._key(k, False)

    def type_text(self, text):
        self._check([])
        chunks, chunk, units = [], "", 0
        for ch in text:
            n = len(ch.encode("utf-16-le")) // 2
            if units + n > UNICODE_CHUNK:
                chunks.append((chunk, units))
                chunk, units = "", 0
            chunk, units = chunk + ch, units + n
        if chunk:
            chunks.append((chunk, units))
        for chunk, units in chunks:
            for down in (True, False):
                ev = self.q.CGEventCreateKeyboardEvent(self.source, 0, down)
                self.q.CGEventSetFlags(ev, 0)
                self.q.CGEventKeyboardSetUnicodeString(ev, units, chunk)
                self._post(ev)
