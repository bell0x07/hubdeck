"""What hubdeck needs from the operating system.

Each OS implements `Platform` in its own package (windows/, macos/); POSIX
pieces shared by macOS and Linux are in posix.py. Anything an OS can't do
raises `Unsupported`, which callers log as a normal failure.

Heavy or OS-only imports (pycaw, pyobjc, ...) happen inside methods, never at
import time: configs are validated on every OS.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class Unsupported(OSError):
    pass


class AudioBackend(ABC):
    """Default speakers/microphone and per-app volume. `kind` is "speakers" or
    "microphone"; volumes are 0-100. Created and used on one thread."""

    @abstractmethod
    def get_volume(self, kind: str) -> float: ...

    @abstractmethod
    def set_volume(self, kind: str, level: float): ...

    @abstractmethod
    def get_mute(self, kind: str) -> bool: ...

    @abstractmethod
    def set_mute(self, kind: str, mute: bool): ...

    @abstractmethod
    def default_name(self, kind: str) -> str:
        """Name of the default device."""

    @abstractmethod
    def devices(self, kind: str) -> list[str]:
        """Names of the devices that can be made the default."""

    @abstractmethod
    def set_default(self, kind: str, want: str) -> str | None:
        """Make the first device whose name contains `want` (any case) the default; its name, or None."""

    def apps(self) -> list[str]:
        """Apps whose volume can be read right now (for the editor's picker)."""
        return []

    def get_app_volume(self, app: str) -> float | None:
        """None when the app has no volume right now (not running or not playing)."""
        return None

    def set_app_volume(self, app: str, level: float):
        pass

    def get_app_mute(self, app: str) -> bool | None:
        """Whether every audio session of the app is muted; None when it has no audio right now."""
        raise Unsupported("muting one app isn't supported on this system")

    def set_app_mute(self, app: str, mute: bool):
        raise Unsupported("muting one app isn't supported on this system")

    def close(self):
        pass


class Keyboard(ABC):
    """Synthetic keystrokes. Keys are canonical names from platforms.keys."""

    @abstractmethod
    def down(self, keys: list[str]):
        """Press keys in order and keep them held."""

    @abstractmethod
    def up(self, keys: list[str]):
        """Release keys in reverse order."""

    def press(self, keys: list[str]):
        self.down(keys)
        self.up(keys)

    @abstractmethod
    def type_text(self, text: str): ...


@dataclass(frozen=True)
class FocusedWindow:
    """The window with keyboard focus, as pages match it."""
    app: str                         # what hubdeck calls the app: the process name (X2.EXE); macOS: Safari
    title: str | None = None         # None: titles can't be read (macOS without Accessibility)
    aliases: tuple[str, ...] = ()    # other names that match the app (macOS: bundle id, executable)


class FocusReader(ABC):
    """Which window has keyboard focus. Used on one thread of its own: read() may block for a
    moment while the app answers (macOS asks the app for its window title)."""

    @abstractmethod
    def read(self) -> FocusedWindow | None:
        """The focused window, or None when there's nothing to switch pages for: no window, the
        system's own surfaces (taskbar, desktop, Alt+Tab, lock screen) or hubdeck itself."""


@dataclass(frozen=True)
class NowPlaying:
    """The media session the media keys control."""
    app: str                         # the app's name as the Start menu shows it (Spotify, Brave)
    playing: bool                    # False: paused or stopped
    title: str | None = None
    artist: str | None = None


class MediaReader(ABC):
    """What's playing. Created and used on one thread of its own."""

    @abstractmethod
    def read(self) -> NowPlaying | None:
        """The session the media keys control, or None when no app has one."""

    def close(self):
        pass


class IpcConnection(ABC):
    """A local byte stream to another app (Discord's RPC pipe/socket)."""

    @abstractmethod
    def available(self) -> int:
        """Bytes that can be read without blocking (on POSIX: 8 if any). Raises OSError once closed."""

    @abstractmethod
    def read(self, n: int) -> bytes: ...

    @abstractmethod
    def write(self, data: bytes): ...

    @abstractmethod
    def close(self): ...


class Platform(ABC):
    name: str

    @abstractmethod
    def app_data_dir(self) -> Path:
        """Folder that holds the hubdeck folder (config, log, cache)."""

    @abstractmethod
    def acquire_single_instance(self) -> bool:
        """True if no other hubdeck is running. Holds the lock until the process exits."""

    @abstractmethod
    def open(self, target: str):
        """Open a file, folder, app or URL with its default handler."""

    def edit(self, path: Path):
        """Open a text file in an editor."""
        self.open(str(path))

    @abstractmethod
    def run(self, cmd: str, cwd: str | None = None):
        """Start a shell command in the background, with no console window."""

    def activate(self, app: str) -> bool:
        """Bring a window of `app` (a process name, e.g. outlook.exe) to the front: the one used
        last, or while the app is in front already, its next one. False when the app has no
        window, or the OS can't do it; launch.focus then opens the app instead."""
        return False

    def message_box(self, text: str):
        """Show a short message to someone who may not have a terminal."""

    @abstractmethod
    def ipc_connect(self, name: str) -> IpcConnection:
        """Connect to another app's local IPC endpoint, e.g. 'discord-ipc-0'. Raises OSError."""

    def audio(self) -> AudioBackend:
        raise Unsupported(f"audio control is not supported on {self.name} yet")

    def keyboard(self) -> Keyboard:
        raise Unsupported(f"keystrokes are not supported on {self.name} yet")

    def focus(self) -> FocusReader:
        raise Unsupported(f"switching pages by app is not supported on {self.name} yet")

    def media(self) -> MediaReader:
        raise Unsupported(f"reading what's playing is not supported on {self.name} yet")

    def hwinfo(self) -> dict[str, float]:
        """The readings HWiNFO reports to its Gadget, by label; empty while it reports none."""
        raise Unsupported(f"HWiNFO is not supported on {self.name}")

    def run_with_event_loop(self, run: Callable[[], None], stop: Callable[[], None]):
        """Run `run` until it returns, with the OS's event loop going where it needs one
        (macOS: on the main thread, so the frontmost app stays current); `stop` ends it early."""
        run()

    def run_shortcut(self, name: str, input: str | None = None, timeout: float = 60) -> str:
        """Run one of the user's automations (macOS Shortcuts) by name; returns its text output."""
        raise Unsupported(f"Shortcuts are not available on {self.name}")

    def shortcut_names(self) -> list[str]:
        """Names run_shortcut accepts, for the editor's picker."""
        return []

    # -- look ---------------------------------------------------------------
    def accent_color(self) -> tuple[int, int, int] | None:
        """The user's system accent colour (sRGB), or None to use hubdeck green."""
        return None

    def label_fonts(self) -> list[tuple[str, str | None]]:
        """Bold fonts for key labels, best first: (font file, variation name or None)."""
        return [("DejaVuSans-Bold.ttf", None)]

    # -- tray ---------------------------------------------------------------
    def prepare_tray(self):
        """Called once before the tray icon starts."""

    def notify(self, title: str, message: str) -> bool:
        """Show a notification the system's own way; False to let the tray library do it."""
        return False

    def start_at_login(self) -> bool | None:
        """Whether hubdeck starts at login, or None when the tray can't offer the switch."""
        return None

    def set_start_at_login(self, on: bool):
        raise Unsupported(f"starting at login is not supported on {self.name} yet")

    def style_tray_icon(self, icon, glyph):
        """Called on the UI thread once the pystray icon shows. `glyph` is the icon as a black
        shape on transparent (PIL), for systems that draw menu-bar icons as tinted templates."""

    def call_on_ui_thread(self, fn):
        """Run fn where the tray's UI toolkit wants it (AppKit only allows its main thread)."""
        fn()
