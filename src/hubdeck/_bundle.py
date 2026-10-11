"""Entry point inside hubdeck.app (macOS), run by its launcher (packaging/macos/launcher.c).

No arguments: the menu-bar app. With arguments: the command line, e.g.
`hubdeck.app/Contents/MacOS/hubdeck send page media`, or through a symlink named hubdeck.
"""

from __future__ import annotations

import os
import sys

BUNDLE_ID = "io.github.bell0x07.hubdeck"


def main():
    # set by the launcher for this Python only: commands hubdeck starts (launch.run, ...) mustn't inherit them
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE"):
        os.environ.pop(key, None)
    args = sys.argv[1:]
    if args:
        from .cli import main as cli
        cli(args)
    else:
        from .tray import main as tray
        tray([])


if __name__ == "__main__":
    main()
