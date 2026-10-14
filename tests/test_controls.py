"""Dial, knobs, rollers and faders in the engine (fake modules)."""

import pytest

from hubdeck.config import ConfigError, ConfigSource, load_config
from hubdeck.device.fake import FakeControls, FakeKeyModule
from hubdeck.device.modules import DialModule, Event, KnobsModule, RollersModule
from hubdeck.engine import Engine
from hubdeck.plugins.base import adjusted, value_state

CONFIG = """
device: {reassert_seconds: 0}
pages:
  main:
    keys:
      1: {action: {page: other}, face: {label: Other}}
    dial:
      turn: {target: rec.level, step: 3}
      action: rec.a
      on_long_press: rec.b
      face: {icon: volume, label: "{value}%", gauge: true}
    knobs:
      1: {turn: rec.level, action: rec.start}
      2: {turn: {target: rec.app, arg: spotify.exe, invert: true}}
    rollers:
      1: {turn: {cw: rec.a, ccw: rec.b}}
    faders:
      1: {target: rec.app, arg: discord.exe}
  other:
    keys:
      1: {action: page.back, face: {label: Back}}
    dial:
      turn: {target: rec.app, arg: chrome.exe}
"""


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make(write, text=CONFIG, kinds=("keys", "dial", "knobs", "rollers", "faders")):
    path = write("config.yaml", text)
    devs = {k: (FakeKeyModule() if k == "keys" else FakeControls(k)) for k in kinds}
    clock = Clock()
    eng = Engine(ConfigSource(path), factories={k: (lambda d=d: d) for k, d in devs.items()}, clock=clock)
    eng.load_initial()
    eng.step()
    return eng, devs, clock


def rec(recorder):
    return recorder.instances[-1]


def test_dial_turn_adjusts_target_with_step(write, recorder):
    eng, devs, _ = make(write)
    devs["dial"].push("turn", 0, 2)
    devs["dial"].push("turn", 0, -1)
    eng.step()
    assert rec(recorder).calls == [("adjust", "level", None, 6), ("adjust", "level", None, -3)]


def test_turn_passes_expo_to_the_target(write, recorder):
    eng, devs, _ = make(write, "knobs: {1: {turn: {target: rec.level, step: 4, expo: 0.5}}}\n", kinds=("knobs",))
    devs["knobs"].push("turn", 2, -2)                     # knob 1 = top = module index 2
    eng.step()
    assert rec(recorder).calls == [("adjust", "level", None, -8, 0.5)]


def test_expo_curve():
    """Expo makes clicks finer near 0 and coarser near 100; the range takes as many clicks as linear."""
    assert adjusted(50, 2) == 52 and adjusted(99, 2) == 100 and adjusted(1, -2) == 0
    assert adjusted(50, 2, 0) == 52

    def clicks(expo, step=2):
        levels = [0.0]
        while levels[-1] < 100 - 1e-6:
            levels.append(adjusted(levels[-1], step, expo))
        return levels
    levels = clicks(0.5)
    assert len(levels) - 1 == 50
    assert levels[1] == pytest.approx(1.0004) and levels[-1] - levels[-2] == pytest.approx(3.9404)
    assert all(b - a < d - c for (a, b), (c, d) in zip(zip(levels, levels[1:]), zip(levels[1:], levels[2:])))
    back = [adjusted(v, -2, 0.5) for v in levels[1:]]     # turning back retraces the same levels
    assert back == pytest.approx(levels[:-1], abs=1e-6)
    assert clicks(1.0)[1] == pytest.approx(0.0008)        # full expo: very fine at the bottom


def test_dial_press_gestures(write, recorder):
    eng, devs, clock = make(write)
    devs["dial"].push("key", 0, True); eng.step()
    clock.t += 0.1
    devs["dial"].push("key", 0, False); eng.step()       # short press -> rec.a
    devs["dial"].push("key", 0, True); eng.step()
    clock.t += 0.7; eng.step()                            # long press -> rec.b
    devs["dial"].push("key", 0, False); eng.step()
    assert rec(recorder).calls == [("a", None), ("b", None)]


def test_dial_screen_and_software_mode(write, recorder):
    eng, devs, _ = make(write)
    dial = devs["dial"]
    assert dial.software_mode and 0 in dial.images
    before = dial.images[0]
    rec(recorder).emit(value_state("level", None), 40); eng.step()
    assert dial.images[0] != before                       # gauge + label redrawn
    face = eng.face_for(eng.def_for("dial", 0))
    assert face.gauge == 40 and face.label == "40%"


def test_unknown_value_shows_empty_gauge(write):
    eng, _, _ = make(write)
    face = eng.face_for(eng.def_for("dial", 0))
    assert face.gauge is None and face.gauge_known is False and face.label == "–%"


