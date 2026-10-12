import io
import threading

import pytest
from PIL import Image

from hubdeck.config import load_config
from hubdeck.device import layout
from hubdeck.plugins import REGISTRY
from hubdeck.render.faces import BLANK, render
from test_engine import make, tap, wait_for

CONFIG = """
device: {ownership: shared, reassert_seconds: 0}
pages:
  main:
    keys:
      1: {action: {python: "gate.wait(5); deck.set_face(deck.key, label='hit')"}, face: {label: One}}
      2: {action: rec.a, on_long_press: {rec.b: 2}, face: {label: Two}}
  sub:
    keys:
      1: {action: page.back, face: {label: Back}}
      3: {face: {text: "{vars.count}"}}
"""

RED = bytes((255, 0, 0))


def test_set_face_overrides_config_and_clear_restores(write):
    eng, dev, _ = make(write, CONFIG)
    before = dev.images[0]
    eng.deck.set_face(1, label="X", bg="red")
    eng.step()
    face = eng.resolve_face(1)
    assert (face.label, face.bg) == ("X", (255, 0, 0))
    eng.deck.set_face(1, icon="star", bg=None)     # merges; None resets bg to the config's
    eng.step()
    face = eng.resolve_face(1)
    assert (face.label, face.icon, face.bg) == ("X", "star", (25, 25, 30))
    eng.deck.set_face(1, label="")                 # empty hides the label
    eng.step()
    assert not eng.resolve_face(1).label
    eng.deck.clear_face(1)
    eng.step()
    assert dev.images[0] == before


def test_set_face_replace_drops_the_config_face_and_earlier_fields(write):
    eng, dev, _ = make(write, CONFIG)
    eng.deck.set_face(1, bg="red")
    eng.deck.set_face(1, replace=True, text="5")
    eng.step()
    face = eng.resolve_face(1)
    assert (face.text, face.label, face.bg) == ("5", None, (25, 25, 30))
    eng.deck.set_face(1, label="n")                # later calls merge, still without the config's face
    eng.deck.set_face(1, text=None)                # None resets to the default style, not the config
    eng.step()
    face = eng.resolve_face(1)
    assert (face.text, face.label) == (None, "n")
    eng.deck.clear_face(1)
    eng.step()
    assert eng.resolve_face(1).label == "One"


def test_set_face_on_unused_key_paints_and_releases_it(write):
    eng, dev, _ = make(write, CONFIG)
    assert 9 not in dev.images
    eng.deck.set_face(10, icon="star")
    eng.step()
    assert 9 in dev.images
    eng.deck.clear_faces()
    eng.step()
    assert dev.images[9] == dev.encode(render(BLANK, 72))
    assert 10 not in eng._owned


def test_faces_belong_to_a_page(write):
    eng, dev, _ = make(write, CONFIG)
    eng.deck.set_face(1, page="sub", label="Z")
    eng.step()
    assert eng.resolve_face(1).label == "One"
    eng.deck.goto("sub")
    eng.step()
    assert eng.resolve_face(1).label == "Z"


def test_faces_survive_reload(write):
    eng, dev, _ = make(write, CONFIG)
    eng.deck.set_face(1, label="X", image=Image.new("RGB", (8, 8), "red"))
    eng.step()
    eng._apply(load_config(eng.source.path))
    eng.step()
    assert eng.resolve_face(1).label == "X"
    assert dev.images[0][:3] == RED                # the in-memory picture survived too


def test_page_navigation(write):
    eng, _, _ = make(write, CONFIG)
    eng.deck.goto("sub"); eng.step()
    assert eng.deck.page == "sub"
    eng.deck.back(); eng.step()
    assert eng.deck.page == "main"
    eng.deck.goto("sub"); eng.deck.home(); eng.step()
    assert eng.page_stack == ["main"]
    assert eng.deck.pages == ["main", "sub"]
    with pytest.raises(ValueError, match="no page named 'nope'"):
        eng.deck.goto("nope")


