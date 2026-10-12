"""Run Python code inside hubdeck: {python: "import winsound; winsound.Beep(440, 200)"}.

Every press runs its code on a new thread, so slow code never holds up other
keys. All snippets share one namespace, so variables set by one press are
still there on the next (until restart). The namespace has `log` (writes to
hubdeck.log) and `deck` (pages, faces, actions, state and the device; see
deck.py).
"""

from __future__ import annotations

import itertools
import threading

from .base import Plugin

FILENAME = "<python action>"


class PythonPlugin(Plugin):
    name = "python"
    actions = {
        "exec": "run Python code on its own thread, e.g. {python: 'deck.set_face(deck.key, label=\"hi\")'}; "
                "snippets share variables and get `deck` and `log`",
    }

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self.namespace: dict = {"__name__": "__hubdeck__", "log": self.log, "deck": ctx.deck}
        self._ids = itertools.count(1)

    @classmethod
    def check_action(cls, action, arg):
        if not isinstance(arg, str):
            return "python.exec needs code, e.g. {python: 'print(1)'}"
        try:
            compile(arg, FILENAME, "exec")
        except (SyntaxError, ValueError) as e:
            where = f" (line {e.lineno})" if getattr(e, "lineno", None) else ""
            return f"python: {getattr(e, 'msg', e)}{where}"
        return None

    def invoke(self, action, arg):
        source = self.ctx.deck.firing  # read here, on the engine thread
        threading.Thread(target=self._exec, args=(arg, source), name=f"hubdeck-python-{next(self._ids)}",
                         daemon=True).start()

    def _exec(self, code: str, source: tuple[str, int] | None):
        self.ctx.deck.bind(source)
        try:
            exec(compile(code, FILENAME, "exec"), self.namespace)
        except Exception:
            self.log.exception("python action failed")
