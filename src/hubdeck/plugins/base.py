"""Plugin interface.

A plugin exposes named actions (things a key can trigger) and named states
(values a key can display). `invoke` must return quickly; plugins that talk
to slow things subclass ThreadedPlugin, whose invoke only enqueues.

Plugins must not import heavy or platform-specific modules at import time:
the registry is imported to validate configs.
"""

from __future__ import annotations

import logging
import queue
import re
import sys
import threading
from typing import TYPE_CHECKING, Any, Callable, ClassVar

if TYPE_CHECKING:
    from ..deck import Deck


class PluginContext:
    def __init__(self, name: str, emit: Callable[[str, str, Any], None], deck: Deck | None = None):
        self.name = name
        self._emit = emit
        self.deck = deck  # thread-safe control of the running engine
        self.log = logging.getLogger(f"hubdeck.{name}")

    def set_state(self, key: str, value: Any):
        self._emit(self.name, key, value)


def value_state(name: str, arg: Any) -> str:
    """State key under which a plugin publishes a watched value, e.g. 'value:app_volume:spotify.exe'."""
    return f"value:{name}:{'' if arg is None else arg}"


def muted_state(name: str, arg: Any) -> str:
    """State key for whether what a watched value controls is muted (a plugin may publish it
    next to the value), e.g. 'muted:app_volume:spotify.exe'."""
    return f"muted:{name}:{'' if arg is None else arg}"


STATE_ARG = re.compile(r"([a-z_][a-z0-9_]*)(?:\[([^\[\]{}]*)\])?")


def state_key(name: str, arg: str | None = None) -> str:
    """State key of a state with an argument, e.g. 'on[light.desk]'; without one, just the name."""
    return name if arg is None else f"{name}[{arg}]"


def split_state(key: str) -> tuple[str, str | None] | None:
    """'on[light.desk]' -> ('on', 'light.desk'), 'muted' -> ('muted', None); None if malformed."""
    m = STATE_ARG.fullmatch(key)
    return (m[1], m[2]) if m else None


def _expo(x: float, expo: float) -> float:
    return (1 - expo) * x + expo * x ** 3


def adjusted(value: float, delta: float, expo: float = 0.0) -> float:
    """`value` (0-100) moved by `delta`, kept within 0-100. With `expo` (0-1) the move is along
    an RC-style expo curve, value = (1 - expo)·x + expo·x³ of a position x that `delta` moves:
    clicks get finer near 0 and coarser near 100, and 0 to 100 still takes 100/step clicks."""
    if not expo:
        return max(0.0, min(100.0, value + delta))
    y = max(0.0, min(1.0, value / 100))
    lo, hi = 0.0, 1.0           # the position of `value`: bisection, the curve only rises
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _expo(mid, expo) < y else (lo, mid)
    x = max(0.0, min(1.0, (lo + hi) / 2 + delta / 100))
    return _expo(x, expo) * 100