def test_run_actions(write, recorder):
    eng, _, _ = make(write, CONFIG)
    eng.deck.run("rec.a")
    eng.deck.run("rec.b", 3)
    eng.deck.run([{"rec.start": None}, {"page": "sub"}])
    eng.step(); eng.step()                         # page.goto is applied one step later, as from a key
    assert recorder.instances[-1].calls == [("a", None), ("b", 3), ("start", None)]
    assert eng.current_page == "sub"


def test_run_starts_plugins_the_config_does_not_use(write, recorder, monkeypatch):
    class Other(recorder):
        name = "other"
    monkeypatch.setitem(REGISTRY, "other", Other)
    eng, _, _ = make(write, CONFIG)
    assert "other" not in eng.plugins
    eng.deck.run("other.a", 1)
    eng.step()
    started = eng.plugins["other"]
    assert started.calls == [("a", 1)]
    eng._apply(load_config(eng.source.path))     # kept across reloads
    assert eng.plugins["other"] is started


@pytest.mark.parametrize("args, message", [
    (("rec.nope",), "no action 'nope'"),
    (("keys", "ctrl+banana"), "banana"),
    (("nothing.x",), "unknown plugin 'nothing'"),
    (({"page": "nope"},), "no page named"),
])
def test_run_rejects_bad_actions(write, args, message):
    eng, _, _ = make(write, CONFIG)
    with pytest.raises(ValueError, match=message):
        eng.deck.run(*args)


def test_press_runs_a_keys_actions(write, recorder):
    eng, _, _ = make(write, CONFIG)
    eng.deck.press(2)
    eng.deck.press(2, page="main", gesture="long_press")
    eng.step()
    assert recorder.instances[-1].calls == [("a", None), ("b", 2)]
    eng.deck.press(1, page="sub")                  # page.back
    eng.deck.goto("sub"); eng.step()
    eng.deck.press(1); eng.step()
    assert eng.current_page == "main"


@pytest.mark.parametrize("args, kwargs, message", [
    ((5,), {}, r"deck\.keys\[5\] on page 'main' has no on_press actions"),
    ((2,), {"gesture": "double_press"}, "has no on_double_press"),
    ((2,), {"gesture": "wiggle"}, "gesture must be one of"),
    ((2,), {"page": "nope"}, "no page named"),
])
def test_press_rejects_keys_without_actions(write, args, kwargs, message):
    eng, _, _ = make(write, CONFIG)
    with pytest.raises(ValueError, match=message):
        eng.deck.press(*args, **kwargs)


def test_pressed_python_key_sees_its_own_number(write):
    eng, _, _ = make(write, CONFIG)
    eng.plugins["python"].namespace["gate"] = threading.Event()
    eng.plugins["python"].namespace["gate"].set()
    eng.deck.press(1)
    wait_for(lambda: eng.step() or eng.overrides)
    assert eng.overrides == {"main": {("keys", 1): {"label": "hit"}}}


def test_face_reads_what_a_key_shows(write):
    eng, _, _ = make(write, CONFIG)
    assert eng.deck.face(2).label == "Two"
    assert eng.deck.face(3, page="sub").text == "–"   # {vars.count} is unknown yet
    assert eng.deck.face(9) is None
    eng.deck.set_face(2, bg="red")
    eng.deck.set_var("count", 4)
    eng.step()
    assert (eng.deck.face(2).label, eng.deck.face(2).bg) == ("Two", (255, 0, 0))
    assert eng.deck.face(3, page="sub").text == "4"


@pytest.mark.parametrize("rotate", [0, 180])
def test_is_held(write, rotate):
    eng, dev, _ = make(write, CONFIG + f"modules: {{keys: {{rotate: {rotate}}}}}\n")
    idx = layout.index_of("keys", 5, rotate)
    dev.press(idx); eng.step()
    assert eng.deck.is_held(5) and not eng.deck.is_held(1)
    dev.release(idx); eng.step()
    assert not eng.deck.is_held(5)


