"""Windows: %APPDATA%, a named mutex, ShellExecute, named pipes, pycaw, SendInput, the Run key,
the foreground window, media sessions."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from ..base import IpcConnection, Platform, Unsupported

CREATE_NO_WINDOW = 0x08000000
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"     # what Task Manager's Startup apps lists
RUN_VALUE = "hubdeck"
MUTEX_NAME = "Local\\hubdeck-single-instance"
ACCENT_KEY =r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent"
DWM_KEY = r"Software\Microsoft\Windows\DWM"
HWINFO_KEY = r"Software\HWiNFO64\VSB"                        # HWiNFO's Gadget: LabelN, ValueRawN from N = 0


def tray_exe() -> Path | None:
    """The hubdeck-tray.exe running now, to start at login: the app's own launcher
    (sys.executable), or pip's script in a venv (sys.argv[0]). None when started another way."""
    for candidate in (sys.executable, sys.argv[0] if sys.argv else ""):
        path = Path(candidate) if candidate else None
        if path and path.name.lower() == "hubdeck-tray.exe" and path.is_file():
            return path.resolve()
    return None


class NamedPipeConnection(IpcConnection):
    """Reads only when PeekNamedPipe says data is waiting: a blocking read on a
    pipe handle would also block writes from other threads."""

    def __init__(self, name: str):
        self.pipe = open(rf"\\?\pipe\{name}", "r+b", buffering=0)

    def available(self) -> int:
        import msvcrt
        from ctypes import wintypes
        avail = wintypes.DWORD(0)
        h = msvcrt.get_osfhandle(self.pipe.fileno())
        ok = ctypes.windll.kernel32.PeekNamedPipe(
            wintypes.HANDLE(h), None, 0, None, ctypes.byref(avail), None)
        if not ok:
            raise OSError("the other side closed the pipe")
        return avail.value

    def read(self, n: int) -> bytes:
        return self.pipe.read(n)

    def write(self, data: bytes):
        self.pipe.write(data)

    def close(self):
        self.pipe.close()


class WindowsPlatform(Platform):
    name = "Windows"

    def __init__(self):
        self._mutex = None
        self._windows = None

    def app_data_dir(self) -> Path:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")

    def acquire_single_instance(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS: another hubdeck has it
            kernel32.CloseHandle(handle)        # don't keep it alive after that one quits
            return False
        self._mutex = handle
        return True

    def open(self, target: str):
        os.startfile(target)

    def edit(self, path: Path):
        for verb in ("edit", "open"):
            try:
                os.startfile(path, verb)
                return
            except OSError:
                continue
        subprocess.Popen(["notepad.exe", str(path)])  # no editor registered for .yaml

    def run(self, cmd: str, cwd: str | None = None):
        subprocess.Popen(cmd, shell=True, cwd=cwd, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def activate(self, app: str) -> bool:
        if self._windows is None:
            from .focus import WinWindows
            self._windows = WinWindows()
        return self._windows.activate(app)

    def message_box(self, text: str):
        ctypes.windll.user32.MessageBoxW(None, text, "hubdeck", 0x40)

    def ipc_connect(self, name: str) -> IpcConnection:
        return NamedPipeConnection(name)

    def label_fonts(self):
        return [("segoeuib.ttf", None), ("arialbd.ttf", None)]

    def audio(self):
        from .audio import WinAudio
        return WinAudio()

    def keyboard(self):
        from .keyboard import SendInputKeyboard
        return SendInputKeyboard()

    def focus(self):
        from .focus import WinFocus
        return WinFocus()

    def media(self):
        from .media import WinMedia
        return WinMedia()

    def hwinfo(self) -> dict[str, float]:
        """The first reading of each label wins; HWiNFO writes the raw values with the
        system's decimal separator."""
        import winreg
        out: dict[str, float] = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HWINFO_KEY) as key:
                for n in range(1000):
                    label, _ = winreg.QueryValueEx(key, f"Label{n}")
                    raw, _ = winreg.QueryValueEx(key, f"ValueRaw{n}")
                    try:
                        out.setdefault(label, float(str(raw).replace(",", ".")))
                    except ValueError:
                        pass
        except OSError:                                # no key, or past the last reading
            pass
        return out

    def accent_color(self) -> tuple[int, int, int] | None:
        """The accent colour from Settings > Personalization > Colors. AccentPalette holds 8
        shades of it, RGBA; the 4th is the accent itself (what apps get as UISettings' Accent).
        DWM's AccentColor (ABGR) is the same colour, kept as a fallback."""
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ACCENT_KEY) as key:
                palette, _ = winreg.QueryValueEx(key, "AccentPalette")
            if isinstance(palette, bytes) and len(palette) >= 16:
                return tuple(palette[12:15])
        except OSError:
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DWM_KEY) as key:
                abgr, _ = winreg.QueryValueEx(key, "AccentColor")
        except OSError:
            return None
        return (abgr & 0xFF, (abgr >> 8) & 0xFF, (abgr >> 16) & 0xFF)

    def start_at_login(self) -> bool | None:
        exe = tray_exe()
        if exe is None:
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, RUN_VALUE)
        except OSError:
            return False
        return str(value).strip().strip('"').lower() == str(exe).lower()   # not another copy of hubdeck

    def set_start_at_login(self, on: bool):
        import winreg
        exe = tray_exe()
        if on and exe is None:
            raise Unsupported("starting at login needs hubdeck-tray.exe")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, f'"{exe}"')
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
