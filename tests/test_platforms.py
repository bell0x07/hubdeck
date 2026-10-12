import os
import socket
import subprocess
import sys
import tempfile
import threading

import pytest

from hubdeck.platforms import Platform, current
from hubdeck.platforms.keys import KEYS, MEDIA, parse_combo
from hubdeck.render import faces


def test_parse_combo_aliases():
    assert parse_combo("Ctrl+Shift+M") == ["ctrl", "shift", "m"]
    assert parse_combo("cmd+option+esc") == ["win", "alt", "esc"]
    assert parse_combo("control + pageup") == ["ctrl", "pgup"]
    with pytest.raises(ValueError):
        parse_combo("ctrl+hyper")


def test_windows_keyboard_maps_every_key():
    wk = pytest.importorskip("hubdeck.platforms.windows.keyboard")   # needs ctypes.wintypes
    assert set(wk.VK) == KEYS
    assert wk.VK["win"] == 0x5B and wk.VK["equals"] == 0xBB and wk.VK["f24"] == 0x87


def test_macos_keyboard_maps_every_key_a_mac_has():
    from hubdeck.platforms.macos.keyboard import KEYCODES, MEDIA as MAC_MEDIA
    assert KEYS - KEYCODES.keys() - MAC_MEDIA.keys() == {"menu", "stop", "f21", "f22", "f23", "f24"}
    assert set(MAC_MEDIA) <= set(MEDIA)


@pytest.fixture
def accent(monkeypatch):
    """Set what the platform reports as its accent colour."""
    plat = current()
    monkeypatch.setattr(faces, "_accent", (-faces.ACCENT_REFRESH_SECONDS, faces.ACCENT_FALLBACK))

    def set_accent(rgb):
        monkeypatch.setattr(plat, "accent_color", lambda: rgb)
        monkeypatch.setattr(faces, "_accent", (-faces.ACCENT_REFRESH_SECONDS, faces.ACCENT_FALLBACK))
    return set_accent


def test_accent_color_follows_platform(accent):
    accent((10, 132, 255))
    assert faces.parse_color("accent") == faces.parse_color(" Accent ") == (10, 132, 255)


def test_accent_color_falls_back_to_hubdeck_green(accent):
    accent(None)
    assert faces.parse_color("accent") == faces.ACCENT_FALLBACK == faces.parse_color("#3cc86e")


def test_default_icon_colour_is_accent():
    from hubdeck.config.schema import Defaults
    assert Defaults().style.fg == "accent"


def test_base_platform_has_no_accent_and_unsupported_audio():
    from hubdeck.platforms.posix import PosixPlatform
    p = PosixPlatform()
    assert isinstance(p, Platform) and p.accent_color() is None
    with pytest.raises(OSError):
        p.audio()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file lock")
def test_single_instance_lock(tmp_path):
    env = {**os.environ, "HUBDECK_HOME": str(tmp_path)}
    code = "from hubdeck import instance; import sys, time; print(instance.acquire(), flush=True); time.sleep(5)"
    first = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == "True"
        second = subprocess.run([sys.executable, "-c", code.replace("time.sleep(5)", "")], env=env,
                                capture_output=True, text=True)
        assert second.stdout.strip() == "False"
    finally:
        first.kill()
        first.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")
def test_unix_socket_ipc(monkeypatch):
    from hubdeck.platforms.posix import PosixPlatform
    with tempfile.TemporaryDirectory(dir="/tmp") as d:   # short path: AF_UNIX paths are limited to ~100 bytes
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(os.path.join(d, "discord-ipc-0"))
        srv.listen(1)

        def echo():
            conn, _ = srv.accept()
            conn.sendall(b"echo:" + conn.recv(16))
            conn.close()
        threading.Thread(target=echo, daemon=True).start()

        plat = PosixPlatform()
        monkeypatch.setattr(plat, "ipc_dirs", lambda: [d])
        conn = plat.ipc_connect("discord-ipc-0")
        conn.write(b"hello")
        for _ in range(100):
            if conn.available():
                break
            threading.Event().wait(0.01)
        assert conn.read(10) == b"echo:hello"
        conn.close()
        srv.close()
        with pytest.raises(OSError):
            plat.ipc_connect("discord-ipc-1")


def test_threaded_plugin_merges_queued_adjusts():
    """A fast spin of a slow target becomes one adjust; other work keeps its order."""
    from hubdeck.plugins.base import _ADJUST, PluginContext, ThreadedPlugin

    class Slow(ThreadedPlugin):
        name = "slow"

    p = Slow(PluginContext("slow", lambda *a: None), {})
    for item in [(_ADJUST, ("vol", "Music", 2)), (_ADJUST, ("vol", "Music", 2)), (_ADJUST, ("vol", "Music", -2)),
                 (_ADJUST, ("vol", "Music", 2)), (_ADJUST, ("vol", None, 5)), ("toggle", None)]:
        p._queue.put(item)
    first = p._next()
    assert p._merge_adjusts(first[1]) == ("vol", "Music", 4)
    second = p._next()
    assert second == (_ADJUST, ("vol", None, 5))
    assert p._merge_adjusts(second[1]) == ("vol", None, 5)
    assert p._next() == ("toggle", None)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS AppleScript")
