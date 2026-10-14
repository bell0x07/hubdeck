"""Rambox plugin against a fake Rambox MCP server: urlopen answers tools/call requests."""

import io
import json
import logging
import urllib.error
import urllib.request

import pytest

from hubdeck.plugins import PluginContext
from hubdeck.plugins import rambox
from hubdeck.plugins.rambox import RamboxPlugin

APPS = [
    {"id": "rc1", "title": "Rocket Chat", "enabled": True},
    {"id": "sl1", "title": "Slack", "enabled": False},
    {"id": "sl2", "title": "Slack", "enabled": True},
    {"id": "gc1", "title": "Google Chat", "enabled": True},
    {"id": "gc2", "title": "Google Chat", "enabled": True},
]


class FakeRambox:
    """urlopen stand-in for Rambox's MCP server, token t1. Errors answer as Rambox's do."""

    def __init__(self):
        self.apps = [dict(a) for a in APPS]
        self.calls: list[tuple[str, dict]] = []
        self.down = False

    def __call__(self, req, timeout=None):
        if self.down:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        assert req.full_url == "http://127.0.0.1:7822/mcp"
        assert "text/event-stream" in req.get_header("Accept")
        if req.get_header("Authorization") != "Bearer t1":
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        params = json.loads(req.data)["params"]
        self.calls.append((params["name"], params["arguments"]))
        if params["name"] == "list_apps":
            result = {"structuredContent": {"ok": True, "data": self.apps}}
        elif params["name"] in ("focus_app", "window_state", "set_theme"):
            result = {"content": [{"type": "text", "text": '{"ok": true}'}], "structuredContent": {"ok": True}}
        else:
            result = {"content": [{"type": "text", "text": f"MCP error -32602: Tool {params['name']} not found"}],
                      "isError": True}
        return io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode())


class FakePlatform:
    def __init__(self):
        self.windows = True        # whether Rambox has a window to bring up
        self.activated: list[str] = []

    def activate(self, app):
        self.activated.append(app)
        return self.windows


@pytest.fixture
def fake(monkeypatch):
    fake = FakeRambox()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


@pytest.fixture
def platform(monkeypatch):
    platform = FakePlatform()
    monkeypatch.setattr(rambox, "current", lambda: platform)
    return platform


def plugin(**config):
    return RamboxPlugin(PluginContext("rambox", lambda *_: None), {"token": "t1", **config})


def test_check_config():
    assert RamboxPlugin.check_config({"token": "t1"}) == []
    assert RamboxPlugin.check_config({"token": "t1", "port": 7900}) == []
    assert "token" in RamboxPlugin.check_config({})[0]
    assert "port" in RamboxPlugin.check_config({"token": "t1", "port": "x"})[0]
    assert "unknown setting 'url'" in RamboxPlugin.check_config({"token": "t1", "url": "x"})[0]


def test_check_action():
    assert RamboxPlugin.check_action("open", "Rocket Chat") is None
    assert RamboxPlugin.check_action("open", "") == rambox.OPEN_HELP
    assert RamboxPlugin.check_action("call", {"tool": "set_theme", "args": {"theme": "dark"}}) is None
    assert RamboxPlugin.check_action("call", {"tool": "list_apps"}) is None
    assert RamboxPlugin.check_action("call", "set_theme") == rambox.CALL_HELP
    assert RamboxPlugin.check_action("call", {"tool": "x", "arguments": {}}) == rambox.CALL_HELP


def test_open_by_name_or_id(fake, platform):
    p = plugin()
    p.handle("open", "rocket chat")                        # any case
    p.handle("open", "sl1")                                # an id, even of a disabled app
    assert fake.calls == [("list_apps", {}), ("focus_app", {"appId": "rc1"}), ("focus_app", {"appId": "sl1"})]
    assert platform.activated == ["Rambox.exe", "Rambox.exe"]


def test_open_prefers_enabled_and_refuses_ambiguous(fake, platform, caplog):
    p = plugin()
    p.handle("open", "Slack")
    assert fake.calls[-1] == ("focus_app", {"appId": "sl2"})
    calls = len(fake.calls)
    with caplog.at_level(logging.WARNING):
        p.handle("open", "Google Chat")
    assert "2 apps named 'Google Chat'; use one's id: gc1, gc2" in caplog.text
    assert len(fake.calls) == calls                        # nothing focused


def test_open_unknown_refetches_once(fake, platform, caplog):
    p = plugin()
    p.handle("open", "Rocket Chat")
    fake.apps.append({"id": "wa1", "title": "WhatsApp", "enabled": True})
    p.handle("open", "WhatsApp")                           # added since: found after a fresh list
    assert fake.calls[-2:] == [("list_apps", {}), ("focus_app", {"appId": "wa1"})]
    with caplog.at_level(logging.WARNING):
        p.handle("open", "Teams")
    assert "no app 'Teams' (it has: Google Chat, Rocket Chat, Slack, WhatsApp)" in caplog.text
    assert fake.calls[-1] == ("list_apps", {})


def test_open_without_a_window_shows_it(fake, platform):
    platform.windows = False                               # closed to the tray
    plugin().handle("open", "Rocket Chat")
    assert fake.calls[-2:] == [("focus_app", {"appId": "rc1"}), ("window_state", {"action": "show"})]
    assert platform.activated == ["Rambox.exe", "Rambox.exe"]


def test_call_any_tool(fake, platform, caplog):
    p = plugin()
    with caplog.at_level(logging.INFO):
        p.handle("call", {"tool": "set_theme", "args": {"theme": "dark"}})
        p.handle("call", {"tool": "nope"})
    assert fake.calls == [("set_theme", {"theme": "dark"}), ("nope", {})]
    assert "set_theme: {'ok': True}" in caplog.text
    assert "Rambox nope: MCP error -32602: Tool nope not found" in caplog.text


def test_errors_are_warnings(fake, platform, caplog):
    with caplog.at_level(logging.WARNING):
        plugin(token="wrong").handle("open", "Rocket Chat")
        fake.down = True
        plugin().handle("call", {"tool": "set_theme"})
    assert "Rambox refused list_apps (HTTP 401): check plugins.rambox.token" in caplog.text
    assert "can't reach Rambox's MCP server on port 7822" in caplog.text
    assert platform.activated == []
