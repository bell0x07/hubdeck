"""Windows audio: speaker/microphone mute and volume, per-app volume, default output switching."""

from __future__ import annotations

from .base import ThreadedPlugin, value_state

KINDS = {"speakers": "speakers", "speaker": "speakers", "output": "speakers",
         "microphone": "microphone", "mic": "microphone", "input": "microphone"}


def _kind(arg) -> str:
    if arg is None:
        return "speakers"
    if isinstance(arg, dict):
        arg = arg.get("device")
    return KINDS[str(arg).lower()]


class AudioPlugin(ThreadedPlugin):
    name = "audio"
    interval = 0.5
    actions = {
        "toggle_mute": "toggle mute; argument: speakers (default) or microphone",
        "mute": "mute speakers/microphone", "unmute": "unmute speakers/microphone",
        "volume_up": "raise speaker volume; argument: step in % (default 5)",
        "volume_down": "lower speaker volume; argument: step in % (default 5)",
        "set_volume": "set speaker volume; argument: 0-100",
        "set_output": "make the playback device whose name contains the argument the default",
    }
    states = {
        "speaker_muted": "default playback device muted",
        "mic_muted": "default recording device muted",
        "volume": "default playback volume 0-100",
        "output": "name of the default playback device",
        "mic_volume": "default recording volume 0-100",
    }
    values = {
        "volume": "default playback volume 0-100",
        "mic_volume": "default recording (microphone) volume 0-100",
        "app_volume": "volume of one app 0-100; arg: process name, e.g. spotify.exe",
    }

    @classmethod
    def check_value(cls, name, arg):
        if name == "app_volume" and not (isinstance(arg, str) and arg.strip()):
            return "audio.app_volume needs arg: the app's process name, e.g. spotify.exe"
        if name in ("volume", "mic_volume") and arg is not None:
            return f"audio.{name} takes no arg"
        return None

    @classmethod
    def check_action(cls, action, arg):
        if action in ("toggle_mute", "mute", "unmute"):
            try:
                _kind(arg)
            except KeyError:
                return f"audio.{action}: argument must be speakers or microphone"
        elif action in ("volume_up", "volume_down") and arg is not None and not isinstance(arg, (int, float)):
            return f"audio.{action}: argument must be a number (percent)"
        elif action == "set_volume" and not (isinstance(arg, (int, float)) and 0 <= arg <= 100):
            return "audio.set_volume needs a number 0-100"
        elif action == "set_output" and not isinstance(arg, str):
            return "audio.set_output needs (part of) a device name"
        return None

    def setup(self):
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()
        self.au = AudioUtilities

    def teardown(self):
        import comtypes
        comtypes.CoUninitialize()

    def _endpoint(self, kind: str):
        if kind == "microphone":
            raw = self.au.GetMicrophone()  # pycaw returns the raw IMMDevice here, unlike GetSpeakers
            dev = self.au.CreateDevice(raw) if raw is not None else None
        else:
            dev = self.au.GetSpeakers()
        if dev is None:
            raise OSError(f"no default {kind} device")
        return dev

    def handle(self, action, arg):
        if action in ("toggle_mute", "mute", "unmute"):
            ep = self._endpoint(_kind(arg)).EndpointVolume
            mute = {"mute": True, "unmute": False}.get(action, not ep.GetMute())
            ep.SetMute(int(mute), None)
        elif action in ("volume_up", "volume_down", "set_volume"):
            ep = self._endpoint("speakers").EndpointVolume
            if action == "set_volume":
                level = float(arg)
            else:
                step = float(arg if arg is not None else 5)
                level = ep.GetMasterVolumeLevelScalar() * 100 + (step if action == "volume_up" else -step)
            ep.SetMasterVolumeLevelScalar(max(0.0, min(100.0, level)) / 100, None)
        elif action == "set_output":
            want = arg.lower()
            for dev in self.au.GetAllDevices():
                name = dev.FriendlyName or ""
                if want in name.lower() and "active" in str(dev.state).lower() and self._is_output(dev):
                    self.au.SetDefaultDevice(dev.id)
                    self.log.info("default output -> %s", name)
                    break
            else:
                self.log.warning("no active playback device matching %r", arg)
        self.tick()

    # -- values ------------------------------------------------------------
    def _sessions(self, app: str):
        want = app.lower()
        for s in self.au.GetAllSessions():
            proc = s.Process
            if proc is None:
                continue
            name = proc.name().lower()
            if name == want or name.removesuffix(".exe") == want.removesuffix(".exe"):
                yield s

    def _get(self, name, arg) -> float | None:
        if name == "app_volume":
            levels = [s.SimpleAudioVolume.GetMasterVolume() for s in self._sessions(arg)]
            return max(levels) * 100 if levels else None
        ep = self._endpoint("microphone" if name == "mic_volume" else "speakers").EndpointVolume
        return ep.GetMasterVolumeLevelScalar() * 100

    def _set(self, name, arg, level: float):
        level = max(0.0, min(100.0, level)) / 100
        if name == "app_volume":
            for s in self._sessions(arg):
                s.SimpleAudioVolume.SetMasterVolume(level, None)
        else:
            ep = self._endpoint("microphone" if name == "mic_volume" else "speakers").EndpointVolume
            ep.SetMasterVolumeLevelScalar(level, None)

    def adjust_value(self, name, arg, delta):
        current = self._get(name, arg)
        if current is None:
            self.log.info("%s %s: not available (app not playing audio?)", name, arg or "")
            return
        self._set(name, arg, current + delta)
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

    def _is_output(self, dev) -> bool:
        # render endpoint ids start with {0.0.0.00000000}, capture ones with {0.0.1.00000000}
        return str(dev.id).startswith("{0.0.0.")

    def tick(self):
        try:
            spk = self._endpoint("speakers")
            ev = spk.EndpointVolume
            self.ctx.set_state("speaker_muted", bool(ev.GetMute()))
            self.ctx.set_state("volume", round(ev.GetMasterVolumeLevelScalar() * 100))
            self.ctx.set_state("output", spk.FriendlyName)
        except Exception:  # no device, or a COMError while devices change
            for k in ("speaker_muted", "volume", "output"):
                self.ctx.set_state(k, None)
        try:
            mic = self._endpoint("microphone").EndpointVolume
            self.ctx.set_state("mic_muted", bool(mic.GetMute()))
            self.ctx.set_state("mic_volume", round(mic.GetMasterVolumeLevelScalar() * 100))
        except Exception:
            self.ctx.set_state("mic_muted", None)
            self.ctx.set_state("mic_volume", None)
        for name, arg in list(self.watched):
            self._publish_value(name, arg)
