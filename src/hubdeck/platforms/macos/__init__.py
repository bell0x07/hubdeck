"""macOS: ~/Library/Application Support, `open`, CoreAudio, Quartz events, AppKit tray.

pyobjc (pulled in by pystray on macOS) is only imported inside functions.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..posix import PosixPlatform

_AX = None
_prompted = False


def accessibility_trusted(prompt: bool = False) -> bool:
    """True if this process may post keystrokes. With prompt, macOS shows its
    'allow in Privacy & Security > Accessibility' dialog (once per process)."""
    global _AX, _prompted
    if _AX is None:
        _AX = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        _AX.AXIsProcessTrusted.restype = ctypes.c_bool
        _AX.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        _AX.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
    if not prompt or _prompted:
        return _AX.AXIsProcessTrusted()
    _prompted = True
    import objc
    from Foundation import NSDictionary
    options = NSDictionary.dictionaryWithDictionary_({"AXTrustedCheckOptionPrompt": True})
    return _AX.AXIsProcessTrustedWithOptions(objc.pyobjc_id(options))


def osascript(script: str, timeout: float = 5) -> str:
    """Run AppleScript; returns its output. Raises OSError with AppleScript's message."""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise OSError("AppleScript timed out")
    if r.returncode:
        raise OSError(r.stderr.strip() or f"osascript exited with {r.returncode}")
    return r.stdout.strip()


