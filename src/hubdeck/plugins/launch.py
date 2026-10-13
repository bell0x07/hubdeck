"""Open files/URLs/apps, switch to an app's window, and run commands."""

from __future__ import annotations

import os
import re

from ..platforms import current
from .base import ThreadedPlugin

URL_RE = re.compile(r"[a-z][a-z0-9+.-]+:", re.IGNORECASE)   # msteams:, https:; not a drive (C:)
FOCUS_HELP = "launch.focus takes an app's path or process name, e.g. {focus: outlook.exe}, or {app: ..., open: ...}"


def focus_target(arg) -> tuple[str, str]:
    """(app to look for, what to open without it) for launch.focus. A path or process name is
    both, the app being its file name; {app: ms-teams.exe, open: 'msteams:'} names them apart."""
    if isinstance(arg, dict):
        return arg["app"], arg.get("open", arg["app"])
    return re.split(r"[\\/]", arg.rstrip("\\/"))[-1], arg


class LaunchPlugin(ThreadedPlugin):
    name = "launch"
    actions = {
        "open": "open a file, folder, app or URL with its default handler, e.g. {open: 'https://...'} "
                "(macOS: also an app name, e.g. {open: Spotify})",
        "focus": "switch to an app's window, or open it if it has none: {focus: outlook.exe} or a path; "
                 "{focus: {app: ms-teams.exe, open: 'msteams:'}} when what to open doesn't name the app",
        "run": "run a shell command, e.g. {run: 'notepad.exe'} or {run: {cmd: '...', cwd: 'C:/...'}}",
    }

    @classmethod
    def check_action(cls, action, arg):
        if action == "open" and not isinstance(arg, str):
            return "launch.open needs a path or URL"
        if action == "focus":
            if isinstance(arg, dict):
                app, target = arg.get("app"), arg.get("open", "")
                if not isinstance(app, str) or not app.strip() or not isinstance(target, str) or set(arg) - {"app", "open"}:
                    return FOCUS_HELP
            elif not isinstance(arg, str) or not arg.strip():
                return FOCUS_HELP
            elif URL_RE.match(arg):
                return "launch.focus: a URL doesn't name the app to switch to; use {focus: {app: <process name>, open: <URL>}}"
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
        elif action == "focus":
            app, target = focus_target(arg)
            if current().activate(app):
                self.log.info("switched to %s", app)
            else:
                current().open(os.path.expandvars(target))
                self.log.info("%s has no window, opened %s", app, target)
        elif action == "run":
            cmd, cwd = (arg["cmd"], arg.get("cwd")) if isinstance(arg, dict) else (arg, None)
            current().run(cmd, cwd=os.path.expandvars(cwd) if cwd else None)
            self.log.info("ran %s", cmd)
