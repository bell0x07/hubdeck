"""Only one hubdeck may drive the module at a time."""

from __future__ import annotations

import sys

_handle = None


def acquire() -> bool:
    """True if this is the only running hubdeck (Windows named mutex)."""
    global _handle
    if sys.platform != "win32":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _handle = kernel32.CreateMutexW(None, False, "Local\\hubdeck-single-instance")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
