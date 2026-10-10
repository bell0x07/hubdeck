"""Turn raw key down/up edges into gestures.

Events: "down" and "up" always fire. "press" fires on key-down when the key
has no long/double action (zero latency); otherwise it fires on release,
or after the double-press window when a double action exists.
"""

from __future__ import annotations


class KeyGesture:
    def __init__(self, long_s: float | None, double_s: float | None):
        self.long_s = long_s          # None = key has no long-press action
        self.double_s = double_s      # None = key has no double-press action
        self.down = False
        self.down_at = 0.0
        self.long_fired = False
        self.second_tap = False
        self.tap_up_at: float | None = None

    @property
    def simple(self) -> bool:
        return self.long_s is None and self.double_s is None

    @property
    def busy(self) -> bool:
        """Mid-gesture: held, or waiting to see if a second tap comes."""
        return self.down or self.tap_up_at is not None

    def edge(self, pressed: bool, now: float) -> list[str]:
        if pressed == self.down:
            return []
        self.down = pressed
        if pressed:
            out = ["down"]
            if self.simple:
                return out + ["press"]
            self.down_at, self.long_fired = now, False
            self.second_tap = self.tap_up_at is not None
            self.tap_up_at = None
            return out
        out = ["up"]
        if self.simple or self.long_fired:
            return out
        if self.second_tap:
            self.second_tap = False
            return out + ["double_press"]
        if self.double_s is not None:
            self.tap_up_at = now
            return out
        return out + ["press"]

    def tick(self, now: float) -> list[str]:
        if (self.down and self.long_s is not None and not self.long_fired and not self.second_tap
                and now - self.down_at >= self.long_s):
            self.long_fired = True
            return ["long_press"]
        if not self.down and self.tap_up_at is not None and now - self.tap_up_at > self.double_s:
            self.tap_up_at = None
            return ["press"]
        return []


class GestureTracker:
    def __init__(self, key_count: int):
        self.keys = [KeyGesture(None, None) for _ in range(key_count)]
        self._pending: dict[int, tuple[float | None, float | None]] = {}

    def configure(self, index: int, long_s: float | None, double_s: float | None):
        """Change a key's gesture set (page switch / reload). A key that is
        mid-gesture finishes it with the old settings first."""
        if self.keys[index].busy:
            self._pending[index] = (long_s, double_s)
        else:
            self._pending.pop(index, None)
            self.keys[index] = KeyGesture(long_s, double_s)

    def _settle(self, index: int):
        if index in self._pending and not self.keys[index].busy:
            self.keys[index] = KeyGesture(*self._pending.pop(index))

    def edge(self, index: int, pressed: bool, now: float) -> list[tuple[int, str]]:
        out = [(index, e) for e in self.keys[index].edge(pressed, now)]
        self._settle(index)
        return out

    def tick(self, now: float) -> list[tuple[int, str]]:
        out = []
        for i, k in enumerate(self.keys):
            out += [(i, e) for e in k.tick(now)]
            self._settle(i)
        return out