class Plugin:
    name: ClassVar[str]
    actions: ClassVar[dict[str, str]] = {}   # action -> help text
    states: ClassVar[dict[str, str]] = {}    # state -> help text
    state_args: ClassVar[dict[str, str]] = {}   # state that takes an [argument] -> what goes in it
    fetches_states: ClassVar[bool] = False      # publishes only what watch_states names (deck.state asks first)
    values: ClassVar[dict[str, str]] = {}    # adjustable value (turn/fader target) -> help text
    platforms: ClassVar[tuple[str, ...] | None] = None   # sys.platform values it works on; None = all

    def __init__(self, ctx: PluginContext, config: dict):
        self.ctx = ctx
        self.config = config
        self.log = ctx.log
        self.watched: set[tuple[str, Any]] = set()
        self.watched_states: set[tuple[str, str | None]] = set()

    @classmethod
    def available(cls) -> bool:
        """Whether it works on this OS. Configs may still use it elsewhere (one config for
        several machines); the editor only offers what's available."""
        return cls.platforms is None or sys.platform in cls.platforms

    @classmethod
    def check_config(cls, config: dict) -> list[str]:
        """Return problems with the plugin's `plugins.<name>` section."""
        return []

    @classmethod
    def check_action(cls, action: str, arg: Any) -> str | None:
        """Return a problem with an action's argument, or None."""
        return None

    @classmethod
    def check_state(cls, name: str, config: dict, arg: str | None = None) -> str | None:
        """Return a problem with a reference to state `name` (with `arg`, from name[arg]), or None.
        Plugins whose states come from their settings (`config`, the plugins.<name> section)
        override this; so do plugins that check the arguments."""
        if name not in cls.states:
            return f"{cls.name} has no state {name!r} (available: {', '.join(cls.states) or 'none'})"
        if name in cls.state_args and arg is None:
            return f"{cls.name}.{name} needs {cls.state_args[name]} in brackets: {cls.name}.{name}[...]"
        if name not in cls.state_args and arg is not None:
            return f"{cls.name}.{name} takes no [argument]"
        return None

    def start(self):
        pass

    def stop(self):
        pass

    @classmethod
    def check_value(cls, name: str, arg: Any) -> str | None:
        """Return a problem with a value target's argument, or None."""
        return None

    def invoke(self, action: str, arg: Any):
        raise NotImplementedError

    # states and values: called on the engine thread, must return quickly ---------------
    def watch_states(self, states: set[tuple[str, str | None]]):
        """The (state, argument) pairs the config shows, plus those asked for at runtime
        (deck.state, hubdeck send get). Plugins that only fetch what's shown publish these,
        under state_key(state, argument)."""
        self.watched_states = set(states)

    def watch(self, targets: set[tuple[str, Any]]):
        """Values the config shows/uses; publish them with set_state(value_state(name, arg), 0-100)."""
        self.watched = set(targets)

    def adjust(self, name: str, arg: Any, delta: float, expo: float = 0.0):
        """Move a value by `delta` on the turn's expo curve: set it to adjusted(current, delta, expo)."""
        raise NotImplementedError

    def set(self, name: str, arg: Any, value: float):
        raise NotImplementedError


_ADJUST, _SET = "\0adjust", "\0set"


class ThreadedPlugin(Plugin):
    """Runs setup / handle / tick / teardown on its own thread."""

    interval: ClassVar[float] = 0.05  # seconds between ticks

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: tuple[str, Any] | None = None   # taken off the queue while merging adjusts

    def start(self):
        self._thread = threading.Thread(target=self._run, name=f"hubdeck-{self.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._queue.put(("", None))  # wake the thread
        if self._thread:
            self._thread.join(timeout=3)

    def invoke(self, action, arg):
        self._queue.put((action, arg))

    def adjust(self, name, arg, delta, expo=0.0):
        self._queue.put((_ADJUST, (name, arg, delta, expo)))

    def set(self, name, arg, value):
        self._queue.put((_SET, (name, arg, value)))

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _run(self):
        try:
            self.setup()
            while not self._stop.is_set():
                try:
                    action, arg = self._next()
                    if action:
                        try:
                            if action == _ADJUST:
                                self.adjust_value(*self._merge_adjusts(arg))
                            elif action == _SET:
                                self.set_value(*arg)
                            else:
                                self.handle(action, arg)
                        except Exception:
                            self.log.exception("action %s failed", action.strip("\0"))
                    continue  # drain queued actions before the next tick
                except queue.Empty:
                    pass
                try:
                    self.tick()
                except Exception:
                    self.log.exception("tick failed")
                    self._stop.wait(1)
        except Exception:
            self.log.exception("plugin crashed")
        finally:
            try:
                self.teardown()
            except Exception:
                self.log.exception("teardown failed")

    def _next(self) -> tuple[str, Any]:
        if self._pending is not None:
            item, self._pending = self._pending, None
            return item
        return self._queue.get(timeout=self.interval)

    def _merge_adjusts(self, arg):
        """Fold the adjusts of the same value that queued up meanwhile into one,
        so a fast spin of a slow target (AppleScript, ~10 ms) doesn't build a backlog.
        Moves on the same expo curve add up like linear ones."""
        name, target, delta, expo = arg
        while self._pending is None:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item[0] == _ADJUST and item[1][:2] == (name, target) and item[1][3] == expo:
                delta += item[1][2]
            else:
                self._pending = item
        return name, target, delta, expo

    # override these ----------------------------------------------------
    def setup(self):
        pass

    def handle(self, action: str, arg: Any):
        raise NotImplementedError

    def adjust_value(self, name: str, arg: Any, delta: float, expo: float):
        raise NotImplementedError

    def set_value(self, name: str, arg: Any, value: float):
        raise NotImplementedError

    def tick(self):
        pass

    def teardown(self):
        pass
