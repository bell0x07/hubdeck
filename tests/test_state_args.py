"""States with an argument: plugin.state[argument] in state:, lighting and {templates}."""

import threading
import time

import pytest

from hubdeck import engine as engine_mod
from hubdeck.config import ConfigError, load_config
from hubdeck.control import ControlServer
from hubdeck.plugins import REGISTRY, Plugin
from hubdeck.plugins.base import state_key
from test_engine import make, rec


def test_states_with_an_argument_load(write):
    cfg = load_config(write("config.yaml", """
        lighting:
          base: {color: white, state: "rec.item[b]", on: {color: red}}
        keys:
          1: {state: "rec.item[light.desk]", face: {text: "{rec.item[sensor.t:x]:.1f}°"}, face_on: {bg: red}}
    """))
    assert cfg.state_refs() == {("rec", "item[light.desk]"), ("rec", "item[sensor.t:x]"), ("rec", "item[b]")}


@pytest.mark.parametrize("ref, error", [
    ("rec.item", "rec.item needs an item name in brackets: rec.item[...]"),
    ("rec.on[x]", "rec.on takes no [argument]"),
    ("rec.nope[x]", "rec has no state 'nope'"),
    ("vars.mode[x]", "vars.mode takes no [argument]"),
])
def test_arguments_are_checked(write, ref, error):
    with pytest.raises(ConfigError, match=error.replace("[", r"\[").replace("]", r"\]")):
        load_config(write("config.yaml", f"""
            keys:
              1: {{state: "{ref}", face_on: {{bg: red}}}}
        """))
    with pytest.raises(ConfigError, match=error.replace("[", r"\[").replace("]", r"\]")):
        load_config(write("config.yaml", f"""
            keys:
              1: {{face: {{label: "{{{ref}}}"}}}}
        """))


def test_malformed_state_is_rejected(write):
    with pytest.raises(ConfigError, match="must look like plugin.state or plugin.state"):
        load_config(write("config.yaml", """
            keys:
              1: {state: "rec.item[a", face_on: {bg: red}}
        """))


def test_plugins_watch_the_pairs_and_faces_follow_them(write, recorder):
    eng, _, _ = make(write, """
        device: {reassert_seconds: 0}
        keys:
          1: {state: "rec.item[light.desk]", face: {label: "{rec.item[sensor.t:x]:.1f}"}, face_on: {bg: red}}
          2: {state: rec.on, face: {label: plain}}
    """)
    plugin = rec(recorder)
    assert plugin.watched_states == {("item", "light.desk"), ("item", "sensor.t:x"), ("on", None)}
    face = eng.resolve_face(1)
    assert face.label == "–" and face.bg != (255, 0, 0)
    plugin.emit("item[light.desk]", True)
    plugin.emit("item[sensor.t:x]", 21.26)
    eng.step()
    face = eng.resolve_face(1)
    assert face.label == "21.3" and face.bg == (255, 0, 0)


def test_matching_a_state_with_an_argument(write, recorder):
    eng, _, _ = make(write, """
        device: {reassert_seconds: 0}
        keys:
          1: {state: {"rec.item[climate.ac]": cool}, face: {label: "{rec.item[climate.ac]}"}, face_on: {bg: red}}
    """)
    rec(recorder).emit("item[climate.ac]", "heat")
    eng.step()
    assert eng.resolve_face(1).bg != (255, 0, 0)
    rec(recorder).emit("item[climate.ac]", "cool")
    eng.step()
    assert eng.resolve_face(1).bg == (255, 0, 0) and eng.resolve_face(1).label == "cool"

# -- states fetched on demand: deck.state / hubdeck send get ---------------------


class FetchPlugin(Plugin):
    """Publishes only the states it's asked to watch, like ha: item[x] = "v:x"; never[x] stays unknown."""
    name = "fetch"
    states = {"item": "", "never": ""}
    state_args = {"item": "a name", "never": "a name"}
    fetches_states = True

    @classmethod
    def check_state(cls, name, config, arg=None):
        return super().check_state(name, config, arg) or ("bad name" if arg == "bad" else None)

    def watch_states(self, states):
        super().watch_states(states)
        for name, arg in states:
            if name == "item":
                self.ctx.set_state(state_key(name, arg), f"v:{arg}")


@pytest.fixture
def fetch():
    REGISTRY["fetch"] = FetchPlugin
    yield
    REGISTRY.pop("fetch", None)


FETCH_CONFIG = """
device: {reassert_seconds: 0}
plugins: {fetch: {}}
keys:
  1: {face: {label: "{fetch.item[shown]}"}}
"""


def read_in_thread(eng, read):
    """Run read() on another thread (as Python actions do) while the engine steps here."""
    out = {}
    t = threading.Thread(target=lambda: out.update(value=read(), at=time.monotonic()))
    start = time.monotonic()
    t.start()
    while t.is_alive():
        eng.step()
        time.sleep(0.005)
    return out["value"], out["at"] - start


def test_first_read_fetches_and_waits(write, fetch):
    eng, _, _ = make(write, FETCH_CONFIG)
    plugin = eng.plugins["fetch"]
    assert plugin.watched_states == {("item", "shown")}
    value, _ = read_in_thread(eng, lambda: eng.deck.state("fetch.item[other]"))
    assert value == "v:other"
    assert plugin.watched_states == {("item", "shown"), ("item", "other")}
    assert eng.deck.state("fetch.item[other]") == "v:other"            # from now on, a lookup


def test_send_get_fetches_too(write, fetch):
    eng, _, _ = make(write, FETCH_CONFIG)
    server = ControlServer(eng)
    reply, _ = read_in_thread(eng, lambda: server.handle({"cmd": "get", "name": "fetch.item[cli]"}))
    assert reply == {"ok": True, "value": "v:cli", "known": True}


def test_unknown_states_and_bad_names_dont_wait(write, fetch, monkeypatch):
    monkeypatch.setattr(engine_mod, "ASK_SECONDS", 0.3)
    eng, _, _ = make(write, FETCH_CONFIG)
    value, took = read_in_thread(eng, lambda: eng.deck.state("fetch.never[x]", "default"))
    assert value == "default" and 0.25 < took < 2            # asked, waited, gave up
    value, took = read_in_thread(eng, lambda: eng.deck.state("fetch.never[x]", "default"))
    assert value == "default" and took < 0.25               # asked already: no second wait
    for name in ("fetch.item[bad]", "fetch.item", "fetch.nope[x]", "rec.on", "nothing.at[all]", "x"):
        value, took = read_in_thread(eng, lambda: eng.deck.state(name, "default"))
        assert value == "default" and took < 0.25, name


def test_the_engine_thread_never_waits(write, fetch, monkeypatch):
    monkeypatch.setattr(engine_mod, "ASK_SECONDS", 1)
    eng, _, _ = make(write, FETCH_CONFIG)                    # make() stepped on this thread
    start = time.monotonic()
    assert eng.deck.state("fetch.never[y]") is None
    assert time.monotonic() - start < 0.5


def test_a_reload_forgets_what_was_read(write, fetch):
    eng, _, _ = make(write, FETCH_CONFIG)
    read_in_thread(eng, lambda: eng.deck.state("fetch.item[other]"))
    write("config.yaml", FETCH_CONFIG.replace("shown", "changed"))
    eng.reload(); eng.step()
    assert eng.plugins["fetch"].watched_states == {("item", "changed")}
