import os

from hubdeck.config import ConfigSource, load_config
from hubdeck.device.fake import FakeKeyModule
from hubdeck.engine import Engine, format_value

BASE = """
device: {ownership: shared, reassert_seconds: 0}
pages:
  main:
    keys:
      1: {action: {page: sub}, face: {label: Sub}}
      2:
        on_down: rec.start
        on_up: rec.end
        state: rec.on
        face: {label: off}
        face_on: {label: ON, bg: red}
        face_unknown: {label: "?"}
      3: {face: {text: "{rec.value:.1f}"}}
  sub:
    keys:
      1: {action: page.back, face: {label: Back}}
      5: {action: rec.a, on_long_press: rec.b, face: {label: Five}}
"""


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(write, text=BASE):
    path = write("config.yaml", text)
    clock = Clock()
    dev = FakeKeyModule()
    eng = Engine(ConfigSource(path), device_factory=lambda: dev, clock=clock)
    eng.load_initial()
    eng.step()
    return eng, dev, clock


def tap(eng, dev, clock, idx, hold=0.05):
    dev.press(idx)
    eng.step()
    clock.t += hold
    dev.release(idx)
    eng.step()


def rec(recorder):
    return recorder.instances[-1]


def test_shared_ownership_paints_only_used_keys(write):
    eng, dev, _ = make(write)
    assert dev.software_mode
    assert set(dev.images) == {0, 1, 2, 4}      # keys 1, 2, 3, 5 (5 is used on page "sub")


def test_exclusive_ownership_paints_all(write):
    eng, dev, _ = make(write, BASE.replace("ownership: shared", "ownership: exclusive"))
    assert set(dev.images) == set(range(15))


def test_page_navigation_and_long_press(write, recorder):
    eng, dev, clock = make(write)
    tap(eng, dev, clock, 0)
    eng.step()                                    # goto is applied on the next step
    assert eng.current_page == "sub"
    tap(eng, dev, clock, 4)                       # short press -> rec.a (on release)
    dev.press(4); eng.step()
    clock.t += 0.6; eng.step()                    # long press -> rec.b
    dev.release(4); eng.step()
    assert rec(recorder).calls == [("a", None), ("b", None)]
    tap(eng, dev, clock, 0)
    assert eng.current_page == "main"


def test_hold_actions_and_state_faces(write, recorder):
    eng, dev, clock = make(write)
    unknown = dev.images[1]
    plugin = rec(recorder)
    dev.press(1); eng.step()
    plugin.emit("on", True); eng.step()
    on = dev.images[1]
    dev.release(1); eng.step()
    plugin.emit("on", False); eng.step()
    assert plugin.calls == [("start", None), ("end", None)]
    assert len({unknown, on, dev.images[1]}) == 3


def test_live_text_only_rewrites_when_text_changes(write, recorder):
    eng, dev, _ = make(write)
    plugin = rec(recorder)
    plugin.emit("value", 1.04); eng.step()
    writes = dev.writes.count(2)
    plugin.emit("value", 1.01); eng.step()        # still "1.0" -> no USB write
    assert dev.writes.count(2) == writes
    plugin.emit("value", 2.0); eng.step()
    assert dev.writes.count(2) == writes + 1


def test_hold_survives_page_switch(write, recorder):
    text = BASE.replace("face: {label: off}", "face: {label: off}\n        on_press: {page: sub}")
    eng, dev, clock = make(write, text)
    dev.press(1); eng.step(); eng.step()          # down: rec.start + switch to sub
    assert eng.current_page == "sub"
    dev.release(1); eng.step()                    # up still goes to the key it started on
    assert rec(recorder).calls == [("start", None), ("end", None)]


def test_hot_reload_and_bad_config_keeps_old(write, recorder):
    eng, dev, clock = make(write)
    path = eng.source.path
    events = []
    eng.listeners.append(lambda e, d: events.append(e))

    path.write_text(BASE.replace("{label: Sub}", "{label: Changed}"), encoding="utf-8")
    os.utime(path, ns=(1, 1_000_000_000))
    clock.t += 2; eng.step()
    assert eng.cfg.pages["main"].keys[1].face.label == "Changed"
    assert events[-1] == "reloaded"

    path.write_text("keys: {1: [broken", encoding="utf-8")
    os.utime(path, ns=(1, 2_000_000_000))
    clock.t += 2; eng.step()
    assert events[-1] == "error"
    assert eng.cfg.pages["main"].keys[1].face.label == "Changed"


def test_reload_restarts_only_changed_plugins(write, recorder):
    eng, dev, clock = make(write, BASE + "plugins: {rec: {x: 1}}\n")
    first = rec(recorder)
    eng._apply(load_config(eng.source.path))     # same settings: keep instance
    assert rec(recorder) is first and not first.stopped
    eng.source.path.write_text(BASE + "plugins: {rec: {x: 2}}\n", encoding="utf-8")
    eng._apply(load_config(eng.source.path))
    assert first.stopped and rec(recorder) is not first


def test_device_reconnect(write):
    from hubdeck.device import DeviceError
    eng, dev, clock = make(write)

    def boom():
        raise DeviceError("unplugged")
    dev.poll = boom
    eng.step()
    assert eng.device is None
    clock.t += 5
    eng.device_factory = FakeKeyModule
    eng.step()
    assert eng.device is not None and eng.device.images


def test_format_value():
    from datetime import datetime
    assert format_value(datetime(2026, 1, 2, 3, 4), "%H:%M") == "03:04"
    assert format_value(12.345, ".0f") == "12"
    assert format_value(None, None) == "–"
    assert format_value(True, None) == "on"
