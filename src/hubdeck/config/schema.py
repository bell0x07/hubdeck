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
# {value} or {value:format}: the current value of the control's turn target
VALUE_RE = re.compile(r"\{value(?::([^{}]*))?\}")
NAME_RE = r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*"
CONTROL_COUNTS = {"knobs": 3, "rollers": 2, "faders": 5}

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
    gauge: bool = False   # ring showing the control's {value} (0-100); for the dial
    image: str | None = None                          # file (relative to the config folder) or http(s) URL
    image_fit: Literal["cover", "contain"] = "cover"  # cover: fill and crop; contain: fit inside

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


class TurnDef(_Model):
    """What turning a dial/knob/roller does: adjust a plugin value, or run actions per step."""
    target: str | None = None          # plugin.value, e.g. audio.volume
    arg: Any = None                    # e.g. the app for audio.app_volume
    step: float = Field(default=2, gt=0)
    invert: bool = False
    cw: list[Action] = []              # actions per clockwise / up step
    ccw: list[Action] = []

    @field_validator("cw", "ccw", mode="before")
    @classmethod
    def _actions(cls, v):
        return parse_actions(v)

    @field_validator("target")
    @classmethod
    def _target_name(cls, v):
        if v is not None and not re.fullmatch(NAME_RE, v):
            raise ValueError(f"target {v!r} must look like plugin.value (e.g. audio.volume)")
        return v

    @model_validator(mode="after")
    def _something(self):
        if not self.target and not (self.cw or self.ccw):
            raise ValueError("turn needs a 'target' (e.g. audio.volume) or 'cw'/'ccw' actions")
        if self.target and (self.cw or self.ccw):
            raise ValueError("use either 'target' or 'cw'/'ccw', not both")
        return self


class ControlDef(KeyDef):
    """A dial, knob or roller: press gestures like a key, plus turning."""
    turn: TurnDef | None = None

    @model_validator(mode="before")
    @classmethod
    def _turn_shorthand(cls, data):
        if isinstance(data, dict) and isinstance(data.get("turn"), str):
            data = dict(data)
            data["turn"] = {"target": data["turn"]}
        return data

    def all_actions(self) -> list[Action]:
        extra = (self.turn.cw + self.turn.ccw) if self.turn else []
        return super().all_actions() + extra

    def press_actions(self) -> list[Action]:
        return super().all_actions()


class FaderDef(_Model):
    target: str
    arg: Any = None
    invert: bool = False

    @field_validator("target")
    @classmethod
    def _target_name(cls, v):
        if not re.fullmatch(NAME_RE, v):
            raise ValueError(f"target {v!r} must look like plugin.value (e.g. audio.volume)")
        return v


def _check_range(v: dict, count: int, what: str):
    bad = [k for k in v if not 1 <= k <= count]
    if bad:
        raise ValueError(f"{what} numbers must be 1-{count}, got {bad}")
    return v


class Page(_Model):
    keys: dict[int, KeyDef] = {}
    dial: ControlDef | None = None
    knobs: dict[int, ControlDef] = {}
    rollers: dict[int, ControlDef] = {}
    faders: dict[int, FaderDef] = {}
    match: dict[str, Any] | None = None  # reserved: switch to this page when an app is focused

    @field_validator("keys")
    @classmethod
    def _key_range(cls, v):
        return _check_range(v, KEY_COUNT, "key")

    @field_validator("knobs", "rollers", "faders")
    @classmethod
    def _control_range(cls, v, info):
        return _check_range(v, CONTROL_COUNTS[info.field_name], info.field_name[:-1])

    @field_validator("rollers")
    @classmethod
    def _rollers_cannot_press(cls, v):
        for num, cd in v.items():
            if cd.press_actions():
                raise ValueError(f"roller {num}: rollers can't be pressed; only 'turn' is supported")
        return v


LED_MODES = ("off", "static", "breathing", "strobe", "spring", "cycling", "random", "wave", "water", "rainbow")
LIGHT_ZONES = {"base": 1, "knobs": 3, "faders": 5}


