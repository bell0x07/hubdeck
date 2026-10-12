"""Run Python code inside hubdeck: {python: "import winsound; winsound.Beep(440, 200)"}.

Every press runs its code on a new thread, so slow code never holds up other
keys. All snippets share one namespace, so variables set by one press are
still there on the next (until restart). `log` in that namespace writes to
hubdeck.log.
"""

from __future__ import annotations

import itertools
import threading

from .base import Plugin

FILENAME = "<python action>"


class PythonPlugin(Plugin):
    name = "python"
    actions = {
        "exec": "run Python code on its own thread, e.g. {python: 'import winsound; winsound.Beep(440, 200)'}; "
                "snippets share variables and can write to the log with `log.info(...)`",
    }

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self.namespace: dict = {"__name__": "__hubdeck__", "log": self.log}
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
        threading.Thread(target=self._exec, args=(arg,), name=f"hubdeck-python-{next(self._ids)}",
                         daemon=True).start()

    def _exec(self, code: str):
        try:
            exec(compile(code, FILENAME, "exec"), self.namespace)
        except Exception:
            self.log.exception("python action failed")
