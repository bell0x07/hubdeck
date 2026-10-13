"""Apple Shortcuts (macOS): run a Shortcut from a control, and show what a Shortcut
returns on keys.

    plugins:
      shortcuts:
        outputs:                       # each becomes the state shortcuts.<name>
          weather: {run: "Get weather", every: 600}

    keys:
      1: {action: {shortcuts.run: "Lights off"}}
      2: {action: {shortcuts.run: {name: "Set scene", input: movie}}}
      3: {action: {shortcuts.refresh: weather}, face: {text: "{shortcuts.weather}"}}

The other direction, a Shortcut driving hubdeck, is `hubdeck send` (see control.py).
"""

from __future__ import annotations

import time

from ..platforms import current
from .base import ThreadedPlugin
from .vars import check_name

DEFAULT_EVERY = 300
MIN_EVERY = 10


def _shortcut_arg(arg) -> tuple[str, str | None] | None:
    """(name, input) from {shortcuts.run: name} or {shortcuts.run: {name, input}}."""
    if isinstance(arg, str) and arg.strip():
        return arg, None
    if isinstance(arg, dict) and isinstance(arg.get("name"), str) and not set(arg) - {"name", "input"}:
        return arg["name"], None if arg.get("input") is None else str(arg["input"])
    return None


class ShortcutsPlugin(ThreadedPlugin):
    name = "shortcuts"
    platforms = ("darwin",)
    interval = 1.0
    actions = {
        "run": "run a Shortcut, e.g. {shortcuts.run: Lights off} or {shortcuts.run: {name: ..., input: ...}}",
        "refresh": "run an output Shortcut now, e.g. {shortcuts.refresh: weather}",
    }
    states = {}   # one per plugins.shortcuts.outputs entry

    @classmethod
    def check_config(cls, config):
        problems = [f"plugins.shortcuts: unknown setting {k!r} (only 'outputs')" for k in sorted(set(config) - {"outputs"})]
        outputs = config.get("outputs") or {}
        if not isinstance(outputs, dict):
            return problems + ["plugins.shortcuts.outputs takes name: {run: Shortcut name, every: seconds}"]
        for name, spec in outputs.items():
            where = f"plugins.shortcuts.outputs.{name}"
            if err := check_name(name):
                problems.append(f"{where}: {err}")
            if not isinstance(spec, dict) or not isinstance(spec.get("run"), str):
                problems.append(f"{where} needs run: <Shortcut name>")
                continue
            problems += [f"{where}: unknown setting {k!r}" for k in sorted(set(spec) - {"run", "every", "input"})]
            every = spec.get("every", DEFAULT_EVERY)
            if not isinstance(every, (int, float)) or every < MIN_EVERY:
                problems.append(f"{where}: every must be at least {MIN_EVERY} seconds")
        return problems

    @classmethod
    def check_state(cls, name, config, arg=None):
        outputs = config.get("outputs") or {}
        if name not in outputs:
            return (f"shortcuts has no output {name!r}: add it under plugins.shortcuts.outputs "
                    f"(defined: {', '.join(outputs) or 'none'})")
        if arg is not None:
            return f"shortcuts.{name} takes no [argument]"
        return None

    @classmethod
    def check_action(cls, action, arg):
        if action == "run" and _shortcut_arg(arg) is None:
            return "shortcuts.run needs a Shortcut name, or {name: ..., input: ...}"
        if action == "refresh":
            return check_name(arg)
        return None

    def setup(self):
        self.outputs: dict = self.config.get("outputs") or {}
        self.due = {name: 0.0 for name in self.outputs}
        self.problem: dict[str, str] = {}    # output -> last problem logged, so a schedule doesn't spam

    def _refresh(self, name: str):
        spec = self.outputs[name]
        self.due[name] = time.monotonic() + spec.get("every", DEFAULT_EVERY)
        try:
            text = current().run_shortcut(spec["run"], spec.get("input"))
        except OSError as e:
            if self.problem.get(name) != str(e):
                self.log.warning("output %s: %s (retrying every %gs)", name, e, spec.get("every", DEFAULT_EVERY))
                self.problem[name] = str(e)
            text = None
        else:
            if self.problem.pop(name, None):
                self.log.info("output %s works again", name)
        self.ctx.set_state(name, text)

    def tick(self):
        now = time.monotonic()
        for name, due in list(self.due.items()):
            if now >= due and not self.stopping:
                self._refresh(name)

    def handle(self, action, arg):
        if action == "run":
            name, data = _shortcut_arg(arg)
            current().run_shortcut(name, data)
            self.log.info("ran Shortcut %r", name)
        elif action == "refresh":
            if arg in self.outputs:
                self._refresh(arg)
            else:
                self.log.warning("refresh: no output %r under plugins.shortcuts.outputs", arg)
