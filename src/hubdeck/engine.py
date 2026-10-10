"""The main loop: device <-> gestures <-> actions/plugins <-> key faces."""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

from .config import Config, ConfigError, ConfigSource, KeyDef
from .config.schema import TEMPLATE_RE, Action
from .device import DeviceError, KeyModule, M4315
from .gestures import GestureTracker
from .plugins import REGISTRY, Plugin, PluginContext
from .render.faces import BLANK, RenderFace, parse_color, render

log = logging.getLogger("hubdeck")

DEVICE_RETRY_SECONDS = 3
RELOAD_CHECK_SECONDS = 1
LOOP_SLEEP = 0.01


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
                 device_factory: Callable[[], KeyModule] = M4315, clock: Callable[[], float] = time.monotonic):
        self.source = source
        self.cfg: Config | None = config
        self.device_factory = device_factory
        self.clock = clock
        self.device: KeyModule | None = None
        self.plugins: dict[str, Plugin] = {}
        self._plugin_cfgs: dict[str, dict] = {}
        self.state: dict[str, Any] = {}
        self._state_q: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self._commands: queue.Queue[tuple] = queue.Queue()
        self.page_stack: list[str] = []
        self.gestures = GestureTracker(15)
        self._active: dict[int, KeyDef | None] = {}   # key index -> KeyDef its current press started on
        self._owned: set[int] = set()                # 1-based
        self.shown: dict[int, bytes] = {}
        self._jpeg_cache: dict[tuple[RenderFace, int], bytes] = {}
        self._dirty = True
        self._force = False
        self._device_retry_at = 0.0
        self._last_device_error = None
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
        self._close_device()

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
        self._ensure_device(now)
        if self.device:
            try:
                for idx, pressed in self.device.poll():
                    if 0 <= idx < 15:
                        for i, ev in self.gestures.edge(idx, pressed, now):
                            self._fire(i, ev)
            except DeviceError as e:
                self._device_lost(e, now)
        for i, ev in self.gestures.tick(now):
            self._fire(i, ev)
        self._drain_states()
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
        for name, page in cfg.pages.items():
            if page.match:
                log.warning("pages.%s.match: app-based page switching is not implemented yet; ignored", name)
        # keys we stop owning get blanked once
        new_owned = cfg.owned_keys()
        if self.device:
            for num in self._owned - new_owned:
                self._write(num - 1, BLANK, force=True)
                self.shown.pop(num - 1, None)
        self._owned = new_owned
        # keep the current page if it still exists
        stack = [p for p in self.page_stack if p in cfg.pages] or [cfg.start_page]
        if self.device and (old is None or old.device.brightness != cfg.device.brightness):
            self._apply_brightness()
        if old is not None and old.device.rotate != cfg.device.rotate:
            self._jpeg_cache.clear()
        self._set_pages(stack, force=True)

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
    def _set_pages(self, stack: list[str], force: bool = False):
        changed = stack != self.page_stack
        self.page_stack = stack
        if not (changed or force):
            return
        page = self.cfg.pages[self.current_page]
        d = self.cfg.defaults
        for idx in range(15):
            kd = page.keys.get(idx + 1)
            long_s = d.long_press_ms / 1000 if kd and kd.on_long_press else None
            double_s = d.double_press_ms / 1000 if kd and kd.on_double_press else None
            self.gestures.configure(idx, long_s, double_s)
        self._dirty = True
        if changed:
            log.info("page: %s", self.current_page)
            self._notify("page", self.current_page)

    # -- actions -----------------------------------------------------------
    def _fire(self, idx: int, event: str):
        if event == "down":
            self._active[idx] = self.cfg.pages[self.current_page].keys.get(idx + 1)
        kd = self._active.get(idx)
        if kd is None:
            return
        for action in kd.actions(event):
            self._invoke(action)

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
    def _template(self, s: str | None) -> str | None:
        if s is None:
            return None
        return TEMPLATE_RE.sub(lambda m: format_value(self.state.get(f"{m[1]}.{m[2]}"), m[3]), s)

    def resolve_face(self, num: int) -> RenderFace:
        kd = self.cfg.pages[self.current_page].keys.get(num)
        if kd is None:
            return BLANK
        merged = self.cfg.defaults.style.model_dump(exclude_none=True)
        merged.update(kd.face.model_dump(exclude_unset=True))
        if kd.state:
            value = self.state.get(kd.state)
            overlay = kd.face_unknown if value is None else kd.face_on if value else None
            if overlay is not None:
                merged.update(overlay.model_dump(exclude_unset=True))
        return RenderFace(
            bg=parse_color(merged.get("bg", "#000000")),
            fg=parse_color(merged.get("fg", "#ffffff")),
            label_color=parse_color(merged.get("label_color", "#ffffff")),
            icon=merged.get("icon"),
            filled=merged.get("filled", False),
            label=self._template(merged.get("label")),
            text=self._template(merged.get("text")),
            font_size=merged.get("font_size"),
        )

    def _encode(self, face: RenderFace) -> bytes:
        key = (face, self.cfg.device.rotate)
        data = self._jpeg_cache.get(key)
        if data is None:
            if len(self._jpeg_cache) > 512:
                self._jpeg_cache.clear()
            data = self.device.encode(render(face, self.device.image_size, self.cfg.device.rotate))
            self._jpeg_cache[key] = data
        return data

    def _write(self, idx: int, face: RenderFace, force: bool = False):
        data = self._encode(face)
        if force or self.shown.get(idx) != data:
            self.device.set_image(idx, data)
            self.shown[idx] = data

    def _paint(self, now: float):
        if not self.device:
            return
        try:
            for num in sorted(self._owned):
                self._write(num - 1, self.resolve_face(num), self._force)
        except DeviceError as e:
            self._device_lost(e, now)
            return
        self._dirty = self._force = False

    # -- device ------------------------------------------------------------
    def _ensure_device(self, now: float):
        if self.device or now < self._device_retry_at:
            return
        try:
            dev = self.device_factory()
            dev.set_software_mode(True)
        except DeviceError as e:
            if str(e) != self._last_device_error:
                log.warning("%s - retrying every %ss", e, DEVICE_RETRY_SECONDS)
                self._notify("device", str(e))
                self._last_device_error = str(e)
            self._device_retry_at = now + DEVICE_RETRY_SECONDS
            return
        self.device = dev
        self._last_device_error = None
        self.shown.clear()
        self._apply_brightness()
        self._dirty = self._force = True
        log.info("key module connected")
        self._notify("device", "connected")

    def _apply_brightness(self):
        if self.device and self.cfg.device.brightness is not None:
            try:
                self.device.set_brightness(self.cfg.device.brightness)
            except DeviceError as e:
                log.warning("could not set brightness: %s", e)

    def _device_lost(self, err: Exception, now: float):
        log.warning("key module lost: %s", err)
        self._notify("device", f"lost: {err}")
        self._close_device()
        self._device_retry_at = now + DEVICE_RETRY_SECONDS
        # release anything that was held so on_up actions (e.g. hold-to-mute) still run
        for idx, k in enumerate(self.gestures.keys):
            if k.down:
                for i, ev in self.gestures.edge(idx, False, now):
                    self._fire(i, ev)

    def _close_device(self):
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
        self.device = None
