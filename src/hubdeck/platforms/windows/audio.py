"""Windows audio through pycaw (Core Audio COM)."""

from __future__ import annotations

import os

from ..base import AudioBackend

WINDOWS_DIR = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")) + os.sep


class WinAudio(AudioBackend):
    """pycaw: default endpoints, per-app sessions. Volumes are 0-100."""

    def __init__(self):
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()
        self.au = AudioUtilities
        self._owners: dict[tuple[int, float], list[str]] = {}

    def close(self):
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

    def get_volume(self, kind: str) -> float:
        return self._endpoint(kind).EndpointVolume.GetMasterVolumeLevelScalar() * 100

    def set_volume(self, kind: str, level: float):
        self._endpoint(kind).EndpointVolume.SetMasterVolumeLevelScalar(max(0.0, min(100.0, level)) / 100, None)

    def get_mute(self, kind: str) -> bool:
        return bool(self._endpoint(kind).EndpointVolume.GetMute())

    def set_mute(self, kind: str, mute: bool):
        self._endpoint(kind).EndpointVolume.SetMute(int(mute), None)

    def default_name(self, kind: str) -> str:
        return self._endpoint(kind).FriendlyName

    def _devices(self, kind: str):
        # render endpoint ids start with {0.0.0.00000000}, capture ones with {0.0.1.00000000}
        prefix = "{0.0.1." if kind == "microphone" else "{0.0.0."
        for dev in self.au.GetAllDevices():
            if dev.FriendlyName and "active" in str(dev.state).lower() and str(dev.id).startswith(prefix):
                yield dev

    def devices(self, kind: str) -> list[str]:
        return sorted({d.FriendlyName for d in self._devices(kind)})

    def set_default(self, kind: str, want: str) -> str | None:
        for dev in self._devices(kind):
            if want.lower() in dev.FriendlyName.lower():
                self.au.SetDefaultDevice(dev.id)
                return dev.FriendlyName
        return None

    @staticmethod
    def _owner_names(proc) -> list[str]:
        """The process's name and those of the apps that started it, up to Windows' own
        processes (Explorer, svchost): new Teams plays through a msedgewebview2.exe it starts."""
        import psutil
        names = [proc.name()]
        try:
            for parent in proc.parents():
                if os.path.normcase(parent.exe()).startswith(WINDOWS_DIR):
                    break
                names.append(parent.name())
        except psutil.Error:               # gone, or a process we may not inspect
            pass
        return names

    def _app_sessions(self) -> list[tuple[object, list[str]]]:
        """Every app's session with its owner names, cached per process."""
        import psutil
        out, owners = [], {}
        for s in self.au.GetAllSessions():
            proc = s.Process
            if proc is None:
                continue
            try:
                key = (proc.pid, proc.create_time())
                owners[key] = self._owners.get(key) or self._owner_names(proc)
            except psutil.Error:
                continue
            out.append((s, owners[key]))
        self._owners = owners
        return out

    def _sessions(self, app: str):
        want = app.lower().removesuffix(".exe")
        return [s for s, names in self._app_sessions()
                if any(n.lower().removesuffix(".exe") == want for n in names)]

    def apps(self) -> list[str]:
        return sorted({n for _, names in self._app_sessions() for n in names}, key=str.lower)

    def get_app_volume(self, app: str) -> float | None:
        levels = [s.SimpleAudioVolume.GetMasterVolume() for s in self._sessions(app)]
        return max(levels) * 100 if levels else None

    def set_app_volume(self, app: str, level: float):
        for s in self._sessions(app):
            s.SimpleAudioVolume.SetMasterVolume(max(0.0, min(100.0, level)) / 100, None)

    def get_app_mute(self, app: str) -> bool | None:
        muted = [bool(s.SimpleAudioVolume.GetMute()) for s in self._sessions(app)]
        return all(muted) if muted else None

    def set_app_mute(self, app: str, mute: bool):
        for s in self._sessions(app):
            s.SimpleAudioVolume.SetMute(int(mute), None)
