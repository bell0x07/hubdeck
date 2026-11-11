"""The 15-key module as one screen: geometry, pictures, slideshows, charts, big text."""

import pytest
from PIL import Image

from hubdeck.config import ConfigError, ConfigSource
from hubdeck.config.loader import load_config
from hubdeck.device import layout
from hubdeck.device.fake import FakeKeyModule
from hubdeck.engine import Engine
from hubdeck.render import display as kd

KEY, GAP = 72, 22


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def cells(cols, rows, gap=GAP):
    """A canvas whose visible key areas each have their own colour, and red bezels."""
    canvas = Image.new("RGB", kd.canvas_size(cols, rows, gap), (255, 0, 0))
    for n in range(cols * rows):
        r, c = divmod(n, cols)
        x, y = c * (KEY + gap), r * (KEY + gap)
        canvas.paste((n * 10, 100, 200), (x, y, x + KEY, y + KEY))
    return canvas


def solid(n):
    return Image.new("RGB", (KEY, KEY), (n * 10, 100, 200)).tobytes()


@pytest.fixture
def engine(write, monkeypatch, tmp_path, recorder):
    monkeypatch.setenv("HUBDECK_HOME", str(tmp_path / "home"))

    def make(config: str):
        keys, clock = FakeKeyModule(), Clock()
        eng = Engine(ConfigSource(write("config.yaml", config)), factories={"keys": lambda: keys}, clock=clock)
        eng.load_initial()
        eng.step()
        return eng, keys, clock
    return make


def test_canvas_and_tiles_skip_the_bezels():
    assert kd.grid(0) == (5, 3) and kd.grid(90) == (3, 5)
    assert kd.canvas_size(5, 3, GAP) == (448, 260) and kd.canvas_size(3, 5, GAP) == (260, 448)
    tiles = kd.tiles(cells(5, 3), 5, 3, GAP)
    assert len(tiles) == 15
    for n, tile in enumerate(tiles):
        assert tile.size == (KEY, KEY) and tile.tobytes() == solid(n)      # no bezel pixel on any key


def test_picture_across_the_keys(engine, tmp_path):
    cells(5, 3).save(tmp_path / "picture.png")
    eng, keys, _ = engine("""
        pages:
          main:
            display: {image: picture.png}
            keys: {15: {action: rec.a}}
        """)
    assert sorted(keys.images) == list(range(15))
    for n in range(15):
        assert keys.images[n] == solid(n)
    keys.press(14)                                       # keys still do things on a display page
    keys.release(14)
    eng.step()
    assert ("a", None) in eng.plugins["rec"].calls


def test_picture_on_a_module_mounted_sideways(engine, tmp_path):
    picture = Image.linear_gradient("L").resize(kd.canvas_size(3, 5, GAP)).convert("RGB")
    picture.save(tmp_path / "picture.png")
    _, keys, _ = engine("""
        modules: {keys: {rotate: 90}}
        pages:
          main:
            display: {image: picture.png}
        """)
    tiles = kd.tiles(picture, 3, 5, GAP)
    turn = layout.image_rotation(90)
    for number, tile in enumerate(tiles, 1):              # reading order as mounted, turned like faces are
        assert keys.images[layout.index_of("keys", number, 90)] == tile.rotate(-turn).tobytes()


def test_slideshow_shows_the_next_picture_on_time(engine, tmp_path):
    (tmp_path / "memes").mkdir()
    Image.new("RGB", (448, 260), (200, 0, 0)).save(tmp_path / "memes" / "a.png")
    Image.new("RGB", (448, 260), (0, 0, 200)).save(tmp_path / "memes" / "b.jpg")
    (tmp_path / "memes" / "notes.txt").write_text("not a picture")
    eng, keys, clock = engine("""
        pages:
          main:
            display: {image: memes, every: 10}
        """)
    assert keys.images[7] == Image.new("RGB", (KEY, KEY), (200, 0, 0)).tobytes()
    assert eng._display_wake == 110
    clock.t = 110
    eng.step()
    r, g, b = keys.images[7][:3]                          # b.jpg: blue, give or take JPEG
    assert r < 30 and g < 30 and b > 170


