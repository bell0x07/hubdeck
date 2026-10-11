"""What hubdeck needs from the operating system.

Each OS implements `Platform` in its own package (windows/, macos/); POSIX
pieces shared by macOS and Linux are in posix.py. Anything an OS can't do
raises `Unsupported`, which callers log as a normal failure.

Heavy or OS-only imports (pycaw, pyobjc, ...) happen inside methods, never at
import time: configs are validated on every OS.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


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
    def output_name(self) -> str:
        """Name of the default playback device."""

    @abstractmethod
    def outputs(self) -> list[str]:
        """Names of the playback devices that can be made the default."""

    @abstractmethod
    def set_output(self, want: str) -> str | None:
        """Make the first output whose name contains `want` (any case) the default; its name, or None."""

    def apps(self) -> list[str]:
        """Apps whose volume can be read right now (for the editor's picker)."""
        return []

    def get_app_volume(self, app: str) -> float | None:
        """None when the app has no volume right now (not running or not playing)."""
        return None

    def set_app_volume(self, app: str, level: float):
        pass

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

    def message_box(self, text: str):
        """Show a short message to someone who may not have a terminal."""

    @abstractmethod
    def ipc_connect(self, name: str) -> IpcConnection:
        """Connect to another app's local IPC endpoint, e.g. 'discord-ipc-0'. Raises OSError."""

    def audio(self) -> AudioBackend:
        raise Unsupported(f"audio control is not supported on {self.name} yet")

    def keyboard(self) -> Keyboard:
        raise Unsupported(f"keystrokes are not supported on {self.name} yet")

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

    def style_tray_icon(self, icon, glyph):
        """Called on the UI thread once the pystray icon shows. `glyph` is the icon as a black
        shape on transparent (PIL), for systems that draw menu-bar icons as tinted templates."""

    def call_on_ui_thread(self, fn):
        """Run fn where the tray's UI toolkit wants it (AppKit only allows its main thread)."""
        fn()