def test_gauge_can_follow_a_state(write, recorder):
    eng, _, _ = make(write, """
        rollers: {1: {turn: rec.level}}
        dial: {face: {text: "{rec.value}%", gauge: rec.value}}
    """)
    assert eng.face_for(eng.def_for("dial", 0)).gauge_known is False
    rec(recorder).emit("value", 37.5); eng.step()
    face = eng.face_for(eng.def_for("dial", 0))
    assert face.gauge == 37.5 and face.text == "37.5%"


def test_knobs_turn_invert_and_press(write, recorder):
    eng, devs, _ = make(write)
    # knob numbers follow reading order: 1 = top (module index 2), 2 = bottom-left (index 0)
    devs["knobs"].push("turn", 2, 1)
    devs["knobs"].push("turn", 0, 1)
    devs["knobs"].push("key", 2, True)
    eng.step()
    assert rec(recorder).calls == [("adjust", "level", None, 2), ("adjust", "app", "spotify.exe", -2),
                                   ("start", None)]


def test_roller_runs_actions_per_step(write, recorder):
    eng, devs, _ = make(write)
    # roller 1 = left = module index 1
    devs["rollers"].push("turn", 1, 2)
    devs["rollers"].push("turn", 1, -1)
    devs["rollers"].push("turn", 0, 1)                    # roller 2 (right) not configured
    eng.step()
    assert rec(recorder).calls == [("a", None), ("a", None), ("b", None)]


def test_fader_soft_takeover(write, recorder):
    eng, devs, _ = make(write)
    plugin = rec(recorder)
    plugin.emit(value_state("app", "discord.exe"), 50); eng.step()
    f = devs["faders"]
    f.push("slide", 0, 0); eng.step()                     # first report = where it sits: never applied
    f.push("slide", 0, 10); eng.step()                    # far from 50: ignored
    f.push("slide", 0, 30); eng.step()                    # still below: ignored
    f.push("slide", 0, 60); eng.step()                    # crossed 50: picked up
    f.push("slide", 0, 70); eng.step()
    assert plugin.calls == [("set", "app", "discord.exe", 60), ("set", "app", "discord.exe", 70)]


def test_fader_lets_go_when_the_value_changes_elsewhere(write, recorder):
    """After the volume is changed by the keyboard (or another fader), the fader must reach
    the new value again before it acts: no jump on the next touch."""
    eng, devs, clock = make(write)
    plugin = rec(recorder)
    key = value_state("app", "discord.exe")
    plugin.emit(key, 50); eng.step()
    f = devs["faders"]
    for v in (40, 50, 55):                                 # caught at 50, sets 55
        f.push("slide", 0, v); eng.step()
    plugin.emit(key, 55); eng.step()                       # its own change coming back: stays caught
    f.push("slide", 0, 58); eng.step()
    assert plugin.calls == [("set", "app", "discord.exe", 50), ("set", "app", "discord.exe", 55),
                            ("set", "app", "discord.exe", 58)]
    clock.t += 2
    plugin.emit(key, 20); eng.step()                       # keyboard sets 20
    plugin.calls.clear()
    f.push("slide", 0, 60); eng.step()                     # fader still up at 60: no jump
    f.push("slide", 0, 30); eng.step()                     # moving down, not there yet
    assert plugin.calls == []
    f.push("slide", 0, 18); eng.step()                     # crossed 20: caught again
    assert plugin.calls == [("set", "app", "discord.exe", 18)]


def test_fader_does_not_resend_the_same_value(write, recorder):
    eng, devs, _ = make(write)
    plugin = rec(recorder)
    plugin.emit(value_state("app", "discord.exe"), 50); eng.step()
    f = devs["faders"]
    for v in (49, 50, 51, 51):
        f.push("slide", 0, v); eng.step()
    assert plugin.calls == [("set", "app", "discord.exe", 50), ("set", "app", "discord.exe", 51)]


def test_fader_does_not_take_over_unknown_value(write, recorder):
    eng, devs, _ = make(write)
    f = devs["faders"]
    for v in (0, 10, 50, 90):                             # app volume never published
        f.push("slide", 0, v); eng.step()
    assert rec(recorder).calls == []


def test_sections_inherit_from_start_page_unless_overridden(write, recorder):
    eng, devs, clock = make(write)
    devs["keys"].press(0); eng.step(); eng.step()          # -> page "other"
    assert eng.current_page == "other"
    devs["dial"].push("turn", 0, 1); devs["knobs"].push("turn", 2, 1)   # knob 1 = top
    eng.step()
    # dial overridden on "other", knobs inherited from "main"
    assert sorted(rec(recorder).calls, key=str) == [("adjust", "app", "chrome.exe", 2), ("adjust", "level", None, 2)]


