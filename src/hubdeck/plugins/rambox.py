"""Rambox 3 through its MCP server: open one of its apps, or call any of its tools.

The server listens on 127.0.0.1 and takes one JSON-RPC request per POST to /mcp,
with the token Rambox shows next to its port.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..platforms import current
from .base import ThreadedPlugin

SETTINGS = ("token", "port")
DEFAULT_PORT = 7822
APP = "Rambox.exe"   # the window to bring up (Windows; elsewhere activate() is False and Rambox shows itself)
OPEN_HELP = "rambox.open takes one of Rambox's apps, by its name in Rambox or its id, e.g. {rambox.open: Rocket Chat}"
CALL_HELP = "rambox.call takes {tool: <MCP tool>, args: {...}}, e.g. {rambox.call: {tool: set_theme, args: {theme: dark}}}"


class RamboxError(Exception):
    pass


def pick_app(apps: list[dict], name: str) -> list[dict]:
    """The apps `name` means: the one with that id, else those with that title (any case),
    the enabled ones if some are."""
    matches = [a for a in apps if a["id"] == name] or [a for a in apps if a["title"].casefold() == name.casefold()]
    return [a for a in matches if a.get("enabled")] or matches


class RamboxPlugin(ThreadedPlugin):
    name = "rambox"
    actions = {
        "open": "bring Rambox up on one of its apps, by name or id: {rambox.open: Rocket Chat}",
        "call": "call any tool of Rambox's MCP server: {rambox.call: {tool: set_theme, args: {theme: dark}}}",
    }

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self._apps: list[dict] | None = None   # list_apps, fetched on the first open

    @classmethod
    def check_config(cls, config):
        problems = []
        token = config.get("token")
        if not (isinstance(token, str) and token.strip()):
            problems.append("plugins.rambox.token: the token of Rambox's MCP server, e.g. token: !secret rambox_token")
        port = config.get("port", DEFAULT_PORT)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            problems.append(f"plugins.rambox.port: the MCP server's port, e.g. {DEFAULT_PORT}")
        problems += [f"plugins.rambox: unknown setting {k!r} (available: {', '.join(SETTINGS)})"
                     for k in sorted(set(config) - set(SETTINGS))]
        return problems

    @classmethod
    def check_action(cls, action, arg):
        if action == "open" and not (isinstance(arg, str) and arg.strip()):
            return OPEN_HELP
        if action == "call" and not (isinstance(arg, dict) and isinstance(arg.get("tool"), str)
                                     and isinstance(arg.get("args", {}), dict) and not set(arg) - {"tool", "args"}):
            return CALL_HELP
        return None

    def call(self, tool: str, args: dict):
        """A tool's reply: its structured content, else its text. RamboxError if it fails."""
        port = self.config.get("port", DEFAULT_PORT)
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", json.dumps(body).encode(), {
            "Authorization": f"Bearer {self.config['token']}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})   # the server refuses either alone
        try:
            # shortcut: reads the reply as JSON, as Rambox sends it; an MCP server may stream it as SSE instead
            with urllib.request.urlopen(req, timeout=5) as r:
                reply = json.load(r)
        except urllib.error.HTTPError as e:
            hint = ": check plugins.rambox.token" if e.code == 401 else ""
            raise RamboxError(f"Rambox refused {tool} (HTTP {e.code}){hint}") from None
        except OSError as e:
            raise RamboxError(f"can't reach Rambox's MCP server on port {port} ({e}); is Rambox running?") from None
        if "error" in reply:
            raise RamboxError(f"Rambox {tool}: {reply['error'].get('message')}")
        result = reply["result"]
        text = " ".join(c.get("text", "") for c in result.get("content", []))
        if result.get("isError"):
            raise RamboxError(f"Rambox {tool}: {text}")
        return result.get("structuredContent", text)

    def _app(self, name: str) -> dict:
        for fresh in (False, True):   # the cached list first; a name it lacks may be a new app
            if fresh or self._apps is None:
                self._apps = self.call("list_apps", {})["data"]
            matches = pick_app(self._apps, name)
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise RamboxError(f"Rambox has {len(matches)} apps named {name!r}; use one's id: "
                                  + ", ".join(a["id"] for a in matches))
        raise RamboxError(f"Rambox has no app {name!r} (it has: {', '.join(sorted({a['title'] for a in self._apps}))})")

    def handle(self, action, arg):
        try:
            if action == "call":
                self.log.info("%s: %s", arg["tool"], self.call(arg["tool"], arg.get("args", {})))
            elif action == "open":
                app = self._app(arg)
                self.call("focus_app", {"appId": app["id"]})
                if not current().activate(APP):          # no window: closed to the tray
                    self.call("window_state", {"action": "show"})
                    current().activate(APP)
                self.log.info("opened %s", app["title"])
        except RamboxError as e:
            self.log.warning("%s", e)
