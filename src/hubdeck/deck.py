"""Thread-safe control of a running Engine.

Python actions get it as `deck`, other plugins as `ctx.deck`. Every module is
reachable: deck.keys[n], deck.dial, deck.knobs[n], deck.rollers[n],
deck.faders[n] and deck.base, numbered like the config. Every call that
changes something is queued and applied by the engine thread on its next step
(within about 10 ms), in the order the calls were made. Bad arguments raise
straight away in the caller.
"""

from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import ValidationError

from .config.loader import format_validation_error
from .config.schema import CONTROL_COUNTS, GESTURES, KEY_COUNT, TEMPLATE_RE, ControlDef, Face, LightZone, parse_actions
from .render.faces import BLANK, RenderFace
from .render.images import LoadedImage, is_url, load_image

if TYPE_CHECKING:
    from .engine import Engine

MAX_IMAGE_SIZE = 480    # px; pictures made in code are kept in memory, scaled down to this
_UNSET = object()


def _fields(model, fields: dict, what: str) -> dict:
    """Validate runtime fields against a config model. Returns them with None kept (= reset)."""
    unknown = set(fields) - set(model.model_fields)
    if unknown:
        raise ValueError(f"unknown {what} field(s) {', '.join(sorted(unknown))} "
                         f"(available: {', '.join(model.model_fields)})")
    try:
        valid = model.model_validate({k: v for k, v in fields.items() if v is not None})
    except ValidationError as e:
        raise ValueError(f"bad {what}:\n" + format_validation_error(e)) from None
    return {**dict.fromkeys(fields), **valid.model_dump(exclude_unset=True)}


# -- controls ----------------------------------------------------------------
class Control:
    """One control of a module, numbered like the config (reading order as mounted)."""
    kind: ClassVar[str]
    single: ClassVar[bool] = False     # dial, base: one per module, no number

    def __init__(self, deck: Deck, number: int = 1):
        self._deck = deck
        self.number = number

    @property
    def _engine(self) -> Engine:
        return self._deck._engine

    @property
    def _id(self) -> tuple[str, int]:
        return self.kind, self.number

    def __repr__(self):
        return f"deck.{self.kind}" if self.single else f"deck.{self.kind}[{self.number}]"

    def __eq__(self, other):
        return isinstance(other, Control) and self._id == other._id

    def __hash__(self):
        return hash(self._id)

    def _def(self, page: str | None):
        page = self._deck._page(page)
        return page, self._engine.def_on(page, self._id)


class _Screen(Control):
    def set_face(self, page: str | None = None, **fields):
        """Change the face. Fields are the config's face fields (icon, filled, label, text, bg,
        fg, label_color, font_size, gauge, image, image_fit); `image` may also be a PIL image or
        image bytes. They go on top of the config's face; fields left out keep their value,
        None resets one to the config's."""
        page = self._deck._page(page)
        image = fields.pop("image", _UNSET)
        values = _fields(Face, fields, "face")
        if image is not _UNSET:
            values["image"] = None if image is None else self._deck._image(image)
        self._engine.set_face(page, self._id, values)

    def clear_face(self, page: str | None = None):
        """Go back to the config's face."""
        self._engine.set_face(self._deck._page(page), self._id, None)

    def face(self, page: str | None = None) -> RenderFace | None:
        """What it shows now (or would show, on a page not on screen), with live text filled in:
        .icon, .filled, .label, .text, .bg, .fg, .label_color (as (r, g, b)), .font_size, .image
        and .image_fit. None if hubdeck doesn't draw it on that page."""
        face = self._engine.screen_face(self._id, self._deck._page(page))
        return None if face is BLANK else face


class _Pressable(Control):
    def press(self, page: str | None = None, gesture: str = "press"):
        """Run the actions the config gives it, as if it was pressed. `gesture` picks which:
        press (on_press / action), down, up, long_press or double_press."""
        if gesture not in GESTURES:
            raise ValueError(f"gesture must be one of {', '.join(GESTURES)}, not {gesture!r}")
        page, d = self._def(page)
        if d is None or not d.actions(gesture):
            raise ValueError(f"{self!r} on page {page!r} has no on_{gesture} actions")
        self._engine.press(page, self._id, gesture)

    @property
    def held(self) -> bool:
        """Whether it's held down right now."""
        return self._engine.is_held(self._id)


