"""Config models. See README.md for the user-facing description."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..render import tabler
from ..render.faces import parse_color

KEY_COUNT = 15
GESTURES = ("press", "down", "up", "long_press", "double_press")

# {plugin.state} or {plugin.state:format} inside face text / label
TEMPLATE_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)(?::([^{}]*))?\}")

# short action names: {page: media}, {keys: "ctrl+c"}, {run: "notepad"}, ...
ALIASES: dict[str, tuple[str, str]] = {
    "page": ("page", "goto"),
    "keys": ("keyboard", "keys"),
    "type": ("keyboard", "type"),
    "media": ("keyboard", "media"),
    "run": ("launch", "run"),
    "open": ("launch", "open"),
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)


class Action(BaseModel):
    model_config = ConfigDict(frozen=True)
    plugin: str
    name: str
    arg: Any = None

    def __str__(self):
        return f"{self.plugin}.{self.name}" + (f"({self.arg!r})" if self.arg is not None else "")


def parse_action(raw: Any) -> Action:
    if isinstance(raw, Action):
        return raw
    if isinstance(raw, str):
        if "." not in raw:
            raise ValueError(f"action {raw!r} must look like plugin.action (e.g. discord.toggle_mute)")
        plugin, name = raw.split(".", 1)
        return Action(plugin=plugin, name=name)
    if isinstance(raw, dict) and len(raw) == 1:
        (key, arg), = raw.items()
        key = str(key)
        if key in ALIASES:
            plugin, name = ALIASES[key]
        elif "." in key:
            plugin, name = key.split(".", 1)
        else:
            raise ValueError(f"unknown action {key!r}; use plugin.action or one of: {', '.join(ALIASES)}")
        return Action(plugin=plugin, name=name, arg=arg)
    raise ValueError(f"cannot read action {raw!r}; expected 'plugin.action' or {{plugin.action: argument}}")


def parse_actions(raw: Any) -> list[Action]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [parse_action(r) for r in raw]
    return [parse_action(raw)]


def _check_color(v: str | None) -> str | None:
    if v is not None:
        try:
            parse_color(v)
        except ValueError:
            raise ValueError(f"unknown colour {v!r} (use #rrggbb or a name like red)")
    return v


class Style(_Model):
    bg: str | None = None
    fg: str | None = None
    label_color: str | None = None

    @field_validator("bg", "fg", "label_color")
    @classmethod
    def _colors(cls, v):
        return _check_color(v)


class Face(Style):
    icon: str | None = None
    filled: bool = False
    label: str | None = None
    text: str | None = None
    font_size: int | None = Field(default=None, ge=4, le=72)

    @model_validator(mode="after")
    def _known_icon(self):
        if self.icon and tabler.codepoint(self.icon, self.filled) is None:
            hint = tabler.suggest(self.icon, self.filled)
            style = "filled" if self.filled else "outline"
            msg = f"unknown {style} Tabler icon {self.icon!r}"
            if hint:
                msg += f" (did you mean {', '.join(hint)}?)"
            raise ValueError(msg + " - search with: hubdeck icons <word>")
        return self


class KeyDef(_Model):
    on_press: list[Action] = []
    on_down: list[Action] = []
    on_up: list[Action] = []
    on_long_press: list[Action] = []
    on_double_press: list[Action] = []
    state: str | None = None
    face: Face = Face()
    face_on: Face | None = None        # used while `state` is truthy
    face_unknown: Face | None = None   # used while `state` is unknown (plugin offline)

    @model_validator(mode="before")
    @classmethod
    def _action_shorthand(cls, data):
        if isinstance(data, dict) and "action" in data:
            if "on_press" in data:
                raise ValueError("use either 'action' or 'on_press', not both")
            data = dict(data)
            data["on_press"] = data.pop("action")
        return data

    @field_validator("on_press", "on_down", "on_up", "on_long_press", "on_double_press", mode="before")
    @classmethod
    def _actions(cls, v):
        return parse_actions(v)

    @field_validator("state")
    @classmethod
    def _state_name(cls, v):
        if v is not None and not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", v):
            raise ValueError(f"state {v!r} must look like plugin.state (e.g. discord.muted)")
        return v

    def actions(self, gesture: str) -> list[Action]:
        return getattr(self, f"on_{gesture}")

    def all_actions(self) -> list[Action]:
        return [a for g in GESTURES for a in self.actions(g)]

    def faces(self) -> list[Face]:
        return [f for f in (self.face, self.face_on, self.face_unknown) if f is not None]


class Page(_Model):
    keys: dict[int, KeyDef] = {}
    match: dict[str, Any] | None = None  # reserved: switch to this page when an app is focused

    @field_validator("keys")
    @classmethod
    def _key_range(cls, v):
        bad = [k for k in v if not 1 <= k <= KEY_COUNT]
        if bad:
            raise ValueError(f"key numbers must be 1-{KEY_COUNT}, got {bad}")
        return v


class Device(_Model):
    ownership: Literal["shared", "exclusive"] = "shared"
    rotate: Literal[0, 90, 180, 270] = 0
    brightness: int | None = Field(default=None, ge=0, le=100)
    reassert_seconds: float = Field(default=5, ge=0)


class Defaults(_Model):
    long_press_ms: int = Field(default=500, ge=100)
    double_press_ms: int = Field(default=250, ge=50)
    style: Style = Style(bg="#19191e", fg="#3cc86e", label_color="#ffffff")


class Config(_Model):
    device: Device = Device()
    defaults: Defaults = Defaults()
    plugins: dict[str, dict[str, Any]] = {}
    start_page: str = "main"
    pages: dict[str, Page] = {}

    @model_validator(mode="before")
    @classmethod
    def _single_page_shorthand(cls, data):
        if isinstance(data, dict) and "keys" in data:
            if "pages" in data:
                raise ValueError("use either top-level 'keys' (single page) or 'pages', not both")
            data = dict(data)
            data["pages"] = {data.get("start_page", "main"): {"keys": data.pop("keys")}}
        return data

    @field_validator("plugins", mode="before")
    @classmethod
    def _plugin_null(cls, v):
        # `discord:` with nothing under it means "enabled with defaults"
        return {k: (c or {}) for k, c in (v or {}).items()}

    @model_validator(mode="after")
    def _pages(self):
        if not self.pages:
            raise ValueError("no keys defined: add a 'keys:' or 'pages:' section")
        if self.start_page not in self.pages:
            raise ValueError(f"start_page {self.start_page!r} is not one of the pages: {', '.join(self.pages)}")
        return self

    # -- helpers used by the engine ---------------------------------------
    def owned_keys(self) -> set[int]:
        """1-based key numbers hubdeck paints."""
        if self.device.ownership == "exclusive":
            return set(range(1, KEY_COUNT + 1))
        return {k for p in self.pages.values() for k in p.keys}

    def iter_keys(self):
        for page_name, page in self.pages.items():
            for num, kd in page.keys.items():
                yield page_name, num, kd

    def state_refs(self) -> set[tuple[str, str]]:
        refs = set()
        for _, _, kd in self.iter_keys():
            if kd.state:
                refs.add(tuple(kd.state.split(".", 1)))
            for f in kd.faces():
                for s in (f.text, f.label):
                    if s:
                        refs.update((p, n) for p, n, _ in TEMPLATE_RE.findall(s))
        return refs

    def used_plugins(self) -> set[str]:
        names = set(self.plugins)
        names.update(a.plugin for _, _, kd in self.iter_keys() for a in kd.all_actions())
        names.update(p for p, _ in self.state_refs())
        return names
