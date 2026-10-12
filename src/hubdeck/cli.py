"""hubdeck command line."""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from . import __version__, paths

EXAMPLE = paths.EXAMPLE_CONFIG


def _source(args):
    from .config import ConfigSource
    return ConfigSource(Path(args.config) if args.config else None)


def cmd_list(args):
    from .device import list_interfaces
    print("\n".join(list_interfaces()))


def cmd_test(args):
    from .device import M4315, DeviceError
    from .render.faces import number_face

    from .device import layout

    rotate = args.rotate
    if rotate is None:
        try:
            rotate = _source(args).load().modules.keys.rotate
        except Exception:
            rotate = 0
    try:
        dev = M4315()
        dev.set_software_mode(True)
        for i in range(dev.key_count):
            number = layout.number_of("keys", i, rotate)
            dev.set_image(i, dev.encode(number_face(number, dev.image_size, layout.image_rotation(rotate))))
    except DeviceError as e:
        sys.exit(f"error: {e}")
    print(f"Every key should now show its number (yellow ^ marks the top), mounted at rotate: {rotate}.")
    print("Press keys to see their numbers here. Ctrl+C to quit.")
    try:
        while True:
            for idx, pressed in dev.poll():
                print(f"key {layout.number_of('keys', idx, rotate)} {'down' if pressed else 'up'}")
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        dev.close()


def cmd_validate(args):
    from .config import ConfigError
    from .plugins import REGISTRY

    src = _source(args)
    try:
        cfg = src.load()
    except ConfigError as e:
        sys.exit(str(e))
    print(f"{src.path}: OK")
    print(f"  ownership: {cfg.device.ownership}, keys painted: {sorted(cfg.owned_keys())}")
    print(f"  modules used: {', '.join(sorted(cfg.used_modules())) or 'none'}")
    for name, page in cfg.pages.items():
        mark = " (start)" if name == cfg.start_page else ""
        mark += " (sticky)" if page.sticky else ""
        extra = [s for s in ("dial", "knobs", "rollers", "faders") if getattr(page, s)]
        windows = [" + ".join(filter(None, (m.app, m.title and f"title /{m.title}/"))) for m in page.match]
        print(f"  page {name}{mark}: keys {sorted(page.keys)}" + (f", {', '.join(extra)}" if extra else "")
              + (f"; for {', '.join(windows)}" if windows else ""))
    plugins = sorted(cfg.used_plugins() - {"page"})
    print(f"  plugins: {', '.join(plugins) or 'none'}")
    if args.verbose:
        for name in plugins:
            p = REGISTRY[name]
            print()
            if p.actions:
                print(f"  {name} actions: " + ", ".join(p.actions))
            if p.states:
                print(f"  {name} states:  " + ", ".join(p.states))


def cmd_run(args):
    from . import instance
    from .config import ConfigError
    from .engine import Engine
    from .logs import setup_logging
    from .platforms import current

    if not instance.acquire():
        sys.exit("hubdeck is already running (tray icon or another terminal)")
    setup_logging(console=True, verbose=args.verbose)
    engine = Engine(_source(args))
    if not engine.source.path.exists():
        sys.exit(f"{engine.source.path} not found (create one with: hubdeck init)")
    try:
        engine.load_initial()
    except ConfigError as e:
        print(f"{e}\n\nWaiting for a valid config - fix it and save.")
    print(f"hubdeck running with {engine.source.path} - edit it and save to apply. Ctrl+C to quit.")
    try:
        current().run_with_event_loop(engine.run, engine.stop)
    except KeyboardInterrupt:
        pass