class Animate(_Model):
    """Software animation: hubdeck computes the colour itself (~30 updates/s)."""
    effect: Literal["pulse", "blink", "cycle"]
    colors: list[str] = []          # pulse/blink: [on, off] (off defaults to black); cycle: 2+ colours
    period: float = Field(default=2.0, gt=0.1)   # seconds per cycle

    @field_validator("colors")
    @classmethod
    def _colors(cls, v):
        for c in v:
            _check_color(c)
        return v

    @model_validator(mode="after")
    def _enough(self):
        if not self.colors:
            raise ValueError("animate needs 'colors'")
        if self.effect == "cycle" and len(self.colors) < 2:
            raise ValueError("animate effect 'cycle' needs at least 2 colors")
        return self


class Light(_Model):
    mode: Literal[LED_MODES] | None = None
    color: str | None = None
    brightness: int | Literal["value"] | None = None    # "value": follow the control's turn/fader target
    min_brightness: int = Field(default=10, ge=0, le=100)
    animate: Animate | None = None

    @field_validator("color")
    @classmethod
    def _color(cls, v):
        return _check_color(v)

    @field_validator("brightness")
    @classmethod
    def _range(cls, v):
        if isinstance(v, int) and not 0 <= v <= 100:
            raise ValueError("brightness must be 0-100 or 'value'")
        return v


class LightZone(Light):
    state: str | None = None
    on: Light | None = None          # used while `state` is truthy
    unknown: Light | None = None     # used while `state` is unknown

    @field_validator("state")
    @classmethod
    def _state_name(cls, v):
        if v is not None and not re.fullmatch(NAME_RE, v):
            raise ValueError(f"state {v!r} must look like plugin.state (e.g. discord.muted)")
        return v


class Lighting(_Model):
    base: LightZone | None = None
    knobs: dict[str, LightZone] = {}     # "all" and/or 1-3
    faders: dict[str, LightZone] = {}    # "all" and/or 1-5

    @field_validator("knobs", "faders", mode="before")
    @classmethod
    def _keys(cls, v, info):
        count = LIGHT_ZONES[info.field_name]
        out = {}
        for k, zone in (v or {}).items():
            key = str(k).lower()
            if key != "all" and not (key.isdigit() and 1 <= int(key) <= count):
                raise ValueError(f"{info.field_name}: use 'all' or 1-{count}, got {k!r}")
            out[key] = zone
        return out

    @field_validator("base")
    @classmethod
    def _base_has_no_value(cls, v):
        for light in (v, v and v.on, v and v.unknown):
            if light is not None and light.brightness == "value":
                raise ValueError("base: brightness 'value' needs a knob or fader")
        return v

    def zones(self) -> list[tuple[str, int, LightZone]]:
        """(module, 0-based index, merged zone) for every configured zone; 'all' merged under the number."""
        out = []
        if self.base:
            out.append(("base", 0, self.base))
        for kind in ("knobs", "faders"):
            section = getattr(self, kind)
            if not section:
                continue
            for i in range(LIGHT_ZONES[kind]):
                parts = [z for z in (section.get("all"), section.get(str(i + 1))) if z is not None]
                if parts:
                    merged = {}
                    for z in parts:
                        merged.update(z.model_dump(exclude_unset=True))
                    out.append((kind, i, LightZone.model_validate(merged)))
        return out


class Device(_Model):
    ownership: Literal["shared", "exclusive"] = "shared"
    reassert_seconds: float = Field(default=5, ge=0)
    orientation: Literal["horizontal", "vertical"] = "horizontal"   # the Base: 6x4 or 4x6 cells (editor layout)


Rotation = Literal[0, 90, 180, 270]


class ModuleSettings(_Model):
    rotate: Rotation = 0   # clockwise, from the module's default mounting
    position: tuple[int, int] | None = None   # top-left cell on the Base [column, row] (editor layout)


class ScreenSettings(ModuleSettings):
    brightness: int | None = Field(default=None, ge=0, le=100)   # backlight; null = leave as is


class Modules(_Model):
    """Per-module mounting and settings."""
    keys: ScreenSettings = ScreenSettings()
    dial: ScreenSettings = ScreenSettings()
    knobs: ModuleSettings = ModuleSettings()
    rollers: ModuleSettings = ModuleSettings()
    faders: ModuleSettings = ModuleSettings()

    def rotate(self, kind: str) -> int:
        settings = getattr(self, kind, None)
        return settings.rotate if settings is not None else 0


