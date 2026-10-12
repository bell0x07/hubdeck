"""deck: the Dial, knobs, rollers, faders and Base from Python (fake modules)."""

import pytest

from hubdeck.device import layout
from hubdeck.plugins.base import value_state
from test_controls import make
from test_engine import wait_for

CONFIG = """
device: {reassert_seconds: 0}
pages:
  main:
    keys:
      1: {action: {python: "seen.append(deck.control)"}, face: {label: One}}
    dial:
      turn: {target: rec.level, step: 3}
      action: rec.a
      face: {icon: volume, label: Dial}
    knobs:
      1: {turn: rec.level, action: rec.start}
      2: {turn: {target: rec.app, arg: spotify.exe, invert: true}}
      3: {action: {python: "seen.append(deck.control)"}, turn: {cw: {python: "seen.append(('cw', deck.control))"}, ccw: rec.b}}
    rollers:
      1: {turn: {cw: rec.a, ccw: rec.b}}
    faders:
      1: {target: rec.app, arg: discord.exe}
  other:
    keys:
      1: {action: page.back, face: {label: Back}}
lighting:
  knobs: {all: {color: white, brightness: 50}}
"""
ALL = ("keys", "dial", "knobs", "rollers", "faders", "base")


def rec(recorder):
    return recorder.instances[-1]


def test_dial_face(write):
    eng, devs, _ = make(write, CONFIG, ALL)
    before = devs["dial"].images[0]
    eng.deck.dial.set_face(label="Hi", bg="red")
    eng.step()
    assert eng.deck.dial.face().label == "Hi" and devs["dial"].images[0] != before
    eng.deck.dial.clear_face()
    eng.step()
    assert eng.deck.dial.face().label == "Dial" and devs["dial"].images[0] == before


def test_dial_face_replace_hides_the_config_icon(write):
    eng, devs, _ = make(write, CONFIG, ALL)
    eng.deck.dial.set_face(text="3", label="presses")
    eng.step()
    assert eng.deck.dial.face().icon == "volume"     # merged: the config's icon stays
    eng.deck.dial.set_face(replace=True, text="3", label="presses")
    eng.step()
    face = eng.deck.dial.face()
    assert (face.icon, face.text, face.label) == (None, "3", "presses")


def test_dial_face_opens_the_dial_when_the_config_has_none(write):
    eng, devs, _ = make(write, "keys: {1: {face: {label: x}}}\n", ALL)
    assert "dial" not in eng.manager.devices
    eng.deck.dial.set_face(text="12:00")
    eng.step()
    assert "dial" in eng.manager.devices and devs["dial"].images


def test_dial_press_turn_and_value(write, recorder):
    eng, devs, _ = make(write, CONFIG, ALL)
    eng.deck.dial.press()
    eng.deck.dial.turn(2)
    rec(recorder).emit(value_state("level", None), 40)
    eng.step()
    assert rec(recorder).calls == [("a", None), ("adjust", "level", None, 6)]
    assert eng.deck.dial.value() == 40 and eng.deck.knobs[1].value() == 40
    assert eng.deck.knobs[3].value() is None          # cw/ccw actions, no target


def test_knobs_press_turn_and_held(write, recorder):
    eng, devs, _ = make(write, CONFIG, ALL)
    eng.deck.knobs[1].press()
    eng.deck.knobs[2].turn(1)                          # inverted, step 2
    eng.step()
    assert rec(recorder).calls == [("start", None), ("adjust", "app", "spotify.exe", -2)]
    idx = layout.index_of("knobs", 2, 0)
    devs["knobs"].push("key", idx, True); eng.step()
    assert eng.deck.knobs[2].held and not eng.deck.knobs[1].held
    devs["knobs"].push("key", idx, False); eng.step()
    assert not eng.deck.knobs[2].held


def test_rollers_turn(write, recorder):
    eng, devs, _ = make(write, CONFIG, ALL)
    eng.deck.rollers[1].turn(-2)
    eng.step()
    assert rec(recorder).calls == [("b", None), ("b", None)]
    with pytest.raises(ValueError, match=r"deck\.rollers\[2\] has no 'turn'"):
        eng.deck.rollers[2].turn(1)
    with pytest.raises(ValueError, match="steps"):
        eng.deck.rollers[1].turn(0)