def cmd_init(args):
    dest = Path(args.config) if args.config else paths.config_path()
    if dest.exists() and not args.force:
        sys.exit(f"{dest} already exists (use --force to overwrite)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE, dest)
    print(f"wrote {dest}")
    secrets = paths.secrets_path(dest)
    if not secrets.exists():  # never overwrite real secrets, even with --force
        shutil.copyfile(paths.EXAMPLE_SECRETS, secrets)
        print(f"wrote {secrets}")


def cmd_icons(args):
    from .render import tabler

    hits = tabler.search(args.term, args.filled)
    print("\n".join(hits) if hits else f"no {'filled' if args.filled else 'outline'} icons match {args.term!r}")
    print(f"({len(hits)} matches, Tabler {tabler.version()}; browse at https://tabler.io/icons)")


def cmd_gui(args):
    from .gui.server import GuiServer
    from .logs import setup_logging

    setup_logging(console=True)
    src = _source(args)
    if not src.path.exists():
        sys.exit(f"{src.path} not found (create one with: hubdeck init)")
    server = GuiServer(src, port=args.port)
    print(f"hubdeck editor: {server.url}  (Ctrl+C to stop). For live state, open it from the tray instead.")
    try:
        server.serve_forever(open_browser=not args.no_browser)
    except KeyboardInterrupt:
        pass


SEND_HELP = """examples:
  hubdeck send discord.toggle_mute          run any action, written as in the config
  hubdeck send page media                   ...with an argument (read like YAML)
  hubdeck send audio.set_volume 30
  hubdeck send shortcuts.run "Lights off"
  hubdeck send set focus Work               set vars.focus, shown with {vars.focus}
  hubdeck send set weather -                ...to the text on stdin, as is (Shortcuts: pass input to stdin)
  hubdeck send get discord.muted            print a state (empty while unknown)
  hubdeck send image picture photo.jpg      set vars.picture to a picture, for image: "{vars.picture}"
  hubdeck send image picture -              ...read from stdin (Shortcuts: pass input to stdin)

From the Shortcuts app, use a "Run Shell Script" action with the full path to hubdeck."""


def _plain(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def cmd_send(args):
    from .config.loader import parse_value
    from .control import ControlError, save_sent_image, send

    words = args.words
    try:
        if words[0] == "set":
            if len(words) < 3:
                sys.exit("usage: hubdeck send set <name> <value, or - for the text on stdin>")
            if words[2:] == ["-"]:      # literal text, e.g. a Shortcut's multi-line result
                # UTF-8 whatever the locale: on Windows sys.stdin would decode a pipe as cp1250 etc.
                value = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace").strip()
            else:
                value = parse_value(" ".join(words[2:]))
            send({"cmd": "set", "name": words[1], "value": value})
        elif words[0] == "image":
            if len(words) != 3:
                sys.exit("usage: hubdeck send image <name> <file or - for stdin>")
            try:
                data = sys.stdin.buffer.read() if words[2] == "-" else Path(words[2]).expanduser().read_bytes()
            except OSError as e:
                sys.exit(f"error: {e}")
            send({"cmd": "set", "name": words[1], "value": str(save_sent_image(words[1], data))})
        elif words[0] == "get":
            if len(words) != 2:
                sys.exit("usage: hubdeck send get <plugin.state>")
            print(_plain(send({"cmd": "get", "name": words[1]})["value"]))
        else:
            raw = words[0] if len(words) == 1 else {words[0]: parse_value(" ".join(words[1:]))}
            send({"cmd": "action", "action": raw})
    except ControlError as e:
        sys.exit(f"error: {e}")


def cmd_where(args):
    print(f"config:  {Path(args.config) if args.config else paths.config_path()}")
    print(f"secrets: {paths.secrets_path(Path(args.config) if args.config else None)}")
    print(f"log:     {paths.log_path()}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hubdeck", description="MasterHub 15-key module controller")
    ap.add_argument("--version", action="version", version=f"hubdeck {__version__}")
    ap.add_argument("--config", help=f"config file (default: {paths.config_path()})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list MasterHub HID interfaces").set_defaults(fn=cmd_list)
    p = sub.add_parser("test", help="show key numbers and print presses")
    p.add_argument("--rotate", type=int, choices=(0, 90, 180, 270))
    p.set_defaults(fn=cmd_test)
    p = sub.add_parser("run", help="run in the foreground")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("validate", help="check the config")
    p.add_argument("-v", "--verbose", action="store_true", help="also list plugin actions and states")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("init", help="write an example config")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("icons", help="search Tabler icon names")
    p.add_argument("term")
    p.add_argument("--filled", action="store_true")
    p.set_defaults(fn=cmd_icons)
    sub.add_parser("where", help="show config, secrets and log paths").set_defaults(fn=cmd_where)
    p = sub.add_parser("send", help="control the running hubdeck (from Shortcuts or scripts)",
                       usage="hubdeck send ACTION [ARG] | set NAME VALUE | get STATE | image NAME FILE",
                       epilog=SEND_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("words", nargs="+", metavar="WORD", help="see the examples below")
    p.set_defaults(fn=cmd_send)
    p = sub.add_parser("gui", help="open the config editor in your browser")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_gui)
    from .cli_probe import add_parser as add_probe_parser
    add_probe_parser(sub)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