def test_state_and_vars(write, recorder):
    eng, _, _ = make(write, CONFIG)
    eng.deck.set_var("count", 7)
    recorder.instances[-1].emit("value", 1.5)
    eng.step()
    assert eng.deck.state("vars.count") == 7
    assert eng.deck.state("rec.value") == 1.5
    assert eng.deck.state("rec.on", "unknown") == "unknown"
    assert eng.deck.states["vars.count"] == 7
    eng.deck.goto("sub"); eng.step()
    assert eng.resolve_face(3).text == "7"
    with pytest.raises(ValueError, match="not a valid name"):
        eng.deck.set_var("Bad Name", 1)


def test_images(write, tmp_path):
    eng, dev, _ = make(write, CONFIG)
    red = Image.new("RGB", (36, 36), "red")
    red.save(tmp_path / "red.png")
    buf = io.BytesIO()
    red.save(buf, "PNG")
    sources = {4: red, 5: str(tmp_path / "red.png"), 6: "red.png", 7: buf.getvalue()}  # 6: next to the config
    for key, src in sources.items():
        eng.deck.set_face(key, image=src)
    eng.step()
    for key in sources:
        assert dev.images[key - 1][:3] == RED, key  # covers the key
    eng.deck.set_face(4, image=None, bg="blue")
    eng.deck.set_face(7, image=None)
    eng.step()
    assert dev.images[3][:3] == bytes((0, 0, 255))
    assert not eng.images._memory                  # pictures no face uses are dropped
    with pytest.raises(FileNotFoundError):
        eng.deck.set_face(4, image=str(tmp_path / "missing.png"))
    with pytest.raises(TypeError):
        eng.deck.set_face(4, image=42)


def test_brightness(write):
    eng, dev, _ = make(write, CONFIG)
    eng.deck.set_brightness(30)
    eng.step()
    assert dev.brightness == 30
    with pytest.raises(ValueError):
        eng.deck.set_brightness(150)
    with pytest.raises(ValueError, match="module must be"):
        eng.deck.set_brightness(50, module="knobs")


def test_controls_are_numbered_like_the_config(write):
    eng, _, _ = make(write, CONFIG)
    with pytest.raises(IndexError, match="keys are numbered 1-15, not 16"):
        eng.deck.set_face(16, label="x")
    with pytest.raises(IndexError, match="knobs are numbered 1-3"):
        eng.deck.knobs[0]
    assert [repr(k) for k in eng.deck.knobs] == ["deck.knobs[1]", "deck.knobs[2]", "deck.knobs[3]"]
    assert eng.deck.keys[3] == eng.deck.keys[3] and len(eng.deck.faders) == 5


@pytest.mark.parametrize("args, kwargs, message", [
    ((1,), {"bg": "notacolour"}, "unknown colour"),
    ((1,), {"icon": "microphon"}, "did you mean microphone"),
    ((1,), {"colour": "red"}, "unknown face field"),
    ((1,), {"page": "nope"}, "no page named"),
])
def test_set_face_rejects_bad_arguments(write, args, kwargs, message):
    eng, _, _ = make(write, CONFIG)
    with pytest.raises(ValueError, match=message):
        eng.deck.set_face(*args, **kwargs)


def test_python_action_targets_its_own_key_and_page(write):
    eng, dev, clock = make(write, CONFIG)
    ns = eng.plugins["python"].namespace
    ns["gate"] = threading.Event()
    tap(eng, dev, clock, 0)                        # the code waits for the gate...
    eng.deck.goto("sub"); eng.step()               # ...while the user switches page
    ns["gate"].set()
    wait_for(lambda: eng.step() or eng.overrides)
    assert eng.overrides == {"main": {("keys", 1): {"label": "hit"}}}
