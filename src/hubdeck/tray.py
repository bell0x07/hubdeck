"""System-tray app: runs the engine in the background (entry point: hubdeck-tray)."""

from __future__ import annotations

import argparse
import logging
import shutil
import threading
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from . import PROJECT_URL, instance, paths
from .config import ConfigError, ConfigSource
from .engine import Engine
from .logs import setup_logging
from .platforms import current
from .render import tabler
from .render.faces import RenderFace, parse_color, render

log = logging.getLogger("hubdeck.tray")


TRAY_GLYPH = "layout-grid"


def _glyph_image(size: int = 64):
    """The tray icon as a plain black shape on transparent, for menu bars that tint icons (macOS)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((size / 2, size / 2), chr(tabler.codepoint(TRAY_GLYPH)), font=tabler.font(int(size * 1.15)),
                             fill="black", anchor="mm")
    return img


def _icon_image():
    face = RenderFace(bg=(25, 25, 30), fg=parse_color("accent"), label_color=(255, 255, 255), icon=TRAY_GLYPH)
    return render(face, size=64)


def _open(path: Path):
    if path.exists():
        current().open(str(path))


def _edit(path: Path, template: Path):
    """Open a text file for editing, creating it from `template` first."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, path)
        log.info("created %s", path)
    current().edit(path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hubdeck-tray")
    ap.add_argument("--config")
    ap.add_argument("--replace", action="store_true", help="take over from a hubdeck that's already running")
    args = ap.parse_args(argv)

    setup_logging(console=False)
    if not instance.acquire() and not (args.replace and instance.replace_running()):
        if not args.replace:            # after an upgrade, staying quiet beats a confusing message
            current().message_box("hubdeck is already running (check the system tray).")
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

    editor = {}

    def open_editor():
        """Start the browser editor once (in this process, so it shows live state) and open it."""
        if not source.path.exists():
            _edit(source.path, paths.EXAMPLE_CONFIG)    # creates the config from the example first
        if "server" not in editor:
            from .gui.server import GuiServer
            editor["server"] = GuiServer(source, engine=engine)
            editor["server"].start(open_browser=True)
        else:
            webbrowser.open(editor["server"].url)

    def notify(message: str, title: str):
        try:
            if current().notify(title, message):
                return
        except Exception:
            log.debug("system notification failed", exc_info=True)
        icon.notify(message, title)

    def toggle_login():
        try:
            current().set_start_at_login(not current().start_at_login())
        except OSError as e:
            log.error("%s", e)
            notify(str(e), "hubdeck")

    def quit_app():
        engine.stop()
        if "server" in editor:
            editor["server"].stop()
        icon.stop()

    menu = pystray.Menu(
        # default item: also runs when the tray icon is clicked
        pystray.MenuItem("Open editor", open_editor, default=True),
        pystray.MenuItem("Edit config as text", lambda: _edit(source.path, paths.EXAMPLE_CONFIG)),
        pystray.MenuItem("Edit secrets", lambda: _edit(source.secrets, paths.EXAMPLE_SECRETS)),
        pystray.MenuItem(lambda item: f"Page: {engine.current_page or '-'}", pystray.Menu(pages_menu)),
        pystray.MenuItem("Reload config", lambda: engine.reload()),
        pystray.MenuItem("Open config folder", lambda: _open(source.path.parent)),
        pystray.MenuItem("Open log", lambda: _open(paths.log_path())),
        pystray.MenuItem("Start at login", toggle_login, checked=lambda item: bool(current().start_at_login()),
                         visible=current().start_at_login() is not None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("GitHub project", lambda: webbrowser.open(PROJECT_URL)),
        pystray.MenuItem("Quit", quit_app),
    )
    current().prepare_tray()
    icon = pystray.Icon("hubdeck", _icon_image(), "hubdeck", menu)

    def on_event(event, detail):
        if event == "quit":                 # asked over the control channel, e.g. by an upgrade
            current().call_on_ui_thread(quit_app)
            return
        if event == "error":
            notify(detail[-250:], "hubdeck: config error")
        elif event == "reloaded":
            notify("Config reloaded", "hubdeck")
        if event in ("page", "reloaded"):
            def refresh():
                icon.title = f"hubdeck - {engine.current_page}"
                icon.update_menu()
            current().call_on_ui_thread(refresh)

    engine.listeners.append(on_event)

    def setup(icon):
        icon.visible = True
        current().call_on_ui_thread(lambda: current().style_tray_icon(icon, _glyph_image()))
        if startup_error:
            notify(startup_error[-250:], "hubdeck: config error")
        threading.Thread(target=engine.run, name="hubdeck-engine", daemon=True).start()

    icon.run(setup=setup)
    engine.stop()


if __name__ == "__main__":
    main()
