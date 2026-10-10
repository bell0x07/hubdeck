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
import threading
from typing import Any, Callable, ClassVar


class PluginContext:
    def __init__(self, name: str, emit: Callable[[str, str, Any], None]):
        self.name = name
        self._emit = emit
        self.log = logging.getLogger(f"hubdeck.{name}")

    def set_state(self, key: str, value: Any):
        self._emit(self.name, key, value)


class Plugin:
    name: ClassVar[str]
    actions: ClassVar[dict[str, str]] = {}   # action -> help text
    states: ClassVar[dict[str, str]] = {}    # state -> help text

    def __init__(self, ctx: PluginContext, config: dict):
        self.ctx = ctx
        self.config = config
        self.log = ctx.log

    @classmethod
    def check_config(cls, config: dict) -> list[str]:
        """Return problems with the plugin's `plugins.<name>` section."""
        return []

    @classmethod
    def check_action(cls, action: str, arg: Any) -> str | None:
        """Return a problem with an action's argument, or None."""
        return None

    def start(self):
        pass

    def stop(self):
        pass

    def invoke(self, action: str, arg: Any):
        raise NotImplementedError


class ThreadedPlugin(Plugin):
    """Runs setup / handle / tick / teardown on its own thread."""

    interval: ClassVar[float] = 0.05  # seconds between ticks

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _run(self):
        try:
            self.setup()
            while not self._stop.is_set():
                try:
                    action, arg = self._queue.get(timeout=self.interval)
                    if action:
                        try:
                            self.handle(action, arg)
                        except Exception:
                            self.log.exception("action %s failed", action)
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

    # override these ----------------------------------------------------
    def setup(self):
        pass

    def handle(self, action: str, arg: Any):
        raise NotImplementedError

    def tick(self):
        pass

    def teardown(self):
        pass