def test_faders(write, recorder):
    eng, devs, _ = make(write, CONFIG, ALL)
    assert eng.deck.faders[1].position is None
    devs["faders"].push("slide", layout.index_of("faders", 1, 0), 70)
    eng.step()
    assert eng.deck.faders[1].position == 70
    eng.deck.faders[1].move_to(30)
    rec(recorder).emit(value_state("app", "discord.exe"), 30)
    eng.step()
    assert ("set", "app", "discord.exe", 30) in rec(recorder).calls
    assert eng.deck.faders[1].value() == 30
    with pytest.raises(ValueError, match="no target"):
        eng.deck.faders[2].move_to(10)
    with pytest.raises(ValueError, match="0-100"):
        eng.deck.faders[1].move_to(101)


def test_lighting_goes_on_top_of_the_config(write):
    eng, devs, _ = make(write, CONFIG, ALL)
    leds = devs["knobs"].leds
    idx = layout.index_of("knobs", 2, 0)
    eng.deck.knobs[2].set_light(color="red")
    eng.step()
    assert ("color", idx, (255, 0, 0)) in leds
    zone = next(z for kind, i, z in eng._light_zones if (kind, i) == ("knobs", idx))
    assert (zone.color, zone.brightness) == ("red", 50)      # brightness still from the config
    leds.clear()
    eng.deck.knobs[2].clear_light()
    eng.step()
    assert ("color", idx, (255, 255, 255)) in leds
    assert ("mode", idx, "off") not in leds                  # the config lights it: back to that


def test_clearing_a_light_the_config_doesnt_have_turns_it_off(write):
    eng, devs, _ = make(write, CONFIG, ALL)
    eng.deck.base.set_light(color="blue")
    eng.step()
    eng.deck.base.clear_light()
    eng.step()
    assert devs["base"].leds[-1] == ("mode", 0, "off")
    eng.deck.base.clear_light()                              # nothing to clear: no write
    eng.step()
    assert devs["base"].leds.count(("mode", 0, "off")) == 1


def test_example_config_python_keys_reach_every_module(write):
    from hubdeck.paths import EXAMPLE_CONFIG
    eng, devs, _ = make(write, EXAMPLE_CONFIG.read_text(encoding="utf-8"), ALL)
    leds = ("base", "knobs", "faders")

    def tap(key, until):
        idx = layout.index_of("keys", key, 0)
        devs["keys"].press(idx); eng.step()
        devs["keys"].release(idx); eng.step()
        wait_for(lambda: eng.step() or until())

    tap(7, lambda: all(devs[k].leds for k in leds) and eng.resolve_face(8).text == "1")
    assert eng.deck.dial.face().text == "1" and devs["dial"].images
    tap(8, lambda: all(devs[k].leds[-1][2] == "off" for k in leds))
    assert eng.resolve_face(8).text == "0" and eng.deck.dial.face() is None


def test_base_and_fader_lighting_open_their_modules(write):
    eng, devs, _ = make(write, CONFIG, ALL)
    assert "base" not in eng.manager.devices
    eng.deck.base.set_light(mode="breathing", color="#00ff00")
    eng.deck.faders[3].set_light(animate={"effect": "blink", "colors": ["red"]})
    eng.step()
    assert ("mode", 0, "breathing") in devs["base"].leds
    assert any(kind == "color" and i == layout.index_of("faders", 3, 0) for kind, i, _ in devs["faders"].leds)


@pytest.mark.parametrize("call, message", [
    (lambda d: d.base.set_light(brightness="value"), "needs a knob or fader"),
    (lambda d: d.knobs[1].set_light(mode="disco"), "bad lighting"),
    (lambda d: d.knobs[1].set_light(colour="red"), "unknown lighting field"),
    (lambda d: d.knobs[1].press(gesture="long_press"), "no on_long_press"),
    (lambda d: d.dial.set_face(value_of=d.keys[1]), "value_of must be a dial, knob, roller or fader"),
    (lambda d: d.dial.set_face(label="x", seconds=0), "seconds must be a number above 0"),
])
def test_bad_module_calls(write, call, message):
    eng, _, _ = make(write, CONFIG, ALL)
    with pytest.raises(ValueError, match=message):
        call(eng.deck)


