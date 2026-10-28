import pytest

from hubdeck.config import ConfigError, ConfigSource, load_config
from hubdeck.config.schema import Animate, Lighting, LightZone
from hubdeck.device.fake import FakeControls, FakeKeyModule
from hubdeck.engine import Engine
from hubdeck.lighting import LedState, LightingController, animate_color, gamma_rgb, resolve
from hubdeck.plugins.base import value_state


def zone(**kw):
    return LightZone.model_validate(kw)


def test_color_alone_means_static():
    assert resolve(zone(color="red"), {}.get, None, 0) == LedState("static", (255, 0, 0), None)


@pytest.mark.parametrize("cache", [None, {}])
def test_state_overlay(cache):
    z = zone(color="#3cc86e", state="rec.on", on={"color": "red", "mode": "breathing"},
             unknown={"color": "#202020"})
    for _ in range(2):                                            # the second round from the cache
        assert resolve(z, {"rec.on": True}.get, None, 0, cache=cache) == LedState("breathing", (255, 0, 0), None)
        assert resolve(z, {"rec.on": False}.get, None, 0, cache=cache).rgb == (0x3C, 0xC8, 0x6E)
        assert resolve(z, {}.get, None, 0, cache=cache).rgb == (0x20, 0x20, 0x20)
    assert cache is None or len(cache) == 3


def test_state_overlay_matching_a_value():
    z = zone(color="#3cc86e", state={"rec.value": 3}, on={"color": "red"})
    assert resolve(z, {"rec.value": 3}.get, None, 0).rgb == (255, 0, 0)
    assert resolve(z, {"rec.value": 30}.get, None, 0).rgb == (0x3C, 0xC8, 0x6E)   # numbers must be equal


def test_brightness_follows_value_with_minimum():
    z = zone(color="white", brightness="value", min_brightness=10)
    assert resolve(z, {}.get, 0, 0).brightness == 10
    assert resolve(z, {}.get, 50, 0).brightness == 55
    assert resolve(z, {}.get, 100, 0).brightness == 100
    assert resolve(z, {}.get, None, 0).brightness == 10          # value unknown
    assert resolve(zone(brightness="value", min_brightness=0), {}.get, 0, 0).brightness == 0


def test_gamma_makes_colours_purer_and_dimming_deeper():
    assert gamma_rgb((0x3C, 0xC8, 0x6E), 2.2) == (11, 149, 40)      # green, not turquoise
    assert gamma_rgb((0xC8, 0x1E, 0x28), 2.2) == (149, 2, 4)        # red, not pink
    assert gamma_rgb((255, 255, 255), 2.2) == (255, 255, 255)
    z = zone(color="white", brightness="value", min_brightness=10)
    assert resolve(z, {}.get, 50, 0, gamma=2.2).brightness == 30    # was 55 without correction
    assert resolve(z, {}.get, 100, 0, gamma=2.2).brightness == 100


def test_animations():
    pulse = Animate(effect="pulse", colors=["#ff0000"], period=2)
    assert animate_color(pulse, 0) == (0, 0, 0) and animate_color(pulse, 1) == (255, 0, 0)
    blink = Animate(effect="blink", colors=["#ffffff", "#000000"], period=1)
    assert animate_color(blink, 0.2) == (255, 255, 255) and animate_color(blink, 0.7) == (0, 0, 0)
    cycle = Animate(effect="cycle", colors=["#ff0000", "#0000ff"], period=2)
    assert animate_color(cycle, 0) == (255, 0, 0) and animate_color(cycle, 1) == (0, 0, 255)
    assert resolve(zone(animate={"effect": "pulse", "colors": ["red"]}), {}.get, None, 1).mode == "static"


def test_all_merges_under_numbered_zones():
    lit = Lighting.model_validate({"knobs": {"all": {"color": "white", "brightness": 50}, 2: {"color": "red"}}})
    zones = {(k, i): z for k, i, z in lit.zones()}
    assert zones[("knobs", 0)].color == "white" and zones[("knobs", 1)].color == "red"
    assert zones[("knobs", 1)].brightness == 50


def test_controller_writes_only_changes():
    dev = FakeControls("knobs")
    ctl = LightingController()
    zones = [("knobs", 0, zone(color="red", brightness=40))]
    ctl.update(zones, {"knobs": dev}, {}.get, lambda k, i: None, 0)
    assert dev.leds == [("mode", 0, "static"), ("color", 0, (255, 0, 0)), ("brightness", 0, 40)]
    ctl.update(zones, {"knobs": dev}, {}.get, lambda k, i: None, 1)
    assert len(dev.leds) == 3                                    # nothing changed
    ctl.update([("knobs", 0, zone(color="blue", brightness=40))], {"knobs": dev}, {}.get, lambda k, i: None, 2)
    assert dev.leds[3:] == [("color", 0, (0, 0, 255))]
    ctl.forget("knobs")
    ctl.update(zones, {"knobs": dev}, {}.get, lambda k, i: None, 3)
    assert len(dev.leds) == 7                                    # full rewrite after reconnect


ENGINE_CONFIG = """
device: {reassert_seconds: 0}
lighting:
  base: {color: "#3cc86e", state: rec.on, on: {color: red, mode: breathing}}
  knobs:
    all: {color: white, brightness: value, min_brightness: 10}
keys: {1: {action: rec.a}}
knobs:
  1: {turn: rec.level}
"""


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_engine_lighting_follows_states_and_values(write, recorder):
    path = write("config.yaml", ENGINE_CONFIG)
    devs = {"keys": FakeKeyModule(), "knobs": FakeControls("knobs"), "base": FakeControls("base")}
    clock = Clock()
    eng = Engine(ConfigSource(path), factories={k: (lambda d=d: d) for k, d in devs.items()}, clock=clock)
    eng.load_initial()
    eng.step()
    base, knobs = devs["base"], devs["knobs"]
    assert ("color", 0, gamma_rgb((0x3C, 0xC8, 0x6E), 2.2)) in base.leds   # default led_gamma
    assert ("brightness", 0, 10) in knobs.leds                   # value unknown -> minimum
    plugin = recorder.instances[-1]
    plugin.emit("on", True)
    plugin.emit(value_state("level", None), 100)
    clock.t += 1; eng.step()
    assert base.leds[-2:] == [("mode", 0, "breathing"), ("color", 0, (255, 0, 0))]
    assert ("brightness", 2, 100) in knobs.leds                  # knob 1 (top, LED 2) follows its value
    assert ("brightness", 0, 10) in knobs.leds                   # knob 2 (LED 0) has no target


@pytest.mark.parametrize("body, message", [
    ("lighting: {base: {brightness: value}}", "needs a knob or fader"),
    ("lighting: {knobs: {4: {color: red}}}", "use 'all' or 1-3"),
    ("lighting: {base: {color: nope}}", "unknown colour"),
    ("lighting: {base: {on: {color: red}}}", "need a 'state'"),
    ("lighting: {base: {state: rec.missing, on: {color: red}}}", "no state 'missing'"),
    ("lighting: {base: {mode: disco}}", "mode"),
    ("lighting: {base: {animate: {effect: cycle, colors: [red]}}}", "at least 2"),
])
def test_lighting_validation(write, body, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write("config.yaml", body + "\nkeys: {1: {action: rec.a}}\n"))