def quote(text: str) -> str:
    """An AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Resolves the accent colour in a fresh process: AppKit caches it per process and only refreshes
# the cache from an app event loop, which `hubdeck run` doesn't have.
_ACCENT_SCRIPT = ("from AppKit import NSColor, NSColorSpace; "
                  "c = NSColor.controlAccentColor().colorUsingColorSpace_(NSColorSpace.sRGBColorSpace()); "
                  "print(c.redComponent(), c.greenComponent(), c.blueComponent())")
# The settings that decide the accent colour: the colour choice, and light/dark mode.
_ACCENT_PREFS = ("AppleAccentColor", "AppleInterfaceStyle")


def _accent_prefs() -> tuple:
    """Current values of the accent settings, read live (CFPreferences sees changes at once)."""
    from CoreFoundation import CFPreferencesAppSynchronize, CFPreferencesCopyAppValue, kCFPreferencesAnyApplication
    CFPreferencesAppSynchronize(kCFPreferencesAnyApplication)
    return tuple(CFPreferencesCopyAppValue(k, kCFPreferencesAnyApplication) for k in _ACCENT_PREFS)


def _resolve_accent() -> tuple[int, int, int]:
    # a clean environment instead of -I: inside hubdeck.app, sys.executable is the app's launcher,
    # which acts as plain Python with HUBDECK_PYTHON=1 and needs its own PYTHONHOME
    env = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin", "HUBDECK_PYTHON": "1"}
    r = subprocess.run([sys.executable, "-c", _ACCENT_SCRIPT], capture_output=True, text=True, timeout=10, env=env)
    return tuple(round(max(0.0, min(1.0, float(x))) * 255) for x in r.stdout.split())


def in_app_bundle() -> bool:
    """True when running as hubdeck.app (its own identity: notifications, login item)."""
    from Foundation import NSBundle

    from ..._bundle import BUNDLE_ID
    return NSBundle.mainBundle().bundleIdentifier() == BUNDLE_ID


class MacPlatform(PosixPlatform):
    name = "macOS"
    opener = "open"

    def __init__(self):
        super().__init__()
        self._accent: tuple[tuple, tuple[int, int, int] | None] = ((), None)   # (settings, colour)
        self._notify_asked = False

    def app_data_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support"

    def open(self, target: str):
        """Paths and URLs as usual; a bare name that isn't a path opens that app ('Spotify')."""
        target = os.path.expanduser(target)
        if ":" in target or os.path.exists(target):
            super().open(target)
        else:
            subprocess.Popen(["open", "-a", target], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def edit(self, path: Path):
        subprocess.Popen(["open", "-t", str(path)])   # the default text editor, whatever owns .yaml

    def message_box(self, text: str):
        """A notification: an alert from osascript, a background helper, opens behind other windows."""
        super().message_box(text)          # also on stderr, for whoever started it from Terminal
        if self.notify("hubdeck", text):
            return
        try:
            osascript(f"display notification {quote(text)} with title \"hubdeck\"")
        except OSError:
            pass

    def notify(self, title: str, message: str) -> bool:
        """Notification Center, with hubdeck's icon: only inside hubdeck.app (it needs a bundle id)."""
        if not in_app_bundle():
            return False
        import uuid

        from UserNotifications import (UNAuthorizationOptionAlert, UNMutableNotificationContent,
                                       UNNotificationRequest, UNUserNotificationCenter)
        center = UNUserNotificationCenter.currentNotificationCenter()
        if not self._notify_asked:
            center.requestAuthorizationWithOptions_completionHandler_(UNAuthorizationOptionAlert, lambda ok, err: None)
            self._notify_asked = True
        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(message)
        request = UNNotificationRequest.requestWithIdentifier_content_trigger_(str(uuid.uuid4()), content, None)
        center.addNotificationRequest_withCompletionHandler_(request, None)
        return True

    def start_at_login(self) -> bool | None:
        if not in_app_bundle():
            return None
        try:
            from ServiceManagement import SMAppService   # macOS 13+
        except ImportError:
            return None
        return SMAppService.mainAppService().status() == 1   # SMAppServiceStatusEnabled

    def set_start_at_login(self, on: bool):
        from ServiceManagement import SMAppService
        service = SMAppService.mainAppService()
        ok, error = (service.registerAndReturnError_ if on else service.unregisterAndReturnError_)(None)
        if not ok:
            raise OSError(f"could not change the login item: {error}")

    def ipc_dirs(self) -> list[str]:
        # Discord puts its socket in the per-user $TMPDIR (/var/folders/...)
        return [d for d in (os.environ.get("TMPDIR"), "/tmp") if d]

    def audio(self):
        from .audio import MacAudio
        return MacAudio()

    def keyboard(self):
        from .keyboard import QuartzKeyboard
        return QuartzKeyboard()

    def run_shortcut(self, name, input=None, timeout=60):
        with tempfile.TemporaryDirectory(prefix="hubdeck-") as tmp:
            out = Path(tmp) / "output.txt"
            cmd = ["shortcuts", "run", name, "--output-path", str(out), "--output-type", "public.plain-text"]
            if input is not None:
                src = Path(tmp) / "input.txt"
                src.write_text(str(input), encoding="utf-8")
                cmd += ["--input-path", str(src)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise OSError(f"Shortcut {name!r} didn't finish within {timeout:g} s")
            if r.returncode:
                raise OSError(f"Shortcut {name!r}: {r.stderr.strip() or f'failed ({r.returncode})'}")
            return out.read_text(encoding="utf-8", errors="replace").strip() if out.exists() else ""

    def shortcut_names(self):
        r = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=10)
        return sorted({line.strip() for line in r.stdout.splitlines() if line.strip()}, key=str.lower)

    def accent_color(self):
        """Cheap to call often: the colour is only resolved again when the settings change."""
        prefs = _accent_prefs()
        if prefs != self._accent[0] or self._accent[1] is None:
            rgb = _resolve_accent()
            if len(rgb) != 3:
                raise OSError("could not read the accent colour")
            self._accent = (prefs, rgb)
        return self._accent[1]

    def label_fonts(self):
        return [("/System/Library/Fonts/SFNS.ttf", "Bold"), ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", None)]

    def prepare_tray(self):
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon

    def style_tray_icon(self, icon, glyph):
        """A template image like every other menu-bar icon: macOS tints it for light and dark
        menu bars. pystray only sets coloured bitmaps, so this replaces the button's image."""
        import io

        from AppKit import NSImage
        from Foundation import NSData
        png = io.BytesIO()
        glyph.save(png, "png")
        image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(png.getvalue(), len(png.getvalue())))
        image.setSize_((18, 18))      # points; the 64 px glyph keeps it sharp on Retina
        image.setTemplate_(True)
        icon._status_item.button().setImage_(image)   # pystray internals: it has no template option

    def call_on_ui_thread(self, fn):
        from PyObjCTools import AppHelper
        AppHelper.callAfter(fn)
