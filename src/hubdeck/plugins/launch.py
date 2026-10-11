"""Open files/URLs/apps and run commands."""

from __future__ import annotations

import os

from ..platforms import current
from .base import ThreadedPlugin


class LaunchPlugin(ThreadedPlugin):
    name = "launch"
    actions = {
        "open": "open a file, folder, app or URL with its default handler, e.g. {open: 'https://...'} "
                "(macOS: also an app name, e.g. {open: Spotify})",
        "run": "run a shell command, e.g. {run: 'notepad.exe'} or {run: {cmd: '...', cwd: 'C:/...'}}",
    }

    @classmethod
    def check_action(cls, action, arg):
        if action == "open" and not isinstance(arg, str):
            return "launch.open needs a path or URL"
        if action == "run":
            if isinstance(arg, dict):
                if not isinstance(arg.get("cmd"), str) or set(arg) - {"cmd", "cwd"}:
                    return "launch.run takes {cmd: '...', cwd: '...'}"
            elif not isinstance(arg, str):
                return "launch.run needs a command"
        return None

    def handle(self, action, arg):
        if action == "open":
            current().open(os.path.expandvars(arg))
        elif action == "run":
            cmd, cwd = (arg["cmd"], arg.get("cwd")) if isinstance(arg, dict) else (arg, None)
            current().run(cmd, cwd=os.path.expandvars(cwd) if cwd else None)
            self.log.info("ran %s", cmd)
