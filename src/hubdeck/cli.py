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

    rotate = args.rotate
    if rotate is None:
        try:
            rotate = _source(args).load().device.rotate
        except Exception:
            rotate = 0
    try:
        dev = M4315()
        dev.set_software_mode(True)
        for i in range(dev.key_count):
            dev.set_image(i, dev.encode(number_face(i + 1, dev.image_size, rotate)))
    except DeviceError as e:
        sys.exit(f"error: {e}")
    print("Every key should now show its number (yellow ^ marks the top).")
    print("Press keys to see their numbers here. Ctrl+C to quit.")
    try:
        while True:
            for idx, pressed in dev.poll():
                print(f"key {idx + 1} {'down' if pressed else 'up'}")
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
        extra = [s for s in ("dial", "knobs", "rollers", "faders") if getattr(page, s)]
        print(f"  page {name}{mark}: keys {sorted(page.keys)}" + (f", {', '.join(extra)}" if extra else ""))
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
        engine.run()
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
    from .cli_probe import add_parser as add_probe_parser
    add_probe_parser(sub)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
