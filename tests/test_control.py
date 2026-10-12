"""The control channel (hubdeck send), and the vars and shortcuts plugins."""

import json
import os
import socket
import time

import pytest

from hubdeck.config import ConfigError, ConfigSource, load_config
from hubdeck.config.loader import parse_value
from hubdeck.config.schema import parse_action
from hubdeck.control import ControlError, ControlServer, control_file, send
from hubdeck.device.fake import FakeKeyModule
from hubdeck.engine import Engine

CONFIG = """
plugins:
  vars: {mode: idle}
  shortcuts:
    outputs:
      weather: {run: "Get weather", every: 60}
keys:
  1: {action: rec.a, face: {text: "{vars.mode}"}}
  2: {action: page.home, state: vars.recording, face: {label: REC}}
  3: {face: {text: "{shortcuts.weather}"}}
"""


@pytest.fixture
def shortcuts_run(monkeypatch):
    """Never run the user's real Shortcuts: record the calls instead."""
    from hubdeck.platforms import current
    calls = []

    def fake_run(name, input=None, timeout=60):
        calls.append((name, input))
        return "21°C" if name == "Get weather" else ""
    monkeypatch.setattr(current(), "run_shortcut", fake_run)
    return calls


@pytest.fixture
def engine(write, monkeypatch, tmp_path, shortcuts_run):
    monkeypatch.setenv("HUBDECK_HOME", str(tmp_path / "home"))
    eng = Engine(ConfigSource(write("config.yaml", CONFIG)), factories={"keys": FakeKeyModule})
    eng.load_initial()
    eng.step()
    server = ControlServer(eng)
    server.start()
    yield eng
    server.stop()
    eng.shutdown()


def test_send_runs_an_action_on_the_engine_thread(engine, recorder):
    assert send({"cmd": "action", "action": "rec.a"}) == {"ok": True}
    assert recorder.instances[-1].calls == []          # only queued so far
    engine.step()
    assert recorder.instances[-1].calls == [("a", None)]


def test_send_reports_what_is_wrong(engine):
    with pytest.raises(ControlError, match="rec has no action 'zzz'"):
        send({"cmd": "action", "action": "rec.zzz"})
    with pytest.raises(ControlError, match="no page named 'nope'"):
        send({"cmd": "action", "action": {"page": "nope"}})
    with pytest.raises(ControlError, match="discord isn't running"):
        send({"cmd": "action", "action": "discord.toggle_mute"})
    with pytest.raises(ControlError, match="not a valid name"):
        send({"cmd": "set", "name": "Focus Mode", "value": 1})


def test_set_and_get_vars(engine):
    assert send({"cmd": "get", "name": "vars.mode"})["value"] == "idle"     # starting value
    send({"cmd": "set", "name": "mode", "value": "work"})
    send({"cmd": "set", "name": "recording", "value": True})
    engine.step()
    engine.step()                                        # vars publish, engine drains states
    assert engine.state["vars.mode"] == "work" and engine.state["vars.recording"] is True
    assert send({"cmd": "get", "name": "vars.mode"}) == {"ok": True, "value": "work", "known": True}
    assert send({"cmd": "get", "name": "vars.nothing"})["known"] is False


def test_wrong_token_is_refused(engine):
    info = json.loads(control_file().read_text())
    if os.name == "posix":
        assert control_file().stat().st_mode & 0o077 == 0     # only this user can read the token
    with socket.create_connection(("127.0.0.1", info["port"]), timeout=5) as s:
        s.sendall(json.dumps({"token": "guess", "cmd": "action", "action": "rec.a"}).encode() + b"\n")
        reply = json.loads(s.makefile("rb").readline())
    assert reply["ok"] is False and "token" in reply["error"]


def test_stop_removes_control_file(write, monkeypatch, tmp_path):
    monkeypatch.setenv("HUBDECK_HOME", str(tmp_path / "home"))
    server = ControlServer(engine=None)
    server.start()
    assert control_file().exists()
    server.stop()
    assert not control_file().exists()
    with pytest.raises(ControlError, match="not running"):
        send({"cmd": "get", "name": "vars.mode"})


def test_parse_value_reads_like_the_config():
    assert parse_value("30") == 30 and parse_value("true") is True
    assert parse_value("on") == "on"                     # YAML 1.2: not a boolean
    assert parse_value("{name: Set scene, input: movie}") == {"name": "Set scene", "input": "movie"}
    assert parse_value("Lights off") == "Lights off"