def test_app_volumes_never_make_the_caller_wait(monkeypatch):
    """App volume calls (~0.2 s, or stuck on a permission prompt) run off the audio thread,
    and a fast turn sends only the latest level."""
    import hubdeck.platforms.macos.audio as mac_audio
    sent, gate = [], threading.Event()

    def fake_osascript(source, timeout=None):
        gate.wait(5)                       # slow app
        sent.append(source)
        return "50" if "get" in source else ""
    monkeypatch.setattr(mac_audio, "osascript", fake_osascript)

    def settle(apps):
        for _ in range(200):
            if apps.idle():
                return
            threading.Event().wait(0.01)
        pytest.fail("worker did not finish")

    apps = mac_audio.AppVolumes()
    assert apps.get("Music") is None       # unknown yet: read in the background, no waiting
    gate.set()
    settle(apps)
    assert apps.get("Music") == 50

    gate.clear()
    apps._known["Music"] = (0, 50.0)       # stale, so the next get queues a read
    apps.get("Music")
    for level in (52, 54, 56, 58):         # a fast turn while the app is still busy
        apps.set("Music", level)
    assert apps.get("Music") == 58         # the wanted level, at once
    gate.set()
    settle(apps)
    sets = [s for s in sent if "set sound volume" in s]
    assert sets[-1].endswith("to 58") and len(sets) <= 2
    assert apps.get("Music") == 58


def test_macos_accent_resolved_again_only_when_the_setting_changes(monkeypatch):
    import hubdeck.platforms.macos as mac
    prefs, resolved = [(1, None)], []
    monkeypatch.setattr(mac, "_accent_prefs", lambda: prefs[0])

    def resolve():
        resolved.append(prefs[0])
        return {(1, None): (247, 130, 27), (5, None): (165, 80, 167), (5, "Dark"): (166, 79, 169)}[prefs[0]]
    monkeypatch.setattr(mac, "_resolve_accent", resolve)
    plat = mac.MacPlatform()
    assert plat.accent_color() == plat.accent_color() == (247, 130, 27)
    prefs[0] = (5, None)                  # purple chosen in System Settings
    assert plat.accent_color() == (165, 80, 167)
    prefs[0] = (5, "Dark")                # dark mode shifts the shade
    assert plat.accent_color() == (166, 79, 169)
    assert resolved == [(1, None), (5, None), (5, "Dark")]


class FakeWinreg:
    """Just enough of winreg for the Run key, so the test runs anywhere."""
    HKEY_CURRENT_USER, KEY_SET_VALUE, REG_SZ = "HKCU", 2, 1

    def __init__(self):
        self.keys = {}

    class Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, sub):
        if (root, sub) not in self.keys:
            raise FileNotFoundError(sub)
        return self.Key(self.keys[(root, sub)])

    def CreateKeyEx(self, root, sub, reserved, access):
        return self.Key(self.keys.setdefault((root, sub), {}))

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


def test_windows_start_at_login_uses_the_run_key(tmp_path, monkeypatch):
    win = pytest.importorskip("hubdeck.platforms.windows")
    reg = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    plat = win.WindowsPlatform()

    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(sys, "argv", ["-m"])
    assert plat.start_at_login() is None              # python -m hubdeck.tray: nothing to start

    exe = tmp_path / "hubdeck" / "hubdeck-tray.exe"   # the installed app: its launcher is sys.executable
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert plat.start_at_login() is False
    plat.set_start_at_login(True)
    assert reg.keys[("HKCU", win.RUN_KEY)] == {"hubdeck": f'"{exe.resolve()}"'}
    assert plat.start_at_login() is True
    plat.set_start_at_login(False)
    assert reg.keys[("HKCU", win.RUN_KEY)] == {} and plat.start_at_login() is False
    plat.set_start_at_login(False)                    # already off: fine

    dev = tmp_path / "venv" / "Scripts" / "hubdeck-tray.exe"   # pip's launcher in a venv: sys.argv[0]
    dev.parent.mkdir(parents=True)
    dev.write_bytes(b"MZ")
    reg.keys[("HKCU", win.RUN_KEY)]["hubdeck"] = f'"{exe.resolve()}"'
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "Scripts" / "pythonw.exe"))
    monkeypatch.setattr(sys, "argv", [str(dev)])
    assert plat.start_at_login() is False             # on, but for the other copy of hubdeck
    plat.set_start_at_login(True)
    assert plat.start_at_login() is True


def test_windows_accent_color_from_the_registry(monkeypatch):
    win = pytest.importorskip("hubdeck.platforms.windows")
    reg = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    plat = win.WindowsPlatform()
    assert plat.accent_color() is None                 # nothing set: hubdeck green

    reg.keys[("HKCU", win.DWM_KEY)] = {"AccentColor": 0xFF008CFF}             # ABGR
    assert plat.accent_color() == (255, 140, 0)

    shades = ["ffd155", "ffb634", "ff9910", "ff8c00", "e37700", "a14600", "651900", "0063b1"]
    reg.keys[("HKCU", win.ACCENT_KEY)] = {"AccentPalette": b"".join(bytes.fromhex(c) + b"\0" for c in shades)}
    reg.keys[("HKCU", win.DWM_KEY)] = {"AccentColor": 0xFFFFFFFF}            # the palette wins
    assert plat.accent_color() == (255, 140, 0)        # its 4th shade: what Settings shows
