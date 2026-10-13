"""The focused window: GetForegroundWindow, its process name (psutil) and its title.

Reading another process's window title doesn't send it a message (Windows keeps the caption),
so a hung app can't block this.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

import psutil

from ..base import FocusedWindow, FocusReader

# explorer.exe windows that are the shell, not File Explorer (CabinetWClass)
SHELL_CLASSES = {
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd",   # taskbar
    "Progman", "WorkerW",                        # desktop
    "XamlExplorerHostIslandWindow",              # Alt+Tab and Task View (Windows 11)
    "MultitaskingViewFrame",                     # Alt+Tab (Windows 10)
}
# the shell's own processes: Start, search, notifications, the touch keyboard, lock screen, UAC, widgets
SHELL_APPS = {
    "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe", "searchui.exe",
    "shellexperiencehost.exe", "shellhost.exe", "textinputhost.exe",
    "lockapp.exe", "logonui.exe", "consent.exe", "widgets.exe", "widgetboard.exe",
    "applicationframehost.exe",                  # a Store app frame whose app wasn't found
}
STORE_FRAME = "ApplicationFrameWindow"
STORE_APP = "Windows.UI.Core.CoreWindow"

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class WinFocus(FocusReader):
    def __init__(self):
        u = ctypes.WinDLL("user32")           # own function prototypes, apart from ctypes.windll's
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetForegroundWindow.argtypes = []
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.EnumChildWindows.argtypes = [wintypes.HWND, _ENUM_PROC, wintypes.LPARAM]
        self.user32 = u
        self.own_pid = os.getpid()

    def read(self) -> FocusedWindow | None:
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None                       # switching windows, or the secure desktop (UAC, lock)
        cls = self._class(hwnd)
        pid = self._pid(hwnd)
        if cls == STORE_FRAME:
            pid = self._store_app(hwnd, pid) or pid
        if pid == self.own_pid:
            return None
        try:
            name = psutil.Process(pid).name()
        except psutil.Error:
            return None
        low = name.lower()
        if low in SHELL_APPS or (low == "explorer.exe" and cls in SHELL_CLASSES):
            return None
        return FocusedWindow(name, self._title(hwnd))

    def _pid(self, hwnd) -> int:
        pid = wintypes.DWORD(0)
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _class(self, hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buf, len(buf))
        return buf.value

    def _title(self, hwnd) -> str:
        n = self.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        self.user32.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value

    def _store_app(self, frame, frame_pid: int) -> int | None:
        """A Store app's window is a CoreWindow inside a frame that belongs to
        ApplicationFrameHost.exe; the app is the process that owns that CoreWindow."""
        found = []

        def visit(child, _):
            if self._class(child) == STORE_APP and (pid := self._pid(child)) != frame_pid:
                found.append(pid)
                return False
            return True

        self.user32.EnumChildWindows(frame, _ENUM_PROC(visit), 0)
        return found[0] if found else None
