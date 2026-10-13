"""The focused window: GetForegroundWindow, its process name (psutil) and its title. And
bringing an app's window to the front (launch.focus).

Reading another process's window title doesn't send it a message (Windows keeps the caption),
so a hung app can't block this.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

import psutil

from ..base import FocusedWindow, FocusReader
from .keyboard import _INPUT, _send

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
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x80, 0x40000
DWMWA_CLOAKED = 14                            # hidden by DWM: suspended Store apps, other virtual desktops
SW_RESTORE = 9

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


def _app_key(name: str) -> str:
    return name.strip().casefold().removesuffix(".exe")


def pick_window(windows: list[int], foreground: int | None) -> int | None:
    """Which of an app's windows (front to back) to bring up: the front one, or while the app is
    in front already, the one at the back, so presses go round all of them."""
    if not windows:
        return None
    return windows[-1] if foreground in windows else windows[0]


class WinWindows(WinFocus):
    """An app's windows as Alt+Tab lists them, and bringing one to the front."""

    def __init__(self):
        super().__init__()
        u = self.user32
        u.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
        for f in (u.IsWindowVisible, u.IsIconic, u.SetForegroundWindow):
            f.argtypes = [wintypes.HWND]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.dwm = ctypes.WinDLL("dwmapi")
        self.dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    def windows(self, app: str) -> list[int]:
        """`app`'s windows that Alt+Tab shows, front to back (EnumWindows goes in z-order). The
        name compares in any case, with or without .exe, as in pages' `match`."""
        want, names, found = _app_key(app), {}, []

        def visit(hwnd, _):
            if self._listed(hwnd):
                pid = self._pid(hwnd)
                if self._class(hwnd) == STORE_FRAME:
                    pid = self._store_app(hwnd, pid) or pid
                if pid not in names:
                    try:
                        names[pid] = _app_key(psutil.Process(pid).name())
                    except psutil.Error:
                        names[pid] = None
                if names[pid] == want:
                    found.append(hwnd)
            return True

        self.user32.EnumWindows(_ENUM_PROC(visit), 0)
        return found

    def activate(self, app: str) -> bool:
        hwnd = pick_window(self.windows(app), self.user32.GetForegroundWindow())
        if hwnd is None:
            return False
        try:
            _send([_INPUT()])                 # an empty mouse input: Windows lets the process with the
        except OSError:                       # last input take the foreground, and refuses others
            pass                              # (blocked by an elevated window in front: try anyway)
        if self.user32.IsIconic(hwnd):
            self.user32.ShowWindow(hwnd, SW_RESTORE)
        self.user32.SetForegroundWindow(hwnd)
        return True

    def _listed(self, hwnd) -> bool:
        """Visible, not cloaked, and a main window: unowned and not a tool window, or marked as
        an app window."""
        if not self.user32.IsWindowVisible(hwnd):
            return False
        cloaked = wintypes.DWORD(0)
        if not self.dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4) and cloaked.value:
            return False
        style = self.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if style & WS_EX_APPWINDOW:
            return True
        return not style & WS_EX_TOOLWINDOW and not self.user32.GetWindow(hwnd, GW_OWNER)