class _Turnable(Control):
    def turn(self, steps: int = 1, page: str | None = None):
        """Act as if it was turned `steps` clicks: positive = clockwise (rollers: up or right).
        Adjusts its turn target, or runs its cw/ccw actions once per click."""
        if isinstance(steps, bool) or not isinstance(steps, int) or steps == 0:
            raise ValueError(f"steps must be a whole number other than 0, not {steps!r}")
        page, d = self._def(page)
        if not isinstance(d, ControlDef) or d.turn is None:
            raise ValueError(f"{self!r} has no 'turn' on page {page!r}")
        self._engine.turn(page, self._id, steps)

    def value(self, page: str | None = None) -> Any:
        """Current value (0-100) of its turn target, or None (no target, or unknown yet)."""
        return self._engine.control_value(self._deck._page(page), self._id)


class _Lit(Control):
    def set_light(self, **fields):
        """Change its LED, with the fields of a `lighting:` zone (color, mode, brightness,
        min_brightness, animate, state, on, unknown). They go on top of the config's lighting
        for it, on every page; fields left out keep their value, None resets one."""
        values = _fields(LightZone, fields, "lighting")
        if self.kind == "base":
            lights = [values] + [values.get(k) or {} for k in ("on", "unknown")]
            if any(light.get("brightness") == "value" for light in lights):
                raise ValueError("base: brightness 'value' needs a knob or fader")
        self._engine.set_light(self._id, values)

    def clear_light(self):
        """Go back to the config's lighting; an LED the config doesn't light is turned off."""
        self._engine.set_light(self._id, None)


class Key(_Screen, _Pressable):
    kind = "keys"


class Dial(_Screen, _Pressable, _Turnable):
    kind = "dial"
    single = True


class Knob(_Pressable, _Turnable, _Lit):
    kind = "knobs"


class Roller(_Turnable):
    kind = "rollers"


