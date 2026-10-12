"""OS-specific code lives behind `current()`. See base.py for the interface."""

from __future__ import annotations

import sys
from functools import cache

from .base import AudioBackend, FocusedWindow, FocusReader, IpcConnection, Keyboard, Platform, Unsupported


@cache
def current() -> Platform:
    if sys.platform == "win32":
        from .windows import WindowsPlatform
        return WindowsPlatform()
    if sys.platform == "darwin":
        from .macos import MacPlatform
        return MacPlatform()
    from .posix import PosixPlatform    # Linux and others: the POSIX basics, no audio/keystrokes yet
    return PosixPlatform()


__all__ = ["AudioBackend", "FocusedWindow", "FocusReader", "IpcConnection", "Keyboard", "Platform", "Unsupported", "current"]