def test_module_plugged_in_later(write, recorder):
    path = write("config.yaml", CONFIG)
    dial = FakeControls("dial")
    available = {"keys": FakeKeyModule()}
    clock = Clock()

    def dial_factory():
        if "dial" not in available:
            from hubdeck.device import DeviceError
            raise DeviceError("not found")
        return available["dial"]

    eng = Engine(ConfigSource(path), factories={"keys": lambda: available["keys"], "dial": dial_factory}, clock=clock)
    eng.load_initial()
    eng.step()
    assert "dial" not in eng.manager.devices
    available["dial"] = dial
    clock.t += 5; eng.step()
    assert eng.manager.devices["dial"] is dial and dial.software_mode and dial.images


def test_unused_modules_are_not_opened(write):
    eng, devs, _ = make(write, "keys: {1: {action: rec.a}}\n")
    assert set(eng.manager.devices) == {"keys"}


@pytest.mark.parametrize("body, message", [
    ("dial: {turn: rec.nope}", "no value 'nope'"),
    ("dial: {turn: rec.app}", "rec.app needs arg"),
    ("dial: {face: {gauge: true}}", "need a turn target"),
    ("dial: {face: {gauge: cpu}}", "or a state like system.cpu"),
    ("dial: {face: {gauge: rec.nope}}", "rec has no state 'nope'"),
    ("dial: {face: {label: '{value}'}}", "need a turn target"),
    ("rollers: {1: {turn: rec.level, action: rec.a}}", "can't be pressed"),
    ("knobs: {4: {turn: rec.level}}", "knob numbers must be 1-3"),
    ("faders: {1: {target: volume}}", "plugin.value"),
    ("dial: {turn: {target: rec.level, cw: rec.a}}", "not both"),
    ("dial: {turn: {target: rec.level, expo: 30}}", "expo"),
])
def test_control_validation(write, body, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write("config.yaml", body))


# -- drivers decode raw frames ------------------------------------------------

class FakeHid:
    def __init__(self, frames):
        self.frames = frames

    def read_frames(self):
        from hubdeck.device.hid import parse_frame
        return [parse_frame(bytes.fromhex(f), self.tag) for f in self.frames]


def _driver(cls, frames, tag):
    d = cls.__new__(cls)
    d.hid = FakeHid(frames)
    d.hid.tag = tag
    if cls is KnobsModule:
        d.pressed = [False] * 3
    return d


def test_dial_driver_decodes_turn_and_press():
    d = _driver(DialModule, ["014403000301 00", "01440300030101", "01440300040101", "01440300040100"], 0x44)
    assert d.poll() == [Event("turn", 0, 1), Event("turn", 0, -1), Event("key", 0, True), Event("key", 0, False)]


def test_knobs_driver_decodes_and_maps_leds():
    d = _driver(KnobsModule, ["012a0500000301ffff", "012a050000 03ff00ff", "012a0500030300 0100", "012a0500030300 0000"],
                0x2A)
    assert d.poll() == [Event("turn", 0, 1), Event("turn", 1, -1), Event("key", 1, True), Event("key", 1, False)]
    assert [d._led_index(i) for i in range(3)] == [0, 1, 2]   # same order as the inputs
    assert d._led_index(None) == 0xFF                         # all knobs


def test_rollers_driver():
    d = _driver(RollersModule, ["012a04000102 00ff", "012a04000102 ff01"], 0x2A)
    assert d.poll() == [Event("turn", 0, 1), Event("turn", 1, -1)]


HOME_AFTER = CONFIG.replace("  other:\n", "  other:\n    home_after: {idle: 60, press: 15}\n")


def test_home_after_idle(write, recorder):
    eng, devs, clock = make(write, HOME_AFTER)
    devs["keys"].press(0); eng.step()
    devs["keys"].release(0); eng.step()           # goto is applied on the next step
    assert eng.current_page == "other"
    clock.t += 59; eng.step()
    devs["knobs"].push("turn", 0, 1); eng.step()   # turning counts as use: 60 s from here
    clock.t += 59; eng.step()
    assert eng.current_page == "other"
    clock.t += 1; eng.step()
    assert eng.current_page == "main"


def test_home_after_press(write, recorder):
    eng, devs, clock = make(write, HOME_AFTER)
    eng.goto_page("other"); eng.step()
    devs["keys"].press(5); eng.step()              # any key, even one without actions
    clock.t += 10
    devs["keys"].release(5); eng.step()            # letting go doesn't count
    clock.t += 4.9; eng.step()
    assert eng.current_page == "other"
    clock.t += 0.1; eng.step()
    assert eng.current_page == "main"
    clock.t += 100; eng.step()
    assert eng.current_page == "main" and eng._home_at is None
