"""Values set from outside or by keys, shown and used like any state.

Shortcuts and scripts set them with `hubdeck send set <name> <value>`, keys with
{vars.set: {name: value}} or {vars.toggle: name}. Use them as {vars.<name>} in
text and labels, or as `state: vars.<name>` for faces and lighting. Starting
values go under plugins.vars; a name that was never set is unknown.
"""

from __future__ import annotations

import re

from .base import Plugin

NAME = re.compile(r"[a-z_][a-z0-9_]*")
SCALARS = (str, int, float, bool)


def check_name(name) -> str | None:
    if not (isinstance(name, str) and NAME.fullmatch(name)):
        return f"vars: {name!r} is not a valid name (lowercase letters, digits and _, e.g. focus_mode)"
    return None


def _check_values(values, where: str) -> list[str]:
    if not isinstance(values, dict):
        return [f"{where} takes name: value pairs, e.g. {{mode: work}}"]
    problems = [p for p in map(check_name, values) if p]
    problems += [f"{where}: {k} must be text, a number or true/false" for k, v in values.items()
                 if not isinstance(v, SCALARS)]
    return problems


class VarsPlugin(Plugin):
    name = "vars"
    actions = {
        "set": "set values, e.g. {vars.set: {mode: work}}",
        "toggle": "flip a true/false value, e.g. {vars.toggle: dnd}",
    }
    states = {}   # any valid name: whatever is set

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self.values: dict = {}

    @classmethod
    def check_config(cls, config):
        return _check_values(config, "plugins.vars")

    @classmethod
    def check_state(cls, name, config, arg=None):
        return check_name(name) or (f"vars.{name} takes no [argument]" if arg is not None else None)

    @classmethod
    def check_action(cls, action, arg):
        if action == "set":
            problems = _check_values(arg, "vars.set")
            return problems[0] if problems else None
        if action == "toggle":
            return check_name(arg)
        return None

    def start(self):
        for name, value in self.config.items():
            self._set(name, value)

    def _set(self, name: str, value):
        self.values[name] = value
        self.ctx.set_state(name, value)

    def invoke(self, action, arg):
        if action == "set":
            for name, value in arg.items():
                self._set(name, value)
        elif action == "toggle":
            self._set(arg, not self.values.get(arg))