class Fader(_Lit):
    kind = "faders"

    @property
    def position(self) -> int | None:
        """Where it sits, 0-100 (up, or right when mounted sideways, = 100); None until it has
        reported after connecting."""
        return self._engine.fader_position(self.number)

    def value(self, page: str | None = None) -> Any:
        """Current value (0-100) of what it controls, or None (no fader there, or unknown yet)."""
        return self._engine.control_value(self._deck._page(page), self._id)

    def move_to(self, value: float, page: str | None = None):
        """Set what it controls to `value` (0-100), as if moved there. The physical fader then
        has to reach that value before it takes over again."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError(f"value must be 0-100, not {value!r}")
        page, d = self._def(page)
        if d is None:
            raise ValueError(f"{self!r} has no target on page {page!r}")
        self._engine.move_fader(page, self.number, value)


class Base(_Lit):
    kind = "base"
    single = True


class Controls:
    """deck.keys / knobs / rollers / faders: index by config number (1-based), or iterate."""

    def __init__(self, deck: Deck, cls: type[Control], count: int):
        self._deck, self._cls, self._count = deck, cls, count

    def __getitem__(self, number: int) -> Control:
        if isinstance(number, bool) or not isinstance(number, int):
            raise TypeError(f"{self._cls.kind} are numbered 1-{self._count}, not {number!r}")
        if not 1 <= number <= self._count:
            raise IndexError(f"{self._cls.kind} are numbered 1-{self._count}, not {number}")
        return self._cls(self._deck, number)

    def __iter__(self):
        return (self[n] for n in range(1, self._count + 1))

    def __len__(self):
        return self._count

    def __repr__(self):
        return f"deck.{self._cls.kind} (1-{self._count})"


# -- deck ----------------------------------------------------------------------
class Deck:
    def __init__(self, engine: Engine):
        self._engine = engine
        self._local = threading.local()
        self.keys = Controls(self, Key, KEY_COUNT)
        self.dial = Dial(self)
        self.knobs = Controls(self, Knob, CONTROL_COUNTS["knobs"])
        self.rollers = Controls(self, Roller, CONTROL_COUNTS["rollers"])
        self.faders = Controls(self, Fader, CONTROL_COUNTS["faders"])
        self.base = Base(self)

    # -- what started the calling code -----------------------------------
    def bind(self, source: tuple[str, str, int] | None):
        """Remember, for the current thread, the (page, module, number) that started the code."""
        self._local.source = source

    @property
    def firing(self) -> tuple[str, str, int] | None:
        """Engine thread only: where the action being invoked right now comes from."""
        return self._engine.firing

    @property
    def control(self) -> Control | None:
        """The key, dial, knob or roller whose press or turn started this code, or None."""
        source = self._source()
        if source is None:
            return None
        _, kind, number = source
        single = {"dial": self.dial, "base": self.base}
        return single[kind] if kind in single else getattr(self, kind)[number]

    @property
    def key(self) -> int | None:
        """Number of the key whose press started this code, or None."""
        source = self._source()
        return source[2] if source and source[1] == "keys" else None

    # -- pages -----------------------------------------------------------
    @property
    def page(self) -> str | None:
        """The page on screen."""
        return self._engine.current_page

    @property
    def pages(self) -> list[str]:
        return self._engine.page_names

    def goto(self, page: str):
        self._engine.goto_page(self._page(page))

    def back(self):
        self._engine.back()

    def home(self):
        self._engine.home()

    # -- keys (shortcuts for deck.keys[n]) -------------------------------
    def set_face(self, key: int, page: str | None = None, **fields):
        self.keys[key].set_face(page, **fields)

    def clear_face(self, key: int, page: str | None = None):
        self.keys[key].clear_face(page)

    def clear_faces(self, page: str | None = None):
        """Go back to the config's faces for every key and the dial on a page."""
        self._engine.set_face(self._page(page), None, None)

    def face(self, key: int, page: str | None = None) -> RenderFace | None:
        return self.keys[key].face(page)

    def press(self, key: int, page: str | None = None, gesture: str = "press"):
        self.keys[key].press(page, gesture)

    def is_held(self, key: int) -> bool:
        return self.keys[key].held

    # -- actions and state -----------------------------------------------
    def run(self, action: Any, arg: Any = None):
        """Run actions written as in the config: run("discord.toggle_mute"), run("keys", "ctrl+c"),
        run({"audio.set_volume": 30}) or a list of them. Starts plugins the config doesn't use."""
        if isinstance(action, str):
            action = {action: arg}
        elif arg is not None:
            raise TypeError("put the argument inside the action, e.g. deck.run({'keys': 'ctrl+c'})")
        actions = parse_actions(action)
        for a in actions:
            if err := self._engine.check_runnable(a, start=True):
                raise ValueError(err)
        for a in actions:
            self._engine.run_action(a, self._source(), start=True)

    def state(self, name: str, default: Any = None) -> Any:
        """A state value, e.g. state("audio.volume"); `default` while it's unknown."""
        value = self._engine.state.get(name)
        return default if value is None else value

    @property
    def states(self) -> dict[str, Any]:
        """Every known state value, by plugin.state name."""
        return dict(self._engine.state)

    def set_var(self, name: str, value: Any):
        """Set vars.<name>, like {vars.set: {name: value}} or `hubdeck send set`: faces show it as
        {vars.name}, and `state: vars.name` switches on it."""
        self.run({"vars.set": {name: value}})

    # -- device ----------------------------------------------------------
    def set_brightness(self, percent: int, module: str = "keys"):
        """Screen backlight 0-100 for keys or dial, until the config's brightness changes."""
        if module not in ("keys", "dial"):
            raise ValueError(f"module must be keys or dial, not {module!r}")
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
            raise ValueError(f"brightness must be 0-100, not {percent!r}")
        self._engine.set_brightness(module, round(percent))

    def reload(self):
        """Reload the config file."""
        self._engine.reload()

    # -- helpers ---------------------------------------------------------
    def _source(self) -> tuple[str, str, int] | None:
        return getattr(self._local, "source", None)

    def _page(self, page: str | None) -> str:
        """Default: the page of the control that started this code, else the page on screen."""
        if page is None:
            source = self._source()
            page = source[0] if source else self._engine.current_page
        if page not in self._engine.page_names:
            raise ValueError(f"no page named {page!r} (pages: {', '.join(self.pages)})")
        return page

    def _image(self, src: Any) -> str | LoadedImage:
        """A face image: a file (relative = next to the config), URL or {template} as a string,
        or a picture made in code (PIL image, image bytes) loaded into memory."""
        from PIL import Image
        if isinstance(src, (str, os.PathLike)):
            src = str(src)
            if is_url(src) or TEMPLATE_RE.search(src):
                return src
            path = Path(os.path.expandvars(src)).expanduser()
            if not path.is_absolute() and self._engine.source is not None:
                path = self._engine.source.path.parent / path
            if not path.is_file():
                raise FileNotFoundError(f"image {src!r} not found ({path})")
            return str(path)
        if isinstance(src, Image.Image):
            loaded = LoadedImage([src.convert("RGBA")], [0.1])
        elif isinstance(src, (bytes, bytearray)):
            loaded = load_image(io.BytesIO(src))
        else:
            raise TypeError(f"image must be a file, URL, PIL image or image bytes, not {type(src).__name__}")
        for frame in loaded.frames:
            frame.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE))
        return loaded