def test_dynamic_states_are_checked(write):
    with pytest.raises(ConfigError, match="shortcuts has no output 'news'"):
        load_config(write("c.yaml", "keys: {1: {face: {text: '{shortcuts.news}'}}}"))
    with pytest.raises(ConfigError, match="every must be at least 10"):
        load_config(write("c.yaml", "plugins: {shortcuts: {outputs: {w: {run: W, every: 1}}}}\n"
                                    "keys: {1: {face: {text: '{shortcuts.w}'}}}"))
    load_config(write("c.yaml", "keys: {1: {face: {text: '{vars.anything}'}, state: vars.other}}"))


def _until(check, timeout=5):
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_shortcut_outputs_and_runs(engine, shortcuts_run):
    def drained():
        engine.step()
        return engine.state.get("shortcuts.weather") == "21°C"
    _until(drained)                                      # the output ran on the plugin's thread
    send({"cmd": "action", "action": {"shortcuts.run": {"name": "Set scene", "input": "movie"}}})
    engine.step()
    _until(lambda: ("Set scene", "movie") in shortcuts_run)
    assert shortcuts_run == [("Get weather", None), ("Set scene", "movie")]


def test_failing_shortcut_output_is_logged_once(engine, shortcuts_run, monkeypatch, caplog):
    from hubdeck.platforms import current
    plugin = engine.plugins["shortcuts"]
    plugin.stop()                                        # drive it by hand from here

    def missing(name, input=None, timeout=60):
        raise OSError("Couldn't find shortcut")
    monkeypatch.setattr(current(), "run_shortcut", missing)
    plugin.setup()
    with caplog.at_level("INFO", logger="hubdeck.shortcuts"):
        for _ in range(3):
            plugin._refresh("weather")
        monkeypatch.setattr(current(), "run_shortcut", lambda name, input=None, timeout=60: "sunny")
        plugin._refresh("weather")
    messages = [r.getMessage() for r in caplog.records]
    assert sum("Couldn't find" in m for m in messages) == 1
    assert any("works again" in m for m in messages)


def test_line_format_picks_one_line_of_text():
    from hubdeck.engine import format_value
    weather = "21°C\nPartly cloudy\n"
    assert format_value(weather, "line1") == "21°C"
    assert format_value(weather, "line2") == "Partly cloudy"
    assert format_value(weather, "line3") == ""
    assert format_value(None, "line1") == "–"


def _png(size=(1200, 800), color=(200, 30, 40)) -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_sent_image_is_scaled_and_replaces_the_previous_one(monkeypatch, tmp_path):
    from PIL import Image
    from hubdeck.control import SENT_IMAGE_SIZE, save_sent_image, sent_images_dir
    monkeypatch.setenv("HUBDECK_HOME", str(tmp_path))
    first = save_sent_image("picture", _png())
    assert max(Image.open(first).size) == SENT_IMAGE_SIZE
    second = save_sent_image("picture", _png(color=(0, 0, 255)))
    other = save_sent_image("cover", _png())
    assert not first.exists() and second.exists() and other.exists()
    assert sorted(p.name.split("-")[0] for p in sent_images_dir().iterdir()) == ["cover", "picture"]
    with pytest.raises(ControlError, match="not a valid name"):
        save_sent_image("../evil", _png())
    with pytest.raises(ControlError, match="not a picture"):
        save_sent_image("picture", b"hello")


def test_key_shows_a_sent_picture(write, monkeypatch, tmp_path):
    from hubdeck.control import save_sent_image
    monkeypatch.setenv("HUBDECK_HOME", str(tmp_path / "home"))
    cfg = write("config.yaml", 'keys: {14: {face: {image: "{vars.picture}", label: Pic}}}')
    eng = Engine(ConfigSource(cfg), factories={"keys": FakeKeyModule})
    eng.load_initial()                                    # no file needed for a templated image
    eng.step()
    assert eng.resolve_face(14).image is None             # nothing sent yet: plain face
    path = save_sent_image("picture", _png())
    eng.run_action(parse_action({"vars.set": {"picture": str(path)}}))
    eng.step()
    eng.step()
    face = eng.resolve_face(14)
    assert face.image == str(path) and face.label == "Pic"
    eng.shutdown()


def test_send_set_reads_text_from_stdin(engine, monkeypatch):
    """A Shortcut passes its result to `hubdeck send set weather -` on stdin: kept as is."""
    import io
    from hubdeck import cli
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO("21°C\nPartly cloudy: windy\n".encode())))
    engine.plugins["vars"]                                # used by the config, so it runs
    cli.main(["send", "set", "weather", "-"])
    engine.step()
    engine.step()
    assert engine.state["vars.weather"] == "21°C\nPartly cloudy: windy"


def test_quit_stops_hubdeck_and_tells_the_tray(engine):
    events = []
    engine.listeners.append(lambda event, detail: events.append(event))
    assert send({"cmd": "quit"}) == {"ok": True}
    assert engine._stop.is_set() and events == ["quit"]
