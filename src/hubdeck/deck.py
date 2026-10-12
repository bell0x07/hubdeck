"""Thread-safe control of a running Engine.

Python actions get it as `deck`, other plugins as `ctx.deck`. Every call that
changes something is queued and applied by the engine thread on its next step
(within about 10 ms), in the order the calls were made. Bad arguments raise
straight away in the caller.
"""

from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .config.loader import format_validation_error
from .config.schema import KEY_COUNT, TEMPLATE_RE, Face, parse_actions
from .render.images import LoadedImage, is_url, load_image

if TYPE_CHECKING:
    from .engine import Engine

MAX_IMAGE_SIZE = 480    # px; pictures made in code are kept in memory, scaled down to this
SCREENS = ("keys", "dial")
_UNSET = object()


class Deck:
    def __init__(self, engine: Engine):
        self._engine = engine
        self._local = threading.local()

    # -- which key started the calling code ------------------------------
    def bind(self, source: tuple[str, int | None] | None):
        """Remember, for the current thread, the (page, key) that started the code running on it."""
        self._local.source = source

    @property
    def firing(self) -> tuple[str, int | None] | None:
        """Engine thread only: the (page, key) whose action is being invoked right now."""
        return self._engine.firing

    @property
    def key(self) -> int | None:
        """Number of the key whose press started this code, or None."""
        source = self._source()
        return source[1] if source else None

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

    # -- faces -----------------------------------------------------------
    def set_face(self, key: int, page: str | None = None, **fields):
        """Change a key's face. Fields are the config's face fields (icon, filled, label, text,
        bg, fg, label_color, font_size, image, image_fit). `image` may also be a PIL image or
        image bytes. They go on top of the config's face; fields left out keep their value,
        None resets one to the config's."""
        num, page = self._key(key), self._page(page)
        unknown = set(fields) - set(Face.model_fields)
        if unknown:
            raise ValueError(f"unknown face field(s) {', '.join(sorted(unknown))} "
                             f"(available: {', '.join(Face.model_fields)})")
        image = fields.pop("image", _UNSET)
        try:
            face = Face.model_validate({k: v for k, v in fields.items() if v is not None})
        except ValidationError as e:
            raise ValueError("bad face:\n" + format_validation_error(e)) from None
        values = {**dict.fromkeys(fields), **face.model_dump(exclude_unset=True)}  # None = reset
        if image is not _UNSET:
            values["image"] = None if image is None else self._image(image)
        self._engine.set_face(page, num, values)

    def clear_face(self, key: int, page: str | None = None):
        """Go back to the config's face for one key."""
        self._engine.set_face(self._page(page), self._key(key), None)

    def clear_faces(self, page: str | None = None):
        """Go back to the config's faces for every key on a page."""
        self._engine.set_face(self._page(page), None, None)

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
        if module not in SCREENS:
            raise ValueError(f"module must be one of {', '.join(SCREENS)}, not {module!r}")
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
            raise ValueError(f"brightness must be 0-100, not {percent!r}")
        self._engine.set_brightness(module, round(percent))

    def reload(self):
        """Reload the config file."""
        self._engine.reload()

    # -- helpers ---------------------------------------------------------
    def _source(self) -> tuple[str, int | None] | None:
        return getattr(self._local, "source", None)

    def _key(self, key: Any) -> int:
        if isinstance(key, bool) or not isinstance(key, int) or not 1 <= key <= KEY_COUNT:
            raise ValueError(f"key must be a number 1-{KEY_COUNT}, not {key!r}")
        return key

    def _page(self, page: str | None) -> str:
        """Default: the page of the key that started this code, else the page on screen."""
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
