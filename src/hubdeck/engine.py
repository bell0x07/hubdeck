"""The main loop: modules <-> gestures/turns/faders <-> actions/plugins <-> faces."""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

from .config import Config, ConfigError, ConfigSource, KeyDef
from .config.schema import TEMPLATE_RE, VALUE_RE, Action, ControlDef
from .device import DeviceError
from .device.manager import RETRY_SECONDS, DeviceManager, present_kinds
from .device.modules import Event
from .gestures import GestureTracker
from .lighting import LightingController
from .plugins import REGISTRY, Plugin, PluginContext
from .plugins.base import value_state
from .render.faces import BLANK, RenderFace, parse_color, render

log = logging.getLogger("hubdeck")

RELOAD_CHECK_SECONDS = 1
LOOP_SLEEP = 0.01
PRESSABLE = {"keys": 15, "dial": 1, "knobs": 3}   # modules whose controls have press gestures
SCREENS = ("keys", "dial")
TAKEOVER_WINDOW = 4                               # faders pick up the value within +/- this


def format_value(value: Any, fmt: str | None) -> str:
    if value is None:
        return "–"
    if isinstance(value, (datetime, date)):
        return value.strftime(fmt or "%H:%M")
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
                 clock: Callable[[], float] = time.monotonic):
        self.source = source
        self.cfg: Config | None = config
        self.clock = clock
        if device_factory is not None:      # tests: a key module only
            factories = {"keys": device_factory}
        self.manager = DeviceManager(factories, present=None if factories is not None else present_kinds)
        self.plugins: dict[str, Plugin] = {}
        self._plugin_cfgs: dict[str, dict] = {}
        self.state: dict[str, Any] = {}
        self._state_q: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self._commands: queue.Queue[tuple] = queue.Queue()
        self.page_stack: list[str] = []
        self.gestures = {kind: GestureTracker(n) for kind, n in PRESSABLE.items()}
        self._active: dict[tuple[str, int], KeyDef | None] = {}   # press cycle -> def it started on
        self._owned: set[int] = set()                             # 1-based key numbers
        self.shown: dict[tuple[str, int], bytes] = {}
        self._jpeg_cache: dict[tuple, bytes] = {}
        self._fader_caught: dict[int, bool] = {}
        self._fader_last: dict[int, int] = {}
        self.lighting = LightingController()
        self._light_zones = []
        self._dirty = True
        self._force = False
        self._reload_check_at = 0.0
        self._reassert_at = 0.0
        self._stop = threading.Event()
        self.listeners: list[Callable[[str, str], None]] = []  # (event, detail): page / error / reloaded / device

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

    def goto_page(self, name: str):
        self._commands.put(("goto", name))

    def reload(self):
        self._commands.put(("reload",))

    def stop(self):
        self._stop.set()

    def load_initial(self):
        """Load the config from the source. Raises ConfigError."""
        self._apply(self.source.load())

    def run(self):
        if self.cfg is not None and not self.page_stack:
            self._apply(self.cfg)
        try:
            while not self._stop.is_set():
                self.step()
                time.sleep(LOOP_SLEEP)
        finally:
            self.shutdown()

    def shutdown(self):
        for name in list(self.plugins):
            self._stop_plugin(name)
        self.manager.close_all()

    # -- loop --------------------------------------------------------------
    def step(self):
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
        if self._dirty or self._force:
            self._paint(now)

    def _notify(self, event: str, detail: str = ""):
        for cb in self.listeners:
            try:
                cb(event, detail)
            except Exception:
                log.exception("listener failed")

    def _handle_commands(self):
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            if cmd[0] == "goto" and self.cfg and cmd[1] in self.cfg.pages:
                self._set_pages(self.page_stack + [cmd[1]] if cmd[1] != self.current_page else self.page_stack)
            elif cmd[0] == "reload":
                self._try_reload()

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
        wanted = {n: cfg.plugins.get(n, {}) for n in cfg.used_plugins() if n != "page"}
        for name in list(self.plugins):
            if name not in wanted or self._plugin_cfgs.get(name) != wanted[name]:
                self._stop_plugin(name)
        for name, settings in wanted.items():
            if name not in self.plugins:
                self._start_plugin(name, settings)
        self._watch_targets()
        self._light_zones = cfg.lighting.zones()
        self.lighting.forget()
        for name, page in cfg.pages.items():
            if page.match:
                log.warning("pages.%s.match: app-based page switching is not implemented yet; ignored", name)
        # keys we stop owning get blanked once
        new_owned = cfg.owned_keys() if "keys" in cfg.used_modules() else set()
        if self.device:
            for num in self._owned - new_owned:
                self._write("keys", num - 1, BLANK, force=True)
                self.shown.pop(("keys", num - 1), None)
        self._owned = new_owned
        # modules: open what the config uses, release the rest (MasterHUB may have them)
        self.manager.wanted = cfg.used_modules()
        for kind in list(self.manager.devices):
            if kind not in self.manager.wanted:
                self.manager.drop(kind)
        stack = [p for p in self.page_stack if p in cfg.pages] or [cfg.start_page]
        if old is None or old.device.brightness != cfg.device.brightness \
                or old.device.dial_brightness != cfg.device.dial_brightness:
            self._apply_brightness()
        if old is not None and old.device.rotate != cfg.device.rotate:
            self._jpeg_cache.clear()
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

    def _start_plugin(self, name: str, settings: dict):
        cls = REGISTRY[name]
        try:
            plugin = cls(PluginContext(name, self._emit_state), settings)
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

    # -- pages -------------------------------------------------------------
    def def_for(self, kind: str, index: int):
        """The definition behind a control on the current page (index is 0-based)."""
        page = self.current_page
        if kind == "keys":
            return self.cfg.pages[page].keys.get(index + 1)
        if kind == "dial":
            return self.cfg.section(page, "dial")
        return self.cfg.section(page, kind).get(index + 1)

    def _set_pages(self, stack: list[str], force: bool = False):
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
        self._dirty = True
        if changed:
            log.info("page: %s", self.current_page)
            self._notify("page", self.current_page)

    # -- input -------------------------------------------------------------
    def _on_event(self, kind: str, e: Event, now: float):
        if e.kind == "key" and kind in self.gestures and 0 <= e.index < PRESSABLE[kind]:
            for i, ev in self.gestures[kind].edge(e.index, bool(e.value), now):
                self._fire(kind, i, ev)
        elif e.kind == "turn":
            self._turn(kind, e.index, int(e.value))
        elif e.kind == "slide":
            self._slide(kind, e.index, int(e.value))

    def _fire(self, kind: str, idx: int, event: str):
        if event == "down":
            self._active[(kind, idx)] = self.def_for(kind, idx)
        kd = self._active.get((kind, idx))
        if kd is None:
            return
        for action in kd.actions(event):
            self._invoke(action)

    def _turn(self, kind: str, idx: int, steps: int):
        cd = self.def_for(kind, idx)
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
                plugin.adjust(n, t.arg, steps * t.step)
            except Exception as e:
                log.error("%s: adjust failed: %s", t.target, e)
            return
        for _ in range(abs(steps)):
            for action in (t.cw if steps > 0 else t.ccw):
                self._invoke(action)

    def _slide(self, kind: str, idx: int, value: int):
        fd = self.def_for(kind, idx)
        if fd is None:
            return
        if fd.invert:
            value = 100 - value
        p, n = fd.target.split(".", 1)
        plugin = self.plugins.get(p)
        if plugin is None:
            return
        current = self.state.get(f"{p}.{value_state(n, fd.arg)}")
        last = self._fader_last.get(idx)
        self._fader_last[idx] = value
        if not self._fader_caught.get(idx):
            crossed = last is not None and current is not None and (last - current) * (value - current) <= 0
            if current is None or abs(value - current) <= TAKEOVER_WINDOW or crossed:
                self._fader_caught[idx] = True
            else:
                return   # soft takeover: don't jump until the fader reaches the current value
        try:
            plugin.set(n, fd.arg, value)
        except Exception as e:
            log.error("%s: set failed: %s", fd.target, e)

    def _invoke(self, action: Action):
        log.debug("action %s", action)
        if action.plugin == "page":
            if action.name == "goto":
                self.goto_page(action.arg)
            elif action.name == "back" and len(self.page_stack) > 1:
                self._set_pages(self.page_stack[:-1])
            elif action.name == "home":
                self._set_pages([self.cfg.start_page])
            return
        plugin = self.plugins.get(action.plugin)
        if plugin is None:
            log.warning("%s: plugin %s is not running", action, action.plugin)
            return
        try:
            plugin.invoke(action.name, action.arg)
        except Exception as e:
            log.error("%s failed: %s", action, e)

    # -- state -------------------------------------------------------------
    def _emit_state(self, plugin: str, key: str, value: Any):
        self._state_q.put((plugin, key, value))

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

    def face_for(self, kd: KeyDef | None) -> RenderFace:
        if kd is None:
            return BLANK
        merged = self.cfg.defaults.style.model_dump(exclude_none=True)
        merged.update(kd.face.model_dump(exclude_unset=True))
        if kd.state:
            value = self.state.get(kd.state)
            overlay = kd.face_unknown if value is None else kd.face_on if value else None
            if overlay is not None:
                merged.update(overlay.model_dump(exclude_unset=True))
        value = self.target_value(kd)
        gauge = merged.get("gauge", False)
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
        )

    def resolve_face(self, num: int) -> RenderFace:
        """Face of key `num` (1-based) on the current page."""
        return self.face_for(self.cfg.pages[self.current_page].keys.get(num))

    def _encode(self, kind: str, face: RenderFace) -> bytes:
        dev = self.manager.devices[kind]
        rotate = self.cfg.device.rotate if kind == "keys" else 0
        key = (kind, face, rotate)
        data = self._jpeg_cache.get(key)
        if data is None:
            if len(self._jpeg_cache) > 512:
                self._jpeg_cache.clear()
            shape = getattr(dev, "shape", "square")
            data = dev.encode(render(face, dev.image_size, rotate, shape))
            self._jpeg_cache[key] = data
        return data

    def _write(self, kind: str, idx: int, face: RenderFace, force: bool = False):
        data = self._encode(kind, face)
        if force or self.shown.get((kind, idx)) != data:
            self.manager.devices[kind].set_image(idx, data)
            self.shown[(kind, idx)] = data

    def _paint(self, now: float):
        for kind in SCREENS:
            if kind not in self.manager.devices:
                continue
            try:
                if kind == "keys":
                    for num in sorted(self._owned):
                        self._write(kind, num - 1, self.resolve_face(num), self._force)
                else:
                    self._write(kind, 0, self.face_for(self.def_for("dial", 0)), self._force)
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

    def _apply_brightness(self, only: str | None = None):
        levels = {"keys": self.cfg.device.brightness, "dial": self.cfg.device.dial_brightness}
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
