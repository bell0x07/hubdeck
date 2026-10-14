"""The main loop: modules <-> gestures/turns/faders <-> actions/plugins <-> faces."""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .config import Config, ConfigError, ConfigSource, KeyDef
from .config.schema import TEMPLATE_RE, VALUE_RE, Action, ControlDef, FaderDef, LightZone, state_on
from .deck import Deck
from .device import DeviceError
from .device.manager import RETRY_SECONDS, DeviceManager, present_kinds
from .device import layout
from .device.modules import Event
from .gestures import GestureTracker
from .lighting import LightingController
from .platforms import FocusedWindow, Unsupported, current
from .plugins import REGISTRY, Plugin, PluginContext
from .plugins.base import muted_state, split_state, value_state
from .render.images import ImageStore, LoadedImage
from . import paths
from .render.faces import BLANK, RenderFace, accent_color, parse_color, render

log = logging.getLogger("hubdeck")

RELOAD_CHECK_SECONDS = 1
LOOP_SLEEP = 0.01
PRESSABLE = {"keys": 15, "dial": 1, "knobs": 3}   # modules whose controls have press gestures
SCREENS = ("keys", "dial")
TAKEOVER_WINDOW = 4                               # faders pick up the value within +/- this
FADER_ECHO_SECONDS = 1.0                          # a target value this recent from the fader is its own echo
FOCUS_SETTLE = 0.3                                # a window must keep focus this long to switch pages
RECENT_WINDOWS = 20                               # focused windows remembered for the editor's picker
ASK_SECONDS = 2.0                                 # how long a first read of a state fetched on demand waits
REPLACE = "replace"                               # in a runtime face: it replaces the config's faces
VALUE_OF = "value_of"                             # in a runtime face: {value} and gauge follow this control
MUTED = "muted"                                   # in a runtime face: fields used while its control is muted
_UNSET = object()
Control = tuple[str, int]                         # (module, config number), e.g. ("knobs", 2); dial, base: 1
Source = tuple[str, str | None, int | None]       # (page, module, number) an action comes from;
                                                  # (page, None, None): the page's on_page_enter/exit


LINE_FMT = re.compile(r"line(\d+)")


