import json
import textwrap
import urllib.error
import urllib.request

import pytest

from hubdeck.config import ConfigSource, load_config
from hubdeck.gui.doc import ConfigDoc, from_plain, to_plain
from hubdeck.gui.server import GuiServer

CONFIG = """\
# my hubdeck config
plugins:
  rec: {token: !secret rec_token}   # secret stays a tag

pages:
  main:
    keys:
      1: {action: rec.a, face: {label: One}}   # first key
      5: {action: rec.b, face: {label: Five}}
    knobs:
      1: {turn: rec.level}
  other:
    keys:
      1: {action: page.back, face: {label: Back}}
"""


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBDECK_REC_TOKEN", "x")
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG, encoding="utf-8")
    return p


def test_plain_roundtrip_keeps_secret_tag():
    node = from_plain({"a": {"$secret": "tok"}, "b": [1, 2]})
    assert to_plain(node) == {"a": {"$secret": "tok"}, "b": [1, 2]}


def test_set_item_keeps_comments_order_and_secrets(cfg_path):
    doc = ConfigDoc(cfg_path)
    doc.set_item("main", "keys", 3, {"action": "rec.a", "face": {"label": "Three"}})
    doc.set_item("main", "keys", 5, None)
    assert doc.save() is None
    text = cfg_path.read_text(encoding="utf-8")
    assert "# my hubdeck config" in text and "# first key" in text and "!secret rec_token" in text
    assert text.index("1: {action") < text.index("3:") and "Five" not in text
    cfg = load_config(cfg_path)
    assert sorted(cfg.pages["main"].keys) == [1, 3]


def test_hand_aligned_lines_keep_their_spacing(tmp_path):
    p = tmp_path / "config.yaml"
    original = textwrap.dedent("""\
        keys:
          1:
            action: rec.a
            state: rec.on
            face:    {icon: microphone, label: MIC}      # aligned by hand
            face_on: {icon: microphone-off, label: OFF}
        """)
    p.write_bytes(original.encode())     # LF on purpose (write_text would give CRLF on Windows)
    doc = ConfigDoc(p)
    doc.set_item("main", "keys", 2, {"action": "rec.b", "face": {"label": "#2", "bg": "#c81e28"}})
    assert doc.save() is None
    text = p.read_text(encoding="utf-8")
    assert text.startswith(original)                       # untouched lines byte-identical
    assert 'bg: "#c81e28"' in text and 'label: "#2"' in text
    doc.set_item("main", "keys", 2, None)
    assert doc.save() is None and p.read_bytes() == original.encode()      # LF kept (no CRLF on Windows)


def test_editing_settings_keeps_comments_of_untouched_entries(tmp_path):
    p = tmp_path / "config.yaml"
    original = ("modules:\n"
                "  keys:    {rotate: 0}     # the big one\n"
                "  faders:  {rotate: 270}   # sideways\n"
                "\n"
                "keys:\n"
                "  1:\n"
                "    action: rec.a     # keep this comment\n"
                "    face: {label: One}\n")
    p.write_bytes(original.encode())
    doc = ConfigDoc(p)
    doc.set_global("modules", {"keys": {"rotate": 90}, "faders": {"rotate": 270}})
    doc.set_item("main", "keys", 1, {"action": "rec.a", "face": {"label": "Uno"}})
    assert doc.save() is None
    text = p.read_text(encoding="utf-8")
    assert "# sideways" in text and "# the big one" in text and "# keep this comment" in text
    assert "rotate: 90" in text and "Uno" in text and "\n\nkeys:" in text.replace("\r\n", "\n")


def test_crlf_files_stay_crlf(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b"keys:\r\n  1: {action: rec.a}\r\n")
    doc = ConfigDoc(p)
    doc.set_item("main", "keys", 2, {"action": "rec.b"})
    assert doc.save() is None
    data = p.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n") and b"2:" in data


def test_live_mode_uses_engine_state(cfg_path, recorder):
    from hubdeck.device.fake import FakeKeyModule
    from hubdeck.engine import Engine
    dev = FakeKeyModule()
    eng = Engine(ConfigSource(cfg_path), factories={"keys": lambda: dev})
    eng.load_initial()
    eng.step()
    s = GuiServer(ConfigSource(cfg_path), engine=eng, port=0)
    try:
        st = s.api_state()
        assert st["live"] and st["current_page"] == "main" and "keys" in st["connected"]
        assert s.face_png("main", "keys", 1)[:4] == b"\x89PNG"
    finally:
        s.httpd.server_close()
        eng.shutdown()


