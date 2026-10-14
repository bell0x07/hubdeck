import pytest
from PIL import Image

from hubdeck.config import ConfigError, ConfigSource, load_config
from hubdeck.device.fake import FakeControls, FakeKeyModule
from hubdeck.engine import Engine
from hubdeck.render.faces import RenderFace, gradient_at, render
from hubdeck.render.images import ImageStore, is_url, load_image


def make_gif(path, colors=((255, 0, 0), (0, 0, 255), (0, 255, 0)), duration=100):
    frames = [Image.new("RGB", (40, 40), c) for c in colors]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration, loop=0)
    return path


def test_load_gif_frames_and_timing(tmp_path):
    img = load_image(make_gif(tmp_path / "a.gif"))
    assert img.animated and len(img.frames) == 3 and img.durations == [0.1, 0.1, 0.1]
    assert [img.frame_at(t) for t in (0, 0.15, 0.25, 0.31)] == [0, 1, 2, 0]   # loops


def test_static_png(tmp_path):
    Image.new("RGB", (10, 10), "white").save(tmp_path / "b.png")
    img = load_image(tmp_path / "b.png")
    assert not img.animated and img.frame_at(5) == 0


def test_render_image_fills_face():
    pic = Image.new("RGB", (100, 50), (255, 0, 0))
    face = RenderFace(bg=(0, 0, 0), fg=(255, 255, 255), label_color=(255, 255, 255), image="x")
    out = render(face, 72, image=pic)
    assert out.getpixel((36, 36)) == (255, 0, 0) and out.getpixel((1, 1)) == (255, 0, 0)    # cover
    contain = render(RenderFace(bg=(0, 0, 0), fg=(0, 0, 0), label_color=(0, 0, 0), image="x", image_fit="contain"),
                     72, image=pic)
    assert contain.getpixel((36, 2)) == (0, 0, 0) and contain.getpixel((36, 36)) == (255, 0, 0)


def test_relative_image_path_and_missing_file(write, tmp_path):
    make_gif(tmp_path / "anim.gif")
    cfg = load_config(write("config.yaml", "keys: {1: {face: {image: anim.gif, label: Hi}}}"))
    assert cfg.pages["main"].keys[1].face.image == str(tmp_path / "anim.gif")
    with pytest.raises(ConfigError, match="not found"):
        load_config(write("config.yaml", "keys: {1: {face: {image: nope.png}}}"))


def test_urls_are_not_checked_on_disk(write):
    cfg = load_config(write("config.yaml", "keys: {1: {face: {image: 'https://example.com/cat.gif'}}}"))
    assert is_url(cfg.pages["main"].keys[1].face.image)


def test_url_cache_path(tmp_path):
    store = ImageStore(tmp_path)
    p = store.cache_path("https://example.com/a/cat.GIF?x=1")
    assert p.parent == tmp_path and p.suffix == ".gif"
    assert store.cache_path("https://example.com/noext").suffix == ".img"


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_engine_plays_gif_frames(write, tmp_path):
    make_gif(tmp_path / "anim.gif")
    path = write("config.yaml", "device: {reassert_seconds: 0}\nkeys: {1: {face: {image: anim.gif}}}\n"
                                "dial: {face: {image: anim.gif}}\n")
    keys, dial = FakeKeyModule(), FakeControls("dial")
    clock = Clock()
    eng = Engine(ConfigSource(path), factories={"keys": lambda: keys, "dial": lambda: dial}, clock=clock)
    eng.load_initial()
    eng.step()
    first_key, first_dial = keys.images[0], dial.images[0]
    clock.t = 0.15; eng.step()
    assert keys.images[0] != first_key and dial.images[0] != first_dial     # next frame shown
    writes = len(keys.writes)
    clock.t = 0.16; eng.step()
    assert len(keys.writes) == writes                                        # same frame: nothing sent
    clock.t = 0.31; eng.step()
    assert keys.images[0] == first_key                                       # looped


def test_gradient_at_blends_evenly():
    colors = ((0, 0, 0), (100, 200, 0), (200, 0, 0))
    assert [gradient_at(colors, x) for x in (0, 0.25, 0.5, 1, 2)] == [(0, 0, 0), (50, 100, 0), (100, 200, 0),
                                                                     (200, 0, 0), (200, 0, 0)]
    assert gradient_at(((9, 9, 9),), 0.7) == (9, 9, 9)


def test_fill_rises_from_the_bottom():
    face = RenderFace(bg=(0, 0, 0), fg=(0, 0, 0), label_color=(0, 0, 0), fill=0.5,
                      fill_colors=((0, 255, 0), (255, 0, 0)))
    img = render(face)
    assert img.getpixel((36, 10)) == (0, 0, 0)                       # above the fill: background
    bottom, middle = img.getpixel((36, 70)), img.getpixel((36, 38))
    assert bottom[1] > 240 and bottom[0] < 15                        # the gradient's bottom colour
    assert 100 < middle[0] < 155 and 100 < middle[1] < 155           # halfway through it
