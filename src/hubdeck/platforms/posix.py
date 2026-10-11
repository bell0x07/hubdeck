"""What macOS and Linux share. PosixPlatform is also the fallback for any
other OS: files, locking, launching and IPC work; audio and keystrokes don't."""

from __future__ import annotations

import os
import select
import socket
import subprocess
import sys
from pathlib import Path

from .base import IpcConnection, Platform


class UnixSocketConnection(IpcConnection):
    def __init__(self, path: str):
        self.sock = socket.socket(socket.AF_UNIX)
        try:
            self.sock.connect(path)
        except OSError:
            self.sock.close()
            raise
        self.file = self.sock.makefile("rwb", buffering=0)

    def available(self) -> int:
        r, _, _ = select.select([self.sock], [], [], 0)
        return 8 if r else 0       # readable, size unknown: a frame header at least

    def read(self, n: int) -> bytes:
        return self.file.read(n)

    def write(self, data: bytes):
        self.file.write(data)

    def close(self):
        self.file.close()
        self.sock.close()


class PosixPlatform(Platform):
    name = sys.platform
    opener = "xdg-open"

    def __init__(self):
        self._lock = None

    def app_data_dir(self) -> Path:
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")

    def acquire_single_instance(self) -> bool:
        import fcntl

        from .. import paths
        paths.app_dir().mkdir(parents=True, exist_ok=True)
        self._lock = open(paths.app_dir() / ".lock", "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # the OS releases it when we exit
        except OSError:
            return False
        return True

    def open(self, target: str):
        subprocess.Popen([self.opener, target], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self, cmd: str, cwd: str | None = None):
        subprocess.Popen(cmd, shell=True, cwd=cwd, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def message_box(self, text: str):
        print(text, file=sys.stderr)

    def ipc_dirs(self) -> list[str]:
        return [os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"]

    def ipc_connect(self, name: str) -> IpcConnection:
        last: OSError = FileNotFoundError(name)
        for d in self.ipc_dirs():
            try:
                return UnixSocketConnection(os.path.join(d, name))
            except OSError as e:
                last = e
        raise last