class Defaults(_Model):
    long_press_ms: int = Field(default=500, ge=100)
    double_press_ms: int = Field(default=250, ge=50)
    style: Style = Style(bg="#19191e", fg="#3cc86e", label_color="#ffffff")
    led_gamma: float = Field(default=2.2, ge=1.0, le=4.0)   # LED colour/brightness correction (1 = off)
    animation_fps: int = Field(default=30, ge=1, le=60)     # cap for GIF faces


class Config(_Model):
    device: Device = Device()
    modules: Modules = Modules()
    defaults: Defaults = Defaults()
    plugins: dict[str, dict[str, Any]] = {}
    lighting: Lighting = Lighting()
    start_page: str = "main"
    pages: dict[str, Page] = {}

    @model_validator(mode="before")
    @classmethod
    def _single_page_shorthand(cls, data):
        sections = ("keys", "dial", "knobs", "rollers", "faders")
        if isinstance(data, dict) and any(s in data for s in sections):
            if "pages" in data:
                raise ValueError("use either top-level keys/dial/knobs/rollers/faders (single page) "
                                 "or 'pages', not both")
            data = dict(data)
            data["pages"] = {data.get("start_page", "main"): {s: data.pop(s) for s in sections if s in data}}
        return data

    @field_validator("plugins", mode="before")
    @classmethod
    def _plugin_null(cls, v):
        # `discord:` with nothing under it means "enabled with defaults"
        return {k: (c or {}) for k, c in (v or {}).items()}

    @model_validator(mode="after")
    def _pages(self):
        if not self.pages:
            raise ValueError("nothing defined: add a 'keys:' or 'pages:' section")
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

    def iter_defs(self):
        """(config path, KeyDef or ControlDef) for every key, dial, knob and roller."""
        for page_name, page in self.pages.items():
            base = f"pages.{page_name}"
            for num, kd in page.keys.items():
                yield f"{base}.keys.{num}", kd
            if page.dial:
                yield f"{base}.dial", page.dial
            for section in ("knobs", "rollers"):
                for num, cd in getattr(page, section).items():
                    yield f"{base}.{section}.{num}", cd

    def iter_targets(self):
        """(config path, target, arg) for every turn target and fader."""
        for where, d in self.iter_defs():
            if isinstance(d, ControlDef) and d.turn and d.turn.target:
                yield f"{where}.turn", d.turn.target, d.turn.arg
        for page_name, page in self.pages.items():
            for num, fd in page.faders.items():
                yield f"pages.{page_name}.faders.{num}", fd.target, fd.arg

    def section(self, page: str, name: str):
        """A page's dial/knobs/rollers/faders section; inherited from start_page when the page has none."""
        own = getattr(self.pages[page], name)
        return own if own else getattr(self.pages[self.start_page], name)

    def used_modules(self) -> set[str]:
        kinds = set()
        if self.device.ownership == "exclusive" or any(p.keys for p in self.pages.values()):
            kinds.add("keys")
        for name in ("dial", "knobs", "rollers", "faders"):
            if any(getattr(p, name) for p in self.pages.values()):
                kinds.add(name)
        kinds.update(kind for kind, _, _ in self.lighting.zones())
        return kinds

    def state_refs(self) -> set[tuple[str, str]]:
        refs = set()
        for _, kd in self.iter_defs():
            if kd.state:
                refs.add(tuple(kd.state.split(".", 1)))
            for f in kd.faces():
                for s in (f.text, f.label):
                    if s:
                        refs.update((p, n) for p, n, _ in TEMPLATE_RE.findall(s))
        for _, _, zone in self.lighting.zones():
            if zone.state:
                refs.add(tuple(zone.state.split(".", 1)))
        return refs

    def used_plugins(self) -> set[str]:
        names = set(self.plugins)
        names.update(a.plugin for _, kd in self.iter_defs() for a in kd.all_actions())
        names.update(p for p, _ in self.state_refs())
        names.update(t.split(".", 1)[0] for _, t, _ in self.iter_targets())
        return names