def test_chart_records_the_values_it_shows(engine):
    eng, keys, clock = engine("""
        pages:
          main:
            display: {chart: [rec.value, rec.on], history: 30, label: "{rec.value}%"}
        """)
    rec = eng.plugins["rec"]
    for value in (10, 50, "not a number", 90):
        rec.emit("value", value)
        rec.emit("on", True)
        clock.t += 1
        eng.step()
    values = [v for _, v in eng._history["rec.value"]]
    assert values[-4:] == [10.0, 50.0, None, 90.0]                # text breaks the line
    assert set(v for _, v in eng._history["rec.on"]) <= {None}     # booleans aren't plotted
    assert eng._history["rec.value"].maxlen >= 30
    assert sorted(keys.images) == list(range(15))
    before = dict(keys.images)
    rec.emit("value", 100)
    clock.t += 1
    eng.step()
    assert keys.images != before                                    # the newest point moved the line


def test_text_puts_a_letter_on_each_key():
    assert kd.letter_cells("19:53", 5, 3) == {6: "1", 7: "9", 8: ":", 9: "5", 10: "3"}   # middle row
    assert kd.letter_cells("CPU\n37%", 5, 3) == {2: "C", 3: "P", 4: "U", 7: "3", 8: "7", 9: "%"}
    assert kd.letter_cells("ABCDEFG", 5, 3) == {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 7: "F", 8: "G"}
    assert kd.letter_cells("12:34", 3, 5) == {4: "1", 5: "2", 6: ":", 7: "3", 8: "4"}    # mounted sideways
    assert len(kd.letter_cells("x" * 40, 5, 3)) == 15                                     # the rest is cut


def test_text_display_with_templates(engine):
    eng, keys, clock = engine("""
        pages:
          main:
            display: {text: "{rec.value}%", fg: white, bg: black}
        """)
    eng.plugins["rec"].emit("value", 42)
    eng.step()
    black = Image.new("RGB", (KEY, KEY), (0, 0, 0)).tobytes()
    lit = {n for n, data in keys.images.items() if data != black}
    assert lit == {6, 7, 8}                         # "42%" on keys 7, 8, 9 (module indexes 6-8)


def test_a_display_page_claims_every_key(write):
    cfg = load_config(write("config.yaml", """
        pages:
          main: {keys: {1: {action: rec.a}}}
          show: {display: {text: hi}}
        """))
    assert cfg.owned_keys() == set(range(1, 16)) and "keys" in cfg.used_modules()
    assert cfg.modules.keys.gap == 22


@pytest.mark.parametrize("display, problem", [
    ("{image: missing.png}", "image 'missing.png' not found"),
    ("{image: empty}", "no pictures in the folder 'empty'"),
    ("{chart: rec.nope}", "rec has no state 'nope'"),
    ("{text: '{rec.nope}'}", "rec has no state 'nope'"),
    ("{text: hi, label: there}", "a text display has no label"),
])
def test_display_problems_are_reported(write, tmp_path, display, problem):
    (tmp_path / "empty").mkdir()
    path = write("config.yaml", f"""
        pages:
          main:
            display: {display}
        """)
    with pytest.raises(ConfigError, match=problem):
        load_config(path)


def test_editor_preview_of_a_display_page(write, tmp_path, recorder, monkeypatch):
    """The editor shows each key's part of the display, not the (unused) key face."""
    import io
    from hubdeck.gui.server import GuiServer
    monkeypatch.setenv("HUBDECK_HOME", str(tmp_path / "home"))
    cells(5, 3).save(tmp_path / "picture.png")
    path = write("config.yaml", """
        pages:
          main:
            display: {image: picture.png}
            keys: {15: {action: rec.a, face: {icon: star}}}
        """)
    s = GuiServer(ConfigSource(path), port=0)
    try:
        png = Image.open(io.BytesIO(s.face_png("main", "keys", 15)))
        assert png.size == (144, 144)
        assert png.convert("RGB").getpixel((72, 72)) == (140, 100, 200)    # key 15's colour, no star
    finally:
        s.httpd.server_close()
