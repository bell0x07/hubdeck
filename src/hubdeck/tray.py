"""System-tray app: runs the engine in the background (entry point: hubdeck-tray)."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import pystray

from . import PROJECT_URL, instance, paths
from .config import ConfigError, ConfigSource
from .engine import Engine
from .logs import setup_logging
from .render.faces import RenderFace, render

log = logging.getLogger("hubdeck.tray")


def _icon_image():
    face = RenderFace(bg=(25, 25, 30), fg=(60, 200, 110), label_color=(255, 255, 255), icon="layout-grid")
    return render(face, size=64)


def _open(path: Path):
    if sys.platform == "win32" and path.exists():
        os.startfile(path)


def _edit(path: Path, template: Path):
    """Open a text file for editing, creating it from `template` first."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, path)
        log.info("created %s", path)
    if sys.platform != "win32":
        return
    for verb in ("edit", "open"):
        try:
            os.startfile(path, verb)
            return
        except OSError:
            continue
    subprocess.Popen(["notepad.exe", str(path)])  # no editor registered for .yaml


def _message_box(text: str):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "hubdeck", 0x40)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hubdeck-tray")
    ap.add_argument("--config")
    args = ap.parse_args(argv)

    setup_logging(console=False)
    if not instance.acquire():
        _message_box("hubdeck is already running (check the system tray).")
        return
    source = ConfigSource(Path(args.config) if args.config else None)
    engine = Engine(source)
    startup_error = None
    try:
        engine.load_initial()
    except ConfigError as e:
        startup_error = str(e)
        log.error("%s", e)

    def goto(page):
        return lambda: engine.goto_page(page)

    def is_current(page):
        return lambda item: engine.current_page == page

    def pages_menu():
        return [pystray.MenuItem(p, goto(p), checked=is_current(p), radio=True) for p in engine.page_names]

    def quit_app():
        engine.stop()
        icon.stop()

    menu = pystray.Menu(
        # default item: also runs when the tray icon is clicked
        pystray.MenuItem("Edit config", lambda: _edit(source.path, paths.EXAMPLE_CONFIG), default=True),
        pystray.MenuItem("Edit secrets", lambda: _edit(source.secrets, paths.EXAMPLE_SECRETS)),
        pystray.MenuItem(lambda item: f"Page: {engine.current_page or '-'}", pystray.Menu(pages_menu)),
        pystray.MenuItem("Reload config", lambda: engine.reload()),
        pystray.MenuItem("Open config folder", lambda: _open(source.path.parent)),
        pystray.MenuItem("Open log", lambda: _open(paths.log_path())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("GitHub project", lambda: webbrowser.open(PROJECT_URL)),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("hubdeck", _icon_image(), "hubdeck", menu)

    def on_event(event, detail):
        if event == "error":
            icon.notify(detail[-250:], "hubdeck: config error")
        elif event == "reloaded":
            icon.notify("Config reloaded", "hubdeck")
        if event in ("page", "reloaded"):
            icon.title = f"hubdeck - {engine.current_page}"
            icon.update_menu()

    engine.listeners.append(on_event)

    def setup(icon):
        icon.visible = True
        if startup_error:
            icon.notify(startup_error[-250:], "hubdeck: config error")
        threading.Thread(target=engine.run, name="hubdeck-engine", daemon=True).start()

    icon.run(setup=setup)
    engine.stop()


if __name__ == "__main__":
    main()