def test_live_mode_shows_plugged_in_modules_the_config_doesnt_use(cfg_path, recorder, monkeypatch):
    """hubdeck only opens the modules the config uses: the editor must still show a new one
    from USB, or there's no way to set it up (seen on macOS with the Dial and Knobs)."""
    from hubdeck.device import manager
    from hubdeck.device.fake import FakeKeyModule
    from hubdeck.engine import Engine
    monkeypatch.setattr(manager, "present_kinds", lambda: {"keys", "dial"})
    eng = Engine(ConfigSource(cfg_path), factories={"keys": lambda: FakeKeyModule()})
    eng.load_initial()
    eng.step()
    s = GuiServer(ConfigSource(cfg_path), engine=eng, port=0)
    try:
        st = s.api_state()
        assert set(eng.manager.devices) == {"keys"} and "dial" not in st["used"]
        assert st["connected"] == ["dial", "keys"]
        shown = {kind: m["connected"] for kind, m in st["layout"]["modules"].items()}
        assert shown["dial"] is True                      # plugged in, not used yet: shown
        assert shown["knobs"] is False                    # used by the config, not plugged in
    finally:
        s.httpd.server_close()
        eng.shutdown()


def test_invalid_change_is_not_written(cfg_path):
    doc = ConfigDoc(cfg_path)
    doc.set_item("main", "keys", 2, {"action": "rec.nope"})
    err = doc.save()
    assert "no action 'nope'" in err
    assert cfg_path.read_text(encoding="utf-8") == CONFIG
    assert doc.get_item("main", "keys", 2) is None          # in-memory change dropped too


def test_single_page_shorthand_converts_when_adding_a_page(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("# top\nkeys:\n  1: {action: rec.a}   # keep me\n", encoding="utf-8")
    doc = ConfigDoc(p)
    assert doc.page_names() == ["main"]
    doc.add_page("media")
    doc.set_item("media", "keys", 1, {"action": "page.back"})
    assert doc.save() is None
    text = p.read_text(encoding="utf-8")
    assert "pages:" in text and "# keep me" in text
    assert set(load_config(p).pages) == {"main", "media"}


def test_rename_and_delete_page(cfg_path):
    doc = ConfigDoc(cfg_path)
    doc.rename_page("other", "media")
    assert doc.save() is None and doc.page_names() == ["main", "media"]
    doc.delete_page("media")
    assert doc.save() is None and doc.page_names() == ["main"]
    with pytest.raises(ValueError):
        doc.delete_page("main")


@pytest.fixture
def server(cfg_path):
    s = GuiServer(ConfigSource(cfg_path), port=0)
    s.connected = lambda: ["keys", "knobs"]       # no hardware enumeration in tests
    s.start(open_browser=False)
    yield s
    s.stop()


def call(server, path, body=None, token=True):
    req = urllib.request.Request(server.url.rstrip("/") + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None)
    if token:
        req.add_header("X-Hubdeck-Token", server.token)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read()
        return json.loads(data) if r.headers.get("Content-Type", "").startswith("application/json") else data


def test_api_requires_token(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server, "/api/state", token=False)
    assert e.value.code == 403
    page = call(server, "/", token=False)
    assert server.token.encode() in page                    # the page itself carries the token


def test_api_state_and_layout(server):
    st = call(server, "/api/state")
    assert set(st["pages"]) == {"main", "other"} and st["error"] is None
    assert st["pages"]["main"]["keys"]["1"].startswith("One")
    mods = st["layout"]["modules"]
    assert {"keys", "knobs"} <= set(mods) and mods["keys"]["w"] == 4 and mods["keys"]["x"] is not None
    assert [c["number"] for c in mods["knobs"]["controls"]] == [2, 3, 1]      # drawn by module index


def test_api_save_item_and_reject_invalid(server, cfg_path):
    r = call(server, "/api/item", {"page": "main", "section": "keys", "num": 2,
                                   "raw": {"action": {"page": "other"}, "face": {"icon": "music"}}})
    assert r["ok"]
    assert load_config(cfg_path).pages["main"].keys[2].on_press[0].arg == "other"
    bad = call(server, "/api/item", {"page": "main", "section": "keys", "num": 2, "yaml": "action: rec.missing\n"})
    assert not bad["ok"] and "missing" in bad["error"]


def test_api_rotation_changes_layout(server):
    assert call(server, "/api/global", {"name": "modules", "raw": {"knobs": {"rotate": 90}}})["ok"]
    assert call(server, "/api/global", {"name": "device", "raw": {"orientation": "vertical"}})["ok"]
    st = call(server, "/api/state")
    assert st["layout"]["cols"] == 4 and st["layout"]["rows"] == 6
    assert st["layout"]["modules"]["knobs"]["rotate"] == 90


def test_api_face_png(server):
    png = call(server, "/api/face.png?page=main&section=keys&num=1")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    prev = call(server, "/api/preview.png", {"page": "main", "section": "keys", "num": 9,
                                             "raw": {"face": {"text": "Hi", "bg": "red"}}})
    assert prev[:4] == b"\x89PNG"


@pytest.mark.parametrize("os_name, offered", [("darwin", True), ("win32", False), ("linux", False)])
def test_editor_offers_shortcuts_only_on_macos(server, monkeypatch, os_name, offered):
    monkeypatch.setattr("hubdeck.plugins.base.sys.platform", os_name)
    plugins = server.api_meta()["plugins"]
    assert ("shortcuts" in plugins) is offered
    assert "vars" in plugins and "audio" in plugins          # everything else stays
