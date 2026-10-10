from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths


def setup_logging(console: bool = True, verbose: bool = False):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        paths.app_dir().mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(paths.log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        print(f"warning: cannot write log file: {e}", file=sys.stderr)
    if console and sys.stderr:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        root.addHandler(ch)
    for noisy in ("PIL", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
