from hubdeck.config import ConfigSource
from hubdeck.device.fake import FakeControls, FakeKeyModule
from hubdeck.device.layout import axis_sign, image_rotation, index_of, number_of, numbering
from hubdeck.engine import Engine


def test_default_numbering_is_reading_order():
    assert numbering("keys", 0) == tuple(range(15))
    assert numbering("knobs", 0) == (2, 0, 1)        # top, bottom-left, bottom-right
    assert numbering("rollers", 0) == (1, 0)         # left, right
    assert numbering("faders", 0) == (0, 1, 2, 3, 4)


def test_rotated_keys():
    # 90 deg clockwise: 3 wide x 5 tall; the new top-left key is the old bottom-left (index 10)
    assert index_of("keys", 1, 90) == 10 and index_of("keys", 3, 90) == 0
    # 180: everything reversed
    assert index_of("keys", 1, 180) == 14 and number_of("keys", 0, 180) == 15
    for r in (0, 90, 180, 270):
        assert sorted(numbering("keys", r)) == list(range(15))


def test_rotated_faders_and_axis():
    # 270 (90 counter-clockwise): fader 1 ends up at the bottom, so the top fader is index 4
    assert numbering("faders", 270) == (4, 3, 2, 1, 0)
    assert [axis_sign(r) for r in (0, 90, 180, 270)] == [1, 1, -1, -1]   # up -> right -> down -> left
    assert [image_rotation(r) for r in (0, 90, 180, 270)] == [0, 270, 180, 90]


CONFIG = """
device: {reassert_seconds: 0}
modules: {faders: {rotate: 270}, rollers: {rotate: 180}, keys: {rotate: 180}}
keys: {1: {action: rec.a, face: {label: One}}}
faders:
  1: {target: rec.level}
rollers:
  1: {turn: rec.level}
"""


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_engine_applies_rotation(write, recorder):
    path = write("config.yaml", CONFIG)
    devs = {"keys": FakeKeyModule(), "faders": FakeControls("faders"), "rollers": FakeControls("rollers")}
    eng = Engine(ConfigSource(path), factories={k: (lambda d=d: d) for k, d in devs.items()}, clock=Clock())
    eng.load_initial()
    eng.step()
    plugin = recorder.instances[-1]
    from hubdeck.plugins.base import value_state
    plugin.emit(value_state("level", None), 30); eng.step()

    assert 14 in devs["keys"].images and 0 not in devs["keys"].images   # key 1 drawn on module key 15
    devs["keys"].press(14); eng.step()
    assert ("a", None) in plugin.calls

    f = devs["faders"]                         # fader 1 = top = module index 4; raw scale reversed
    f.push("slide", 4, 100); eng.step()        # baseline: raw 100 = left end = 0
    f.push("slide", 4, 72); eng.step()         # 28: within takeover window of 30
    f.push("slide", 4, 40); eng.step()         # 60
    assert [c for c in plugin.calls if c[0] == "set"] == [("set", "level", None, 28), ("set", "level", None, 60)]

    devs["rollers"].push("turn", 0, 1); eng.step()   # roller 1 at 180 deg = right roller (index 0), direction flipped
    assert plugin.calls[-1] == ("adjust", "level", None, -2)
