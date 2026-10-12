"""Which window has keyboard focus, for switching pages by app (see Engine._poll_focus)."""

from __future__ import annotations

import logging
import threading

from .platforms import FocusedWindow, FocusReader, Unsupported, current

log = logging.getLogger("hubdeck")

POLL_SECONDS = 0.2


def supported() -> bool:
    """Whether this OS can tell hubdeck the focused app (for the tray and the editor)."""
    try:
        current().focus()
    except Unsupported:
        return False
    return True


class FocusWatcher:
    """Reads the focused window every POLL_SECONDS on a thread of its own: a read can block
    (macOS asks the app for its title), and that must never hold up the controls. The engine
    reads `current`: the latest window, or None (nothing to switch pages for)."""

    def __init__(self, reader: FocusReader, interval: float = POLL_SECONDS):
        self.reader = reader
        self.interval = interval
        self.current: FocusedWindow | None = None
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="hubdeck-focus", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        failing = False
        while not self._stop.is_set():
            try:
                self.current = self.reader.read()
                failing = False
            except Exception:
                (log.debug if failing else log.warning)("could not read the focused window", exc_info=True)
                failing = True
                self.current = None
            self._stop.wait(self.interval)
