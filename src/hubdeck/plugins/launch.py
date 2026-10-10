"""Open files/URLs/apps and run commands."""

from __future__ import annotations

import os
import subprocess
import sys

from .base import ThreadedPlugin

CREATE_NO_WINDOW = 0x08000000


class LaunchPlugin(ThreadedPlugin):
    name = "launch"
    actions = {
        "open": "open a file, folder, app or URL with its default handler, e.g. {open: 'https://...'}",
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
            target = os.path.expandvars(arg)
            if sys.platform == "win32":
                os.startfile(target)
            else:
                subprocess.Popen(["xdg-open", target])
        elif action == "run":
            cmd, cwd = (arg["cmd"], arg.get("cwd")) if isinstance(arg, dict) else (arg, None)
            flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
            subprocess.Popen(cmd, shell=True, cwd=os.path.expandvars(cwd) if cwd else None,
                             creationflags=flags, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.log.info("ran %s", cmd)
