"""Audio: speaker/microphone mute and volume, per-app volume, default device switching.
The OS side is platforms.current().audio()."""

from __future__ import annotations

from ..platforms import Unsupported, current
from .base import ThreadedPlugin, adjusted, value_state

FOCUSED = "focused"     # app_volume arg: whichever app has focus
KINDS = {"speakers": "speakers", "speaker": "speakers", "output": "speakers",
         "microphone": "microphone", "mic": "microphone", "input": "microphone"}


def _kind(arg) -> str:
    if arg is None:
        return "speakers"
    if isinstance(arg, dict):
        arg = arg.get("device")
    return KINDS[str(arg).lower()]


def _app_arg(arg) -> str | None:
    """The app of a mute argument like {app: spotify.exe} or {app: focused}; None for a device."""
    return arg["app"] if isinstance(arg, dict) and "app" in arg else None


class AudioPlugin(ThreadedPlugin):
    name = "audio"
    interval = 0.5
    actions = {
        "toggle_mute": "toggle mute; argument: speakers (default), microphone, or {app: spotify.exe} "
                       "(or {app: focused}) for one app",
        "mute": "mute speakers/microphone/{app: ...}", "unmute": "unmute speakers/microphone/{app: ...}",
        "volume_up": "raise speaker volume; argument: step in % (default 5)",
        "volume_down": "lower speaker volume; argument: step in % (default 5)",
        "set_volume": "set speaker volume; argument: 0-100",
        "set_output": "make the playback device whose name contains the argument the default",
        "set_input": "make the recording device whose name contains the argument the default",
    }
    states = {
        "speaker_muted": "default playback device muted",
        "mic_muted": "default recording device muted",
        "volume": "default playback volume 0-100",
        "output": "name of the default playback device",
        "mic_volume": "default recording volume 0-100",
        "input": "name of the default recording device",
    }
    values = {
        "volume": "default playback volume 0-100",
        "mic_volume": "default recording (microphone) volume 0-100",
        "app_volume": "volume of one app 0-100; arg: process name, e.g. spotify.exe, or focused: "
                      "the focused window's app (macOS: apps with an AppleScript volume, e.g. Spotify, Music)",
    }

    @classmethod
    def check_value(cls, name, arg):
        if name == "app_volume" and not (isinstance(arg, str) and arg.strip()):
            return "audio.app_volume needs arg: the app's process name, e.g. spotify.exe, or focused"
        if name in ("volume", "mic_volume") and arg is not None:
            return f"audio.{name} takes no arg"
        return None

    @classmethod
    def check_action(cls, action, arg):
        if action in ("toggle_mute", "mute", "unmute"):
            if isinstance(arg, dict) and "app" in arg:
                if set(arg) != {"app"} or not (isinstance(arg["app"], str) and arg["app"].strip()):
                    return f"audio.{action}: {{app: ...}} needs the app's process name, e.g. spotify.exe, or focused"
                return None
            try:
                _kind(arg)
            except KeyError:
                return f"audio.{action}: argument must be speakers, microphone or {{app: <process name>}}"
        elif action in ("volume_up", "volume_down") and arg is not None and not isinstance(arg, (int, float)):
            return f"audio.{action}: argument must be a number (percent)"
        elif action == "set_volume" and not (isinstance(arg, (int, float)) and 0 <= arg <= 100):
            return "audio.set_volume needs a number 0-100"
        elif action in ("set_output", "set_input") and not isinstance(arg, str):
            return f"audio.{action} needs (part of) a device name"
        return None

    def setup(self):
        self._unavailable: set[tuple[str, object]] = set()
        self.backend = current().audio()   # on this thread: COM on Windows

    def teardown(self):
        self.backend.close()

    def handle(self, action, arg):
        b = self.backend
        if action in ("toggle_mute", "mute", "unmute") and (app := _app_arg(arg)) is not None:
            self._mute_app(action, app)
        elif action in ("toggle_mute", "mute", "unmute"):
            kind = _kind(arg)
            b.set_mute(kind, {"mute": True, "unmute": False}.get(action, not b.get_mute(kind)))
        elif action in ("volume_up", "volume_down", "set_volume"):
            if action == "set_volume":
                level = float(arg)
            else:
                step = float(arg if arg is not None else 5)
                level = b.get_volume("speakers") + (step if action == "volume_up" else -step)
            b.set_volume("speakers", level)
        elif action in ("set_output", "set_input"):
            kind, what = ("speakers", "playback") if action == "set_output" else ("microphone", "recording")
            name = b.set_default(kind, arg)
            if name:
                self.log.info("default %s device -> %s", what, name)
            else:
                self.log.warning("no active %s device matching %r", what, arg)
        self.tick()

    def _mute_app(self, action: str, arg: str):
        app = self._app(arg)
        try:
            muted = None if app is None else self.backend.get_app_mute(app)
            if muted is None:
                self.log.info("%s %s: not available (app not playing audio?)", action, app or arg)
                return
            mute = {"mute": True, "unmute": False}.get(action, not muted)
            self.backend.set_app_mute(app, mute)
        except Unsupported as e:
            self.log.warning("%s %s: %s", action, app, e)
            return
        self.log.info("%s %s", "muted" if mute else "unmuted", app)

    # -- values ------------------------------------------------------------
    def _app(self, arg) -> str | None:
        """The app an app_volume arg names. `focused` is the focused window's app, the last one
        that wasn't the taskbar, desktop or hubdeck itself (None until hubdeck has seen one)."""
        if str(arg).strip().lower() != FOCUSED:
            return arg
        focused = self.ctx.deck.focused if self.ctx.deck else None
        return focused.app if focused else None

    def _get(self, name, arg) -> float | None:
        if name == "app_volume":
            app = self._app(arg)
            return None if app is None else self.backend.get_app_volume(app)
        return self.backend.get_volume("microphone" if name == "mic_volume" else "speakers")

    def _set(self, name, arg, level: float):
        if name == "app_volume":
            if (app := self._app(arg)) is not None:
                self.backend.set_app_volume(app, level)
        else:
            self.backend.set_volume("microphone" if name == "mic_volume" else "speakers", level)

    def adjust_value(self, name, arg, delta, expo):
        current = self._get(name, arg)
        which = (name, self._app(arg) if name == "app_volume" else arg)   # focused: the app it is now
        if current is None:
            if which not in self._unavailable:            # once, not on every click
                self.log.info("%s %s: not available (app not playing audio?)", name, which[1] or "")
                self._unavailable.add(which)
            return
        self._unavailable.discard(which)
        self._set(name, arg, adjusted(current, delta, expo))
        self._publish_value(name, arg)

    def set_value(self, name, arg, value):
        self._set(name, arg, value)
        self._publish_value(name, arg)

    def _publish_value(self, name, arg):
        try:
            v = self._get(name, arg)
        except Exception:
            v = None
        self.ctx.set_state(value_state(name, arg), None if v is None else round(v))

    def tick(self):
        b = self.backend
        try:
            self.ctx.set_state("speaker_muted", b.get_mute("speakers"))
            self.ctx.set_state("volume", round(b.get_volume("speakers")))
            self.ctx.set_state("output", b.default_name("speakers"))
        except Exception:  # no device, or a COMError while devices change
            for k in ("speaker_muted", "volume", "output"):
                self.ctx.set_state(k, None)
        try:
            self.ctx.set_state("mic_muted", b.get_mute("microphone"))
            self.ctx.set_state("mic_volume", round(b.get_volume("microphone")))
            self.ctx.set_state("input", b.default_name("microphone"))
        except Exception:
            for k in ("mic_muted", "mic_volume", "input"):
                self.ctx.set_state(k, None)
        for name, arg in list(self.watched):
            self._publish_value(name, arg)