def format_value(value: Any, fmt: str | None) -> str:
    if value is None:
        return "–"
    if isinstance(value, (datetime, date)):
        return value.strftime(fmt or "%H:%M")
    if isinstance(value, timedelta):            # a duration: %H counts all its hours, or those past %d days
        s = int(value.total_seconds())
        days = "%d" in (fmt := fmt or "%H:%M")
        return (fmt.replace("%d", str(s // 86400)).replace("%H", f"{s % 86400 // 3600 if days else s // 3600:02d}")
                .replace("%M", f"{s // 60 % 60:02d}").replace("%S", f"{s % 60:02d}"))
    if isinstance(value, str) and fmt and (m := LINE_FMT.fullmatch(fmt.strip())):
        lines = value.splitlines()    # {shortcuts.weather:line2}: one line of multi-line text
        n = int(m.group(1))
        return lines[n - 1].strip() if 0 < n <= len(lines) else ""
    if fmt:
        try:
            return format(value, fmt)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


class Engine:
    def __init__(self, source: ConfigSource | None = None, config: Config | None = None,
                 device_factory: Callable[[], Any] | None = None,
                 factories: dict[str, Callable[[], Any]] | None = None,
                 clock: Callable[[], float] = time.monotonic, focus: Any = None):
        self.source = source
        self.cfg: Config | None = config
        self.clock = clock
        if device_factory is not None:      # tests: a key module only
            factories = {"keys": device_factory}
        self.manager = DeviceManager(factories, present=None if factories is not None else present_kinds)
        self.plugins: dict[str, Plugin] = {}
        self._plugin_cfgs: dict[str, dict] = {}
        self.state: dict[str, Any] = {}
        self._shown_states: frozenset[str] = frozenset()   # plugin.state names the config shows
        self._asked_states: frozenset[str] = frozenset()   # and those read at runtime, until a reload
        self._loop_thread: threading.Thread | None = None
        self._state_q: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self._commands: queue.Queue[tuple[Callable, tuple]] = queue.Queue()
        self.page_stack: list[str] = []
        # switching pages by app; `focus` has .current, the focused window (FocusWatcher, set by run)
        self.focus_source = focus
        self.focus: FocusedWindow | None = None      # the focused window (ignored ones left out)
        self.focus_page: str | None = None           # the page it matches
        self.recent_windows: list[FocusedWindow] = []   # newest first
        self._focus_seen: tuple[FocusedWindow | None, float] | None = None   # reading, since when
        self._focus_handled: FocusedWindow | None = None
        self._auto: bool | None = None               # auto_switch set at runtime; None: the config's
        self._saved_stack: list[str] | None = None   # your stack, while one auto-switching set is shown
        self._title_warned = False
        self.gestures = {kind: GestureTracker(n) for kind, n in PRESSABLE.items()}
        # press cycle -> (page, def) it started on
        self._active: dict[tuple[str, int], tuple[str, KeyDef | None]] = {}
        self._owned: set[int] = set()                             # 1-based key numbers
        # set at runtime (deck); controls are (module, config number)
        self.overrides: dict[str, dict[Control, dict[str, Any]]] = {}   # page -> screen -> face fields
        self.override_until: dict[tuple[str, Control], float] = {}      # (page, screen) -> when its face goes
        self.light_overrides: dict[Control, dict[str, Any]] = {}       # LED -> lighting fields
        self.brightness: dict[str, int] = {}                           # screen -> backlight
        self.firing: Source | None = None            # where the action being invoked comes from
        self._on_demand: set[str] = set()            # plugins started by deck.run, kept across reloads
        self.shown: dict[tuple[str, int], bytes] = {}
        self._jpeg_cache: dict[tuple, bytes] = {}
        self._fader_caught: dict[int, bool] = {}
        self._fader_last: dict[int, int] = {}
        self._fader_sent: dict[int, list[tuple[float, int]]] = {}   # recent (time, value) a fader set
        self.lighting = LightingController()
        self.images = ImageStore(paths.app_dir() / "cache")
        self._animating = False
        self._next_anim = 0.0
        self._light_zones = []
        self._dirty = True
        self._force = False
        self._accent = None             # last accent colour painted, to repaint when the OS one changes
        self._reload_check_at = 0.0
        self._home_at: float | None = None   # when the page's home_after takes it back to start_page
        self._reassert_at = 0.0
        self._stop = threading.Event()
        self.listeners: list[Callable[[str, str], None]] = []  # (event, detail): page / auto / error / reloaded / device / quit
        self.deck = Deck(self)

    # -- public API (thread-safe) -----------------------------------------
    @property
    def current_page(self) -> str | None:
        return self.page_stack[-1] if self.page_stack else None

    @property
    def page_names(self) -> list[str]:
        return list(self.cfg.pages) if self.cfg else []

    @property
    def device(self):
        """The key module, if connected."""
        return self.manager.devices.get("keys")

    def call(self, fn: Callable, *args):
        """Run fn(*args) on the engine thread at the start of the next step."""
        self._commands.put((fn, args))

    def goto_page(self, name: str):
        self.call(self._goto, name)

    def back(self):
        self.call(self._back)

    def home(self):
        self.call(self._home)

    @property
    def auto_switch(self) -> bool:
        """Whether pages switch by the focused app."""
        if self._auto is not None:
            return self._auto
        return self.cfg.auto_switch.enabled if self.cfg else False

    def set_auto_switch(self, on: bool | None):
        """Switch pages by app or not (None: toggle), until restart or until the config's
        auto_switch.enabled changes."""
        self.call(self._set_auto, on)

    def reload(self):
        self.call(self._try_reload)

    def run_action(self, action: Action, source: Source | None = None, start: bool = False):
        """Run an action on the engine thread (from any thread, e.g. the control channel).
        `source` is the (page, module, number) it counts as coming from; start=True also starts
        a plugin the config doesn't use."""
        self.call(self._invoke, action, source, start)

    def press(self, page: str, control: Control, gesture: str = "press"):
        """Run the actions the config gives a control on `page` for `gesture`."""
        self.call(self._press, page, control, gesture)

    def turn(self, page: str, control: Control, steps: int):
        """Act as if a dial/knob/roller on `page` was turned `steps` clicks (+ = clockwise/up)."""
        self.call(self._turn_at, page, control, steps)

    def move_fader(self, page: str, number: int, value: float):
        """Set what fader `number` controls on `page` to `value` (0-100), as if moved there."""
        self.call(self._move_fader, page, number, value)

    def set_face(self, page: str, control: Control | None, fields: dict[str, Any] | None,
                 replace: bool = False, value_of: Control | None = None, seconds: float | None = None,
                 muted: dict[str, Any] | None = None):
        """Merge `fields` into the runtime face of a key or the dial (None values reset to the
        config's); replace=True makes `fields` the whole face, over the default style only.
        value_of: {value} and gauge follow that control's value instead. seconds: the face
        goes after that long (a later set_face decides again). muted: fields used on top while
        what the face shows (value_of, or the screen's own target) is muted. fields=None removes
        it (control=None: every screen on the page)."""
        if value_of is not None:
            fields = {**(fields or {}), VALUE_OF: value_of}
        if muted is not None:
            fields = {**(fields or {}), MUTED: muted}
        self.call(self._set_face, page, control, fields, replace, seconds)

    def set_light(self, control: Control, fields: dict[str, Any] | None):
        """Merge `fields` into the runtime lighting of an LED (None values reset to the config's);
        fields=None removes it."""
        self.call(self._set_light, control, fields)

    # reads: single lookups, safe from any thread
    def def_on(self, page: str, control: Control):
        """The config's definition of a control on `page` (dial/knobs/rollers/faders are
        inherited from start_page when the page has none)."""
        kind, number = control
        if kind == "keys":
            return self.cfg.pages[page].keys.get(number)
        if kind == "dial":
            return self.cfg.section(page, "dial")
        return self.cfg.section(page, kind).get(number)

    def is_held(self, control: Control) -> bool:
        kind, number = control
        rot = self.cfg.modules.rotate(kind) if self.cfg else 0
        return self.gestures[kind].keys[layout.index_of(kind, number, rot)].down

    def control_value(self, page: str, control: Control) -> Any:
        """Current value (0-100) of what a dial/knob/roller turn or a fader controls on `page`."""
        d = self.def_on(page, control)
        if isinstance(d, FaderDef):
            p, n = d.target.split(".", 1)
            return self.state.get(f"{p}.{value_state(n, d.arg)}")
        return self.target_value(d)

    def control_muted(self, page: str, control: Control) -> bool | None:
        """Whether what a dial/knob/roller turn or a fader controls on `page` is muted; None if
        unknown, or if its plugin doesn't say."""
        d = self.def_on(page, control)
        if isinstance(d, FaderDef):
            target, arg = d.target, d.arg
        elif isinstance(d, ControlDef) and d.turn and d.turn.target:
            target, arg = d.turn.target, d.turn.arg
        else:
            return None
        p, n = target.split(".", 1)
        return self.state.get(f"{p}.{muted_state(n, arg)}")

    def fader_position(self, number: int) -> int | None:
        """Where fader `number` sits (0-100, up/right = 100), once it has reported."""
        rot = self.cfg.modules.rotate("faders") if self.cfg else 0
        return self._fader_last.get(layout.index_of("faders", number, rot))

    def set_brightness(self, kind: str, percent: int):
        self.call(self._set_brightness, kind, percent)

    def read_state(self, name: str) -> Any:
        """A state's value (None while unknown), for deck.state and hubdeck send get. A state its
        plugin only fetches on demand (fetches_states), and nothing has shown or read yet, is
        asked for first: that read waits up to ASK_SECONDS for the value (but never on the
        engine's own thread)."""
        if self._ask(name) and threading.current_thread() is not self._loop_thread:
            deadline = time.monotonic() + ASK_SECONDS
            while self.state.get(name) is None and time.monotonic() < deadline:
                time.sleep(0.02)
        return self.state.get(name)

    def _ask(self, name: str) -> bool:
        """Have the plugin fetch state `name` if it isn't watched yet; True if it was asked for."""
        if name in self._shown_states or name in self._asked_states:
            return False
        p, _, key = name.partition(".")
        plugin, parsed = self.plugins.get(p), split_state(key)
        if plugin is None or parsed is None or not plugin.fetches_states or                 plugin.check_state(parsed[0], self._plugin_cfgs.get(p, {}), parsed[1]):
            return False
        self.call(self._ask_state, name)
        return True

    def _ask_state(self, name: str):
        if name not in self._asked_states:
            self._asked_states |= {name}
            self._watch_states()

    def emit_state(self, plugin: str, key: str, value: Any):
        self._state_q.put((plugin, key, value))

    def check_runnable(self, action: Action, start: bool = False) -> str | None:
        """Why `action` can't run now, or None. For actions that don't come from the config
        (hubdeck send, deck.run), which the loader hasn't checked. start=True: a plugin the
        config doesn't use is fine, it will be started."""
        from .plugins import REGISTRY
        plugin = REGISTRY.get(action.plugin)
        if plugin is None:
            return f"unknown plugin {action.plugin!r} (available: {', '.join(sorted(REGISTRY))})"
        if action.name not in plugin.actions:
            return f"{action.plugin} has no action {action.name!r} (available: {', '.join(plugin.actions)})"
        if err := plugin.check_action(action.name, action.arg):
            return err
        if action.plugin == "page":
            if action.name == "goto" and (self.cfg is None or action.arg not in self.cfg.pages):
                return f"no page named {action.arg!r}"
        elif action.plugin not in self.plugins:
            if not start:
                return f"{action.plugin} isn't running: nothing in the config uses it yet"
            if not plugin.available():
                return f"{action.plugin} doesn't work on this system"
        return None

    def stop(self):
        self._stop.set()

    def load_initial(self):
        """Load the config from the source. Raises ConfigError."""
        self._apply(self.source.load())

    def run(self):
        from .control import ControlServer
        if self.cfg is not None and not self.page_stack:
            self._apply(self.cfg)
        if self.focus_source is None:
            self._watch_focus()
        control = ControlServer(self)
        try:
            control.start()
        except OSError as e:
            log.warning("control channel not available (hubdeck send won't work): %s", e)
        try:
            while not self._stop.is_set():
                self.step()
                time.sleep(LOOP_SLEEP)
        finally:
            control.stop()
            self.shutdown()

    def shutdown(self):
        if hasattr(self.focus_source, "stop"):
            self.focus_source.stop()
        for name in list(self.plugins):
            self._stop_plugin(name)
        self.manager.close_all()

    def _watch_focus(self):
        from .focus import FocusWatcher
        try:
            reader = current().focus()
        except Unsupported as e:
            if self.cfg and (self.cfg.window_matches() or self.cfg.auto_switch.on_focus):
                log.warning("%s: pages' match and auto_switch do nothing", e)
            return
        self.focus_source = FocusWatcher(reader)
        self.focus_source.start()

    # -- loop --------------------------------------------------------------
    def step(self):
        self._loop_thread = threading.current_thread()
        now = self.clock()
        self._handle_commands()
        if self.source and now >= self._reload_check_at:
            self._reload_check_at = now + RELOAD_CHECK_SECONDS
            if self.source.changed():
                self._try_reload()
        if self.cfg is None:
            return
        for kind in self.manager.scan(now):
            self._init_module(kind, now)
        events, lost = self.manager.poll()
        for kind, err in lost:
            self._module_lost(kind, err, now)
        for kind, event in events:
            self._on_event(kind, event, now)
        for kind, tracker in self.gestures.items():
            for i, ev in tracker.tick(now):
                self._fire(kind, i, ev)
        self._drain_states()
        for (page, screen), until in list(self.override_until.items()):
            if now >= until:
                self._set_face(page, screen, None)
        if self.focus_source is not None:
            self._poll_focus(now)
        if (accent := accent_color()) != self._accent:
            self._accent = accent
            self._dirty = True
        if self.images.changed.is_set():          # a downloaded image is ready
            self.images.changed.clear()
            self._dirty = True
        if self._animating and now >= self._next_anim:
            self._next_anim = now + 1 / self.cfg.defaults.animation_fps
            self._dirty = True
        if self._light_zones:
            for kind in self.lighting.update(self._light_zones, self.manager.devices, self.state.get,
                                             self._light_value, now, force=self._dirty,
                                             gamma=self.cfg.defaults.led_gamma):
                self.manager.drop(kind)
                self._module_lost(kind, "lighting write failed", now)
        reassert = self.cfg.device.reassert_seconds
        if reassert and now >= self._reassert_at:
            self._reassert_at = now + reassert
            self._force = True
        if self._home_at is not None and now >= self._home_at:
            log.info("page %s: unused for its home_after, back to %s", self.current_page, self.cfg.start_page)
            self._home()
        if self._dirty or self._force:
            self._paint(now)

    def notify(self, event: str, detail: str = ""):
        """Tell the listeners (the tray) about an event, e.g. "quit" from the control channel."""
        self._notify(event, detail)

    def _notify(self, event: str, detail: str = ""):
        for cb in self.listeners:
            try:
                cb(event, detail)
            except Exception:
                log.exception("listener failed")

    def _handle_commands(self):
        while True:
            try:
                fn, args = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                fn(*args)
            except Exception:
                log.exception("%s failed", getattr(fn, "__name__", fn))

    # -- config ------------------------------------------------------------
    def _try_reload(self):
        try:
            cfg = self.source.load()
        except ConfigError as e:
            log.error("config not reloaded:\n%s", e)
            self._notify("error", str(e))
            return
        self._apply(cfg)
        log.info("config reloaded from %s", self.source.path)
        self._notify("reloaded", str(self.source.path))

    def _apply(self, cfg: Config):
        old = self.cfg
        self.cfg = cfg
        # plugins: restart only the ones whose settings changed
        wanted = {n: cfg.plugins.get(n, {}) for n in cfg.used_plugins() | self._on_demand if n != "page"}
        for name in list(self.plugins):
            if name not in wanted or self._plugin_cfgs.get(name) != wanted[name]:
                self._stop_plugin(name)
        for name, settings in wanted.items():
            if name not in self.plugins:
                self._start_plugin(name, settings)
        self._watch_targets()
        self._asked_states = frozenset()   # read again, they're asked for again
        self._watch_states()
        self._update_light_zones()
        self.lighting.forget()
        self.images.forget()
        self._jpeg_cache.clear()
        self.overrides = {p: keys for p, keys in self.overrides.items() if p in cfg.pages}
        self.override_until = {k: t for k, t in self.override_until.items() if k[0] in cfg.pages}
        self._update_owned(old.modules.keys.rotate if old is not None else None)
        # modules: open what the config uses, release the rest (MasterHUB may have them)
        self.manager.wanted = cfg.used_modules() | self._runtime_modules()
        for kind in list(self.manager.devices):
            if kind not in self.manager.wanted:
                self.manager.drop(kind)
        stack = [p for p in self.page_stack if p in cfg.pages] or [cfg.start_page]
        if self._saved_stack is not None:
            saved = [p for p in self._saved_stack if p in cfg.pages] or [cfg.start_page]
            if self.page_stack[0] not in cfg.pages:      # the app's page is gone: back to yours
                stack, saved = saved, None
            self._saved_stack = saved
        if old is not None and old.auto_switch.enabled != cfg.auto_switch.enabled:
            self._auto = None             # the config's new setting wins over page.auto_on/off
        self._set_state("page.auto", self.auto_switch)
        # matches may have changed; the page only follows them from the next focus change
        self.focus_page = cfg.match_page(self.focus) if self.focus else None
        if old is None or (old.modules.keys.brightness, old.modules.dial.brightness) != \
                (cfg.modules.keys.brightness, cfg.modules.dial.brightness):
            self.brightness.clear()       # the config's new levels win over deck.set_brightness
            self._apply_brightness()
        if old is not None and old.modules != cfg.modules:
            self._jpeg_cache.clear()
            self.shown.clear()            # numbering may have moved: repaint everything
            self.lighting.forget()
        self._set_pages(stack, force=True)

    def _watch_targets(self):
        per_plugin: dict[str, set] = {}
        for _, target, arg in self.cfg.iter_targets():
            p, n = target.split(".", 1)
            per_plugin.setdefault(p, set()).add((n, arg))
        for name, plugin in self.plugins.items():
            try:
                plugin.watch(per_plugin.get(name, set()))
            except Exception:
                log.exception("plugin %s: watch failed", name)

    def _watch_states(self):
        """Tell each plugin which of its states the config shows or were read at runtime,
        as (state, argument) pairs."""
        self._shown_states = frozenset(f"{p}.{key}" for p, key in self.cfg.state_refs())
        per_plugin: dict[str, set] = {}
        for name in self._shown_states | self._asked_states:
            p, _, key = name.partition(".")
            if parsed := split_state(key):
                per_plugin.setdefault(p, set()).add(parsed)
        for name, plugin in self.plugins.items():
            try:
                plugin.watch_states(per_plugin.get(name, set()))
            except Exception:
                log.exception("plugin %s: watch_states failed", name)

    def _start_plugin(self, name: str, settings: dict):
        cls = REGISTRY[name]
        try:
            plugin = cls(PluginContext(name, self.emit_state, self.deck), settings)
            plugin.start()
        except Exception as e:
            log.exception("plugin %s failed to start", name)
            self._notify("error", f"plugin {name} failed to start: {e}")
            return
        self.plugins[name] = plugin
        self._plugin_cfgs[name] = settings

    def _stop_plugin(self, name: str):
        plugin = self.plugins.pop(name, None)
        self._plugin_cfgs.pop(name, None)
        if plugin:
            try:
                plugin.stop()
            except Exception:
                log.exception("plugin %s failed to stop", name)
        for k in [k for k in self.state if k.startswith(name + ".")]:
            del self.state[k]
        self._dirty = True

    def _update_owned(self, rotate: int | None = None):
        """Keys hubdeck paints: the config's, plus any with a runtime face. Keys it stops
        owning get blanked once (`rotate`: the numbering they were painted with)."""
        owned = self.cfg.owned_keys() if "keys" in self.cfg.used_modules() else set()
        owned |= {num for faces in self.overrides.values() for kind, num in faces if kind == "keys"}
        if self.device:
            rot = self.cfg.modules.keys.rotate if rotate is None else rotate
            for num in self._owned - owned:
                idx = layout.index_of("keys", num, rot)
                self._write("keys", idx, BLANK, force=True)
                self.shown.pop(("keys", idx), None)
        self._owned = owned

    def _runtime_modules(self) -> set[str]:
        """Modules runtime faces and lighting need open, even if the config doesn't use them."""
        kinds = {kind for kind, _ in self.light_overrides}
        kinds.update(kind for faces in self.overrides.values() for kind, _ in faces)
        return kinds

    def _open_runtime_modules(self):
        """Open modules the runtime faces/lighting newly need now, not at the next periodic scan."""
        new = self._runtime_modules() - self.manager.wanted
        if new:
            self.manager.wanted |= new
            now = self.clock()
            for kind in self.manager.scan(now, force=True):
                self._init_module(kind, now)

    def _update_light_zones(self):
        """The config's lighting with runtime changes on top, as (module, LED index, zone)."""
        zones = {(kind, i + 1): zone for kind, i, zone in self.cfg.lighting.zones()}
        for control, fields in self.light_overrides.items():
            base = zones.get(control)
            try:
                zones[control] = LightZone.model_validate(
                    {**(base.model_dump(exclude_unset=True) if base else {}), **fields})
            except ValueError as e:
                log.warning("lighting %s %d: %s", *control, e)
        # lighting zones are numbered like the controls; convert to module LED indexes
        self._light_zones = [(kind, 0 if kind == "base" else layout.index_of(kind, num, self.cfg.modules.rotate(kind)), z)
                             for (kind, num), z in zones.items()]

    # -- pages -------------------------------------------------------------
    def _keep_page(self, now: float, pressed: bool = False):
        """Count the page's home_after from now: its `press` time after a press, else its `idle` time."""
        home_after = self.cfg.pages[self.current_page].home_after if self.current_page else None
        self._home_at = now + home_after.seconds(pressed) if home_after else None

    def _goto(self, name: str):
        if self.cfg and name in self.cfg.pages and name != self.current_page:
            self._set_pages(self.page_stack + [name])

    def _back(self):
        if len(self.page_stack) > 1:
            self._set_pages(self.page_stack[:-1])

    def _home(self):
        self._saved_stack = None          # a stack you set
        self._set_pages([self.cfg.start_page])

    def def_for(self, kind: str, index: int):
        """The definition behind a control on the current page. `index` is the module's own
        0-based index; config numbers follow the mounted orientation (device/layout.py)."""
        page = self.current_page
        if kind == "dial":
            return self.cfg.section(page, "dial")
        number = layout.number_of(kind, index, self.cfg.modules.rotate(kind))
        if kind == "keys":
            return self.cfg.pages[page].keys.get(number)
        return self.cfg.section(page, kind).get(number)

    def _set_pages(self, stack: list[str], force: bool = False):
        shown = self.current_page
        changed = stack != self.page_stack
        self.page_stack = stack
        if not (changed or force):
            return
        d = self.cfg.defaults
        for kind, tracker in self.gestures.items():
            for idx in range(PRESSABLE[kind]):
                kd = self.def_for(kind, idx)
                long_s = d.long_press_ms / 1000 if kd and kd.on_long_press else None
                double_s = d.double_press_ms / 1000 if kd and kd.on_double_press else None
                tracker.configure(idx, long_s, double_s)
        self._fader_caught.clear()       # new page, new targets: faders must pick up again
        self._fader_sent.clear()
        self._dirty = True
        self._keep_page(self.clock())    # a new page or a reload: home_after counts from here
        if changed:
            log.info("page: %s", self.current_page)
            self._notify("page", self.current_page)
        if self.current_page != shown:
            self._page_hooks(shown, self.current_page)

    def _page_hooks(self, left: str | None, shown: str):
        """Run the on_page_exit actions of the page that went off screen, then the on_page_enter
        ones of the page now on it. Their source is the page itself: (page, None, None)."""
        for page, hook in ((left, "on_page_exit"), (shown, "on_page_enter")):
            if page in self.cfg.pages:            # a page a reload removed has no actions left
                for action in getattr(self.cfg.pages[page], hook):
                    self._invoke(action, (page, None, None))

    # -- switching pages by app ---------------------------------------------
    def _poll_focus(self, now: float):
        window = self.focus_source.current
        if self._focus_seen is None or self._focus_seen[0] != window:
            self._focus_seen = (window, now)            # settling: Alt+Tab passes many windows
        elif window is not None and window != self._focus_handled and now - self._focus_seen[1] >= FOCUS_SETTLE:
            self._focus_changed(window)

    def _focus_changed(self, window: FocusedWindow):
        self._focus_handled = window
        self.recent_windows = [window] + [w for w in self.recent_windows if w != window][:RECENT_WINDOWS - 1]
        if window == self.focus or self.cfg.auto_switch.ignores(window):
            return                                     # as if focus never moved
        new_app = self.focus is None or window.app != self.focus.app
        self.focus = window
        self._set_state("page.app", window.app)
        self._set_state("page.title", window.title)
        if window.title is None and not self._title_warned and any(
                m.title is not None for m in self.cfg.window_matches()):
            self._title_warned = True
            log.warning("window titles can't be read, so match and ignore entries with a 'title' don't "
                        "apply (macOS: hubdeck needs the Accessibility permission)")
        page = self.cfg.match_page(window)
        matched = f"matches page {page}" if page else "no page"
        if new_app or page != self.focus_page:
            log.info("focus: %s, %s", window.app, matched)    # titles can be private: debug only
        log.debug("focus: %s %r, %s", window.app, window.title, matched)
        if page != self.focus_page:
            self.focus_page = page
            self._follow_focus()
        for action in self.cfg.auto_switch.on_focus:
            self._invoke(action)

    def _follow_focus(self):
        """Show the page the focus matches, or with none, put back the stack you set; not while
        switching is off or a sticky page is on screen."""
        if not self.auto_switch or self.cfg.pages[self.current_page].sticky:
            return
        if self.focus_page is not None:
            if self._saved_stack is None:
                self._saved_stack = self.page_stack
            self._set_pages([self.focus_page])
        elif self._saved_stack is not None:
            stack, self._saved_stack = self._saved_stack, None
            self._set_pages(stack)

    def _set_auto(self, on: bool | None):
        was = self.auto_switch
        self._auto = not was if on is None else on
        self._set_state("page.auto", self._auto)
        if self._auto != was:
            log.info("switching pages by app: %s", "on" if self._auto else "off")
            self._notify("auto", "on" if self._auto else "off")
            if self._auto:
                self._follow_focus()

    # -- input -------------------------------------------------------------
    def _on_event(self, kind: str, e: Event, now: float):
        if e.kind != "key" or e.value:       # any use but letting go of a key; before the actions,
            self._keep_page(now, pressed=e.kind == "key")    # which may switch pages
        if e.kind == "key" and kind in self.gestures and 0 <= e.index < PRESSABLE[kind]:
            for i, ev in self.gestures[kind].edge(e.index, bool(e.value), now):
                self._fire(kind, i, ev)
        elif e.kind == "turn":
            self._turn(kind, e.index, int(e.value))
        elif e.kind == "slide":
            self._slide(kind, e.index, int(e.value))

    def _fire(self, kind: str, idx: int, event: str):
        if event == "down":
            self._active[(kind, idx)] = (self.current_page, self.def_for(kind, idx))
        page, kd = self._active.get((kind, idx), (None, None))
        if kd is None:
            return
        source = (page, kind, layout.number_of(kind, idx, self.cfg.modules.rotate(kind)))
        for action in kd.actions(event):
            self._invoke(action, source)

    def _press(self, page: str, control: Control, gesture: str):
        kd = self.def_on(page, control) if page in self.cfg.pages else None
        for action in kd.actions(gesture) if kd else []:
            self._invoke(action, (page, *control))

    def _turn(self, kind: str, idx: int, steps: int):
        number = layout.number_of(kind, idx, self.cfg.modules.rotate(kind))
        log.debug("%s %d turned %+d", kind, number, steps)
        if kind == "rollers":           # rolling direction follows the mounting
            steps *= layout.axis_sign(self.cfg.modules.rotate(kind))
        self._turn_def(self.def_for(kind, idx), steps, (self.current_page, kind, number))

    def _turn_at(self, page: str, control: Control, steps: int):
        if page in self.cfg.pages:
            self._turn_def(self.def_on(page, control), steps, (page, *control))

    def _turn_def(self, cd, steps: int, source: Source):
        if not isinstance(cd, ControlDef) or cd.turn is None:
            return
        t = cd.turn
        if t.invert:
            steps = -steps
        if t.target:
            p, n = t.target.split(".", 1)
            plugin = self.plugins.get(p)
            if plugin is None:
                return
            try:
                plugin.adjust(n, t.arg, steps * t.step, t.expo)
            except Exception as e:
                log.error("%s: adjust failed: %s", t.target, e)
        else:
            for _ in range(abs(steps)):
                for action in (t.cw if steps > 0 else t.ccw):
                    self._invoke(action, source)
        for action in cd.on_turn:
            self._invoke(action, source)

    def _move_fader(self, page: str, number: int, value: float):
        fd = self.def_on(page, ("faders", number)) if page in self.cfg.pages else None
        if fd is None:
            return
        p, n = fd.target.split(".", 1)
        plugin = self.plugins.get(p)
        if plugin is None:
            return
        try:
            plugin.set(n, fd.arg, value)   # not the fader's own echo: soft takeover lets go of it
        except Exception as e:
            log.error("%s: set failed: %s", fd.target, e)

    def _slide(self, kind: str, idx: int, value: int):
        fd = self.def_for(kind, idx)
        if fd is None:
            return
        if (layout.axis_sign(self.cfg.modules.rotate(kind)) < 0) != fd.invert:
            value = 100 - value           # increase up/right whatever the mounting
        log.debug("faders %d at %d", layout.number_of(kind, idx, self.cfg.modules.rotate(kind)), value)
        p, n = fd.target.split(".", 1)
        plugin = self.plugins.get(p)
        if plugin is None:
            return
        current = self.state.get(f"{p}.{value_state(n, fd.arg)}")
        last = self._fader_last.get(idx)
        self._fader_last[idx] = value
        if last is None:
            return       # first report after connecting: just where the fader sits, not a movement
        if not self._fader_caught.get(idx):
            if current is None:
                return   # value unknown (app not running?): don't take over blindly
            crossed = (last - current) * (value - current) <= 0
            if abs(value - current) <= TAKEOVER_WINDOW or crossed:
                self._fader_caught[idx] = True
                log.info("fader %d took over %s at %d (was %s)", idx + 1, fd.target, value, current)
            else:
                return   # soft takeover: don't jump until the fader reaches the current value
        sent = self._fader_sent.setdefault(idx, [])
        if sent and sent[-1][1] == value:
            return       # already set to this
        now = self.clock()
        sent[:] = [(t, v) for t, v in sent if now - t < FADER_ECHO_SECONDS] + [(now, value)]
        try:
            plugin.set(n, fd.arg, value)
        except Exception as e:
            log.error("%s: set failed: %s", fd.target, e)

    def _release_faders(self, name: str, value: Any):
        """A fader's target changed, and not to a value the fader just set (keyboard, the app,
        another fader): let go, so the fader has to reach the new value before it acts again."""
        if value is None:
            return
        now = self.clock()
        for idx, caught in self._fader_caught.items():
            fd = self.def_for("faders", idx) if caught else None
            if fd is None:
                continue
            p, n = fd.target.split(".", 1)
            if f"{p}.{value_state(n, fd.arg)}" != name:
                continue
            if any(now - t < FADER_ECHO_SECONDS and abs(v - value) <= TAKEOVER_WINDOW
                   for t, v in self._fader_sent.get(idx, [])):
                continue   # the fader's own change coming back
            self._fader_caught[idx] = False
            self._fader_sent.pop(idx, None)
            log.info("fader %d released: %s changed to %s elsewhere", idx + 1, fd.target, value)

    def _invoke(self, action: Action, source: Source | None = None, start: bool = False):
        if self.cfg is None:
            return
        log.debug("action %s", action)
        if action.plugin == "page":
            if action.name == "goto":
                self.goto_page(action.arg)
            elif action.name == "back":
                self._back()
            elif action.name == "home":
                self._home()
            elif action.name in ("auto_on", "auto_off", "auto_toggle"):
                self._set_auto({"auto_on": True, "auto_off": False}.get(action.name))
            return
        plugin = self.plugins.get(action.plugin)
        if plugin is None and start:
            self._on_demand.add(action.plugin)
            self._start_plugin(action.plugin, self.cfg.plugins.get(action.plugin, {}))
            plugin = self.plugins.get(action.plugin)
        if plugin is None:
            log.warning("%s: plugin %s is not running", action, action.plugin)
            return
        self.firing = source
        try:
            plugin.invoke(action.name, action.arg)
        except Exception as e:
            log.error("%s failed: %s", action, e)
        finally:
            self.firing = None

    # -- state -------------------------------------------------------------
    def _set_state(self, name: str, value: Any):
        """A state the engine keeps itself (page.*)."""
        if self.state.get(name, ...) != value:
            self.state[name] = value
            self._dirty = True

    def _drain_states(self):
        while True:
            try:
                plugin, key, value = self._state_q.get_nowait()
            except queue.Empty:
                return
            if plugin not in self.plugins:
                continue  # late update from a stopped plugin
            name = f"{plugin}.{key}"
            if self.state.get(name, ...) != value:
                self.state[name] = value
                self._dirty = True
                if key.startswith("value:"):
                    self._release_faders(name, value)

    # -- faces -------------------------------------------------------------
    def _template(self, s: str | None, value: Any = None) -> str | None:
        if s is None:
            return None
        s = TEMPLATE_RE.sub(lambda m: format_value(self.state.get(f"{m[1]}.{m[2]}"), m[3]), s)
        return VALUE_RE.sub(lambda m: format_value(value, m[1]), s)

    def target_value(self, kd) -> Any:
        if isinstance(kd, ControlDef) and kd.turn and kd.turn.target:
            p, n = kd.turn.target.split(".", 1)
            return self.state.get(f"{p}.{value_state(n, kd.turn.arg)}")
        return None

    def _set_face(self, page: str, control: Control | None, fields: dict[str, Any] | None,
                  replace: bool = False, seconds: float | None = None):
        if page not in self.cfg.pages:
            return
        faces = self.overrides.setdefault(page, {})
        for screen in list(faces) if control is None else [control]:
            self.override_until.pop((page, screen), None)
        if fields is not None and seconds:
            self.override_until[(page, control)] = self.clock() + seconds
        if fields is not None:
            if isinstance(fields.get("image"), LoadedImage):     # a picture made in code
                fields = {**fields, "image": self.images.put(fields["image"])}
            base = {REPLACE: True} if replace else faces.get(control, {})   # later merges keep REPLACE
            merged = {k: v for k, v in {**base, **fields}.items() if v is not None}
            if merged:
                faces[control] = merged
            else:
                faces.pop(control, None)
        elif control is None:
            faces.clear()
        else:
            faces.pop(control, None)
        if not faces:
            del self.overrides[page]
        self.images.keep_memory({f["image"] for faces in self.overrides.values() for f in faces.values()
                                 if "image" in f})
        self._update_owned()
        self._open_runtime_modules()
        self._dirty = True

    def _set_light(self, control: Control, fields: dict[str, Any] | None):
        merged = {k: v for k, v in {**self.light_overrides.get(control, {}), **(fields or {})}.items()
                  if v is not None}
        if fields is not None and merged:
            self.light_overrides[control] = merged
        elif self.light_overrides.pop(control, None) is not None:
            self._light_off(control)
        self._update_light_zones()
        self._open_runtime_modules()
        self._dirty = True

    def _light_off(self, control: Control):
        """Turn off an LED that only runtime lighting lit (like keys, it's handed back blank)."""
        kind, number = control
        if any((k, i + 1) == control for k, i, _ in self.cfg.lighting.zones()):
            return                         # the config lights it: it goes back to that
        idx = 0 if kind == "base" else layout.index_of(kind, number, self.cfg.modules.rotate(kind))
        self.lighting.written.pop((kind, idx), None)
        dev = self.manager.devices.get(kind)
        if dev is not None:
            try:
                dev.set_led_mode(idx, "off")
            except DeviceError as e:
                log.warning("%s lighting write failed: %s", kind, e)

    def face_for(self, kd: KeyDef | None, override: dict[str, Any] | None = None,
                 value: Any = _UNSET) -> RenderFace:
        """The face for a definition, with `override` (fields set at runtime) on top, or instead
        of the definition's faces when it holds REPLACE. {value} and the gauge show `value`,
        by default the definition's turn target's."""
        if kd is None and override is None:
            return BLANK
        merged = self.cfg.defaults.style.model_dump(exclude_none=True)
        replace = override is not None and REPLACE in override
        if override is not None:
            override = {k: v for k, v in override.items() if k not in (REPLACE, VALUE_OF, MUTED)}
        if not replace and kd is not None:
            merged.update(kd.face.model_dump(exclude_unset=True))
            if kd.state:
                on = state_on(kd.state, self.state.get)
                overlay = kd.face_unknown if on is None else kd.face_on if on else None
                if overlay is not None:
                    merged.update(overlay.model_dump(exclude_unset=True))
            for layer in kd.layers:
                if state_on(layer.state, self.state.get):
                    merged.update(layer.model_dump(exclude_unset=True, exclude={"state"}))
        if override is not None:
            merged.update(override)
        if value is _UNSET:
            value = self.target_value(kd)
        gauge = merged.get("gauge", False)
        image, frame = merged.get("image"), 0
        if image and "{" in image:              # e.g. {vars.picture}: a picture sent with `hubdeck send image`
            image = self._template(image, value)
            if image in ("", "–"):
                image = None
        if image:
            loaded = self.images.get(image)
            if loaded is None:
                image = None                       # still downloading, or unusable: draw without it
            elif loaded.animated:
                frame = loaded.frame_at(self.clock())
                self._animating = True
        return RenderFace(
            bg=parse_color(merged.get("bg", "#000000")),
            fg=parse_color(merged.get("fg", "#ffffff")),
            label_color=parse_color(merged.get("label_color", "#ffffff")),
            icon=merged.get("icon"),
            filled=merged.get("filled", False),
            label=self._template(merged.get("label"), value),
            text=self._template(merged.get("text"), value),
            font_size=merged.get("font_size"),
            gauge=float(value) if gauge and value is not None else None,
            gauge_known=not gauge or value is not None,
            image=image,
            image_fit=merged.get("image_fit", "cover"),
            frame=frame,
        )

    def screen_face(self, control: Control, page: str | None = None) -> RenderFace:
        """Face of a key or the dial on `page` (default: the one on screen)."""
        page = page or self.current_page
        override = self.overrides.get(page, {}).get(control)
        if override is None:
            return self.face_for(self.def_on(page, control), None)
        source = override.get(VALUE_OF, control)                    # whose value and mute it shows
        if MUTED in override and self.control_muted(page, source):
            override = {**override, **override[MUTED]}
        value = self.control_value(page, source) if VALUE_OF in override else _UNSET
        return self.face_for(self.def_on(page, control), override, value)

    def resolve_face(self, num: int, page: str | None = None) -> RenderFace:
        """Face of key `num` (1-based) on `page` (default: the one on screen)."""
        return self.screen_face(("keys", num), page)

    def _encode(self, kind: str, face: RenderFace) -> bytes:
        dev = self.manager.devices[kind]
        rotate = layout.image_rotation(self.cfg.modules.rotate(kind))
        key = (kind, face, rotate)
        data = self._jpeg_cache.get(key)
        if data is None:
            if len(self._jpeg_cache) > 4096:         # GIF frames x keys can add up
                self._jpeg_cache.clear()
            shape = getattr(dev, "shape", "square")
            picture = None
            if face.image:
                loaded = self.images.get(face.image)
                picture = loaded.frames[face.frame] if loaded else None
            data = dev.encode(render(face, dev.image_size, rotate, shape, picture))
            self._jpeg_cache[key] = data
        return data

    def _write(self, kind: str, idx: int, face: RenderFace, force: bool = False):
        data = self._encode(kind, face)
        if force or self.shown.get((kind, idx)) != data:
            self.manager.devices[kind].set_image(idx, data)
            self.shown[(kind, idx)] = data

    def _paint(self, now: float):
        self._animating = False                     # face_for sets it again while a GIF is visible
        for kind in SCREENS:
            if kind not in self.manager.devices:
                continue
            try:
                if kind == "keys":
                    rot = self.cfg.modules.keys.rotate
                    for num in sorted(self._owned):
                        self._write(kind, layout.index_of(kind, num, rot), self.resolve_face(num), self._force)
                else:
                    self._write(kind, 0, self.screen_face(("dial", 1)), self._force)
            except DeviceError as e:
                self.manager.drop(kind)
                self._module_lost(kind, str(e), now)
        self._dirty = self._force = False

    # -- modules -----------------------------------------------------------
    def _init_module(self, kind: str, now: float):
        dev = self.manager.devices[kind]
        try:
            if kind in SCREENS:
                dev.set_software_mode(True)
                for k in [k for k in self.shown if k[0] == kind]:
                    del self.shown[k]
        except DeviceError as e:
            self.manager.drop(kind)
            self._module_lost(kind, str(e), now)
            return
        self._apply_brightness(kind)
        self.lighting.forget(kind)
        self._dirty = self._force = True
        self._notify("device", f"{kind} connected")

    def _light_value(self, kind: str, idx: int):
        """Value a zone's `brightness: value` follows: the control's target on the current page."""
        if kind == "knobs":
            return self.target_value(self.def_for("knobs", idx))
        if kind == "faders":
            fd = self.def_for("faders", idx)
            if fd is not None:
                p, n = fd.target.split(".", 1)
                return self.state.get(f"{p}.{value_state(n, fd.arg)}")
        return None

    def _set_brightness(self, kind: str, percent: int):
        self.brightness[kind] = percent
        self._apply_brightness(kind)

    def _apply_brightness(self, only: str | None = None):
        levels = {"keys": self.cfg.modules.keys.brightness, "dial": self.cfg.modules.dial.brightness}
        levels.update(self.brightness)
        for kind, level in levels.items():
            dev = self.manager.devices.get(kind)
            if dev is None or level is None or (only and kind != only):
                continue
            try:
                dev.set_brightness(level)
            except DeviceError as e:
                log.warning("could not set %s brightness: %s", kind, e)

    def _module_lost(self, kind: str, err: str, now: float):
        self._notify("device", f"{kind} lost: {err}")
        self.manager.defer(now)
        # release anything that was held so on_up actions (e.g. hold-to-mute) still run
        tracker = self.gestures.get(kind)
        if tracker:
            for idx, k in enumerate(tracker.keys):
                if k.down:
                    for i, ev in tracker.edge(idx, False, now):
                        self._fire(kind, i, ev)
        if kind == "faders":             # positions must be re-learned after reconnecting
            self._fader_last.clear()
            self._fader_caught.clear()
            self._fader_sent.clear()
