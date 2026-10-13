"""States with an argument: plugin.state[argument] in state:, lighting and {templates}."""

import pytest

from hubdeck.config import ConfigError, load_config
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
