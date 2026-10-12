"""The frontmost app (NSWorkspace) and its focused window's title (Accessibility).

NSWorkspace only refreshes the frontmost app while the main thread runs an event loop: the
tray has AppKit's, and `hubdeck run` keeps one going (MacPlatform.run_with_event_loop).
Titles need the Accessibility permission, the same one keystrokes need; the app answers
the request itself, so a hung app can delay it (up to TITLE_TIMEOUT).
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import byref, c_bool, c_float, c_int, c_int32, c_long, c_uint32, c_ulong, c_void_p
from pathlib import Path

from ..base import FocusedWindow, FocusReader
from . import accessibility_trusted

log = logging.getLogger("hubdeck")

AX_LIB = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
CF_LIB = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
UTF8 = 0x08000100
TITLE_TIMEOUT = 0.5      # seconds an app gets to answer
# frontmost but not an app you work in: lock screen, Dock, password prompts, Notification
# Center, Control Centre, Stage Manager, the screenshot tool
IGNORED = {
    "com.apple.loginwindow", "com.apple.dock", "com.apple.SecurityAgent", "com.apple.coreautha",
    "com.apple.notificationcenterui", "com.apple.controlcenter", "com.apple.WindowManager",
    "com.apple.screencaptureui",
}


class _Titles:
    """The focused window's title through the Accessibility API (ctypes: pyobjc's
    ApplicationServices wrapper isn't a dependency)."""

    def __init__(self):
        ax, cf = ctypes.CDLL(AX_LIB), ctypes.CDLL(CF_LIB)
        ax.AXUIElementCreateApplication.restype = c_void_p
        ax.AXUIElementCreateApplication.argtypes = [c_int]
        ax.AXUIElementCreateSystemWide.restype = c_void_p
        ax.AXUIElementCreateSystemWide.argtypes = []
        ax.AXUIElementCopyAttributeValue.restype = c_int32
        ax.AXUIElementCopyAttributeValue.argtypes = [c_void_p, c_void_p, ctypes.POINTER(c_void_p)]
        ax.AXUIElementSetMessagingTimeout.restype = c_int32
        ax.AXUIElementSetMessagingTimeout.argtypes = [c_void_p, c_float]
        cf.CFStringCreateWithCString.restype = c_void_p
        cf.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]
        cf.CFRelease.argtypes = [c_void_p]
        cf.CFGetTypeID.restype = c_ulong
        cf.CFGetTypeID.argtypes = [c_void_p]
        cf.CFStringGetTypeID.restype = c_ulong
        cf.CFStringGetLength.restype = c_long
        cf.CFStringGetLength.argtypes = [c_void_p]
        cf.CFStringGetMaximumSizeForEncoding.restype = c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [c_long, c_uint32]
        cf.CFStringGetCString.restype = c_bool
        cf.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, c_long, c_uint32]
        self.ax, self.cf = ax, cf
        self.focused_window = cf.CFStringCreateWithCString(None, b"AXFocusedWindow", UTF8)
        self.title = cf.CFStringCreateWithCString(None, b"AXTitle", UTF8)
        system = ax.AXUIElementCreateSystemWide()
        ax.AXUIElementSetMessagingTimeout(system, TITLE_TIMEOUT)    # for every element
        cf.CFRelease(system)

    def _copy(self, element, attribute) -> c_void_p | None:
        out = c_void_p()
        if self.ax.AXUIElementCopyAttributeValue(element, attribute, byref(out)) or not out.value:
            return None
        return out

    def _string(self, ref) -> str:
        cf = self.cf
        if cf.CFGetTypeID(ref) != cf.CFStringGetTypeID():
            return ""
        size = cf.CFStringGetMaximumSizeForEncoding(cf.CFStringGetLength(ref), UTF8) + 1
        buf = ctypes.create_string_buffer(size)
        return buf.value.decode("utf-8", "replace") if cf.CFStringGetCString(ref, buf, size, UTF8) else ""

    def of(self, pid: int) -> str:
        """The title of `pid`'s focused window; "" when it has none or doesn't answer."""
        app = self.ax.AXUIElementCreateApplication(pid)
        if not app:
            return ""
        try:
            window = self._copy(app, self.focused_window)
            if window is None:
                return ""
            try:
                title = self._copy(window, self.title)
                if title is None:
                    return ""
                try:
                    return self._string(title)
                finally:
                    self.cf.CFRelease(title)
            finally:
                self.cf.CFRelease(window)
        finally:
            self.cf.CFRelease(app)


class MacFocus(FocusReader):
    def __init__(self):
        self.own_pid = os.getpid()
        self._titles: _Titles | None = None
        self._told = False

    def read(self) -> FocusedWindow | None:
        import objc
        from AppKit import NSApplicationActivationPolicyRegular, NSWorkspace
        with objc.autorelease_pool():
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            pid = app.processIdentifier()
            bundle_id = str(app.bundleIdentifier() or "")
            if (pid == self.own_pid or bundle_id in IGNORED
                    or app.activationPolicy() != NSApplicationActivationPolicyRegular):
                return None
            name = str(app.localizedName() or "")
            aliases = [bundle_id]
            for url in (app.bundleURL(), app.executableURL()):
                if url is not None and url.path():
                    aliases.append(Path(str(url.path())).name)     # Visual Studio Code.app, Code
        name = name or next((a for a in aliases if a), "")
        aliases = tuple(dict.fromkeys(a for a in aliases if a and a != name))
        return FocusedWindow(name, self._title(pid), aliases)

    def _title(self, pid: int) -> str | None:
        if not accessibility_trusted():
            if not self._told:
                self._told = True
                log.info("window titles need the Accessibility permission "
                         "(System Settings > Privacy & Security > Accessibility)")
            return None
        if self._titles is None:
            self._titles = _Titles()
        return self._titles.of(pid)