def test_control_tells_which_control_started_the_code(write):
    eng, devs, _ = make(write, CONFIG, ALL)
    seen = eng.plugins["python"].namespace["seen"] = []
    idx = layout.index_of("knobs", 3, 0)
    devs["knobs"].push("key", idx, True); eng.step()
    devs["knobs"].push("key", idx, False); eng.step()
    devs["knobs"].push("turn", idx, 1); eng.step()
    eng.deck.press(1)
    eng.step()
    wait_for(lambda: len(seen) == 3)
    assert set(map(repr, seen)) == {"deck.knobs[3]", "('cw', deck.knobs[3])", "deck.keys[1]"}


def test_on_turn_runs_after_the_turn(write, recorder):
    eng, devs, _ = make(write, """
knobs: {1: {turn: rec.level, on_turn: rec.a}}
rollers: {1: {turn: {cw: rec.b, ccw: rec.b}, on_turn: [rec.a, rec.start]}}
""", ALL)
    devs["knobs"].push("turn", layout.index_of("knobs", 1, 0), 2)
    eng.step()
    assert rec(recorder).calls == [("adjust", "level", None, 4), ("a", None)]
    devs["rollers"].push("turn", layout.index_of("rollers", 1, 0), 2)
    eng.step()
    assert rec(recorder).calls[2:] == [("b", None), ("b", None), ("a", None), ("start", None)]


def test_dial_face_follows_another_controls_value_for_a_while(write, recorder):
    eng, devs, clock = make(write, CONFIG, ALL)
    rec(recorder).emit(value_state("level", None), 40)
    rec(recorder).emit(value_state("app", "spotify.exe"), 70)
    eng.deck.dial.set_face(icon="brand-spotify", label="{value}%", gauge=True, value_of=eng.deck.knobs[2],
                           replace=True, seconds=1.5)
    eng.step()
    face = eng.deck.dial.face()
    assert (face.icon, face.label, face.gauge) == ("brand-spotify", "70%", 70)
    rec(recorder).emit(value_state("app", "spotify.exe"), 72)
    eng.step()
    assert eng.deck.dial.face().label == "72%"               # live

    clock.t += 1
    eng.deck.dial.set_face(value_of=eng.deck.knobs[1], seconds=1.5)   # another knob: the countdown restarts
    eng.step()
    face = eng.deck.dial.face()
    assert (face.icon, face.label, face.gauge) == ("brand-spotify", "40%", 40)
    clock.t += 1
    eng.step()
    assert eng.deck.dial.face().icon == "brand-spotify"
    clock.t += 0.6
    eng.step()
    face = eng.deck.dial.face()
    assert (face.icon, face.label, face.gauge) == ("volume", "Dial", None)   # the config's face again

    eng.deck.dial.set_face(label="kept", seconds=1)
    eng.deck.dial.set_face(label="kept")                     # a later call without seconds keeps it
    clock.t += 5
    eng.step()
    assert eng.deck.dial.face().label == "kept"


def test_knob_shows_its_value_on_the_dial_while_turned(write, recorder):
    eng, devs, clock = make(write, """
device: {reassert_seconds: 0}
dial:
  turn: rec.level
  state: rec.on                                    # a state that is false doesn't change what {value} shows
  face: {icon: volume, label: "{value}%", gauge: true}
  face_on: {icon: volume-off}
knobs:
  2:
    turn: {target: rec.app, arg: spotify.exe}
    on_turn:
      python: 'deck.dial.set_face(icon="brand-spotify", label="{value}%", gauge=True, value_of=deck.control, replace=True, seconds=1.5)'
""", ALL)
    rec(recorder).emit(value_state("level", None), 40)
    rec(recorder).emit(value_state("app", "spotify.exe"), 70)
    rec(recorder).emit("on", False)
    eng.step()
    face = eng.deck.dial.face()
    assert (face.icon, face.label, face.gauge) == ("volume", "40%", 40)
    devs["knobs"].push("turn", layout.index_of("knobs", 2, 0), 1)
    eng.step()
    assert rec(recorder).calls == [("adjust", "app", "spotify.exe", 2)]
    wait_for(lambda: (eng.step(), eng.deck.dial.face().icon)[1] == "brand-spotify")
    face = eng.deck.dial.face()
    assert (face.label, face.gauge) == ("70%", 70) and devs["dial"].images
    clock.t += 1.6
    eng.step()
    face = eng.deck.dial.face()
    assert (face.icon, face.label, face.gauge) == ("volume", "40%", 40)
