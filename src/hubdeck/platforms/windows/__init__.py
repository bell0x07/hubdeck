"""Windows: %APPDATA%, a named mutex, ShellExecute, named pipes, pycaw, SendInput."""

from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path

from ..base import IpcConnection, Platform

CREATE_NO_WINDOW = 0x08000000


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

    def app_data_dir(self) -> Path:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")

    def acquire_single_instance(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        self._mutex = kernel32.CreateMutexW(None, False, "Local\\hubdeck-single-instance")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS

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
