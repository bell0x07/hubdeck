import os
import socket
import subprocess
import sys
import tempfile
import threading

import pytest

from hubdeck.platforms import Platform, Unsupported, current
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
    with pytest.raises(Unsupported):
        p.focus()


def test_focus_watcher_keeps_reading_after_a_failure():
    from hubdeck.focus import FocusWatcher
    from hubdeck.platforms import FocusedWindow, FocusReader

    class Reader(FocusReader):
        reads = 0
        done = threading.Event()

        def read(self):
            self.reads += 1
            if self.reads == 1:
                raise OSError("no")
            self.done.set()
            return FocusedWindow("x.exe", "t")

    reader = Reader()
    watcher = FocusWatcher(reader, interval=0.001)
    watcher.start()
    assert reader.done.wait(5)
    watcher.stop()
    assert watcher.current == FocusedWindow("x.exe", "t")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows foreground window")
def test_windows_focus_reads_the_foreground_window(monkeypatch):
    from hubdeck.platforms.windows import focus

    reader = current().focus()
    if not reader.user32.GetForegroundWindow():
        pytest.skip("no foreground window (locked or no desktop)")
    window = reader.read()                    # whatever has focus while the tests run
    assert window is None or (window.app and isinstance(window.title, str))
    monkeypatch.setattr(reader, "own_pid", reader._pid(reader.user32.GetForegroundWindow()))
    assert reader.read() is None              # hubdeck's own windows don't count
    monkeypatch.setattr(reader, "own_pid", -1)
    monkeypatch.setattr(focus.psutil, "Process", lambda pid: type("P", (), {"name": lambda self: "explorer.exe"})())
    monkeypatch.setattr(reader, "_class", lambda hwnd: "Shell_TrayWnd")
    assert reader.read() is None              # the taskbar
    monkeypatch.setattr(reader, "_class", lambda hwnd: "CabinetWClass")
    assert reader.read().app == "explorer.exe"   # File Explorer


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
    for item in [(_ADJUST, ("vol", "Music", 2, 0)), (_ADJUST, ("vol", "Music", 2, 0)),
                 (_ADJUST, ("vol", "Music", -2, 0)), (_ADJUST, ("vol", "Music", 2, 0)),
                 (_ADJUST, ("vol", None, 5, 0)), ("toggle", None)]:
        p._queue.put(item)
    first = p._next()
    assert p._merge_adjusts(first[1]) == ("vol", "Music", 4, 0)
    second = p._next()
    assert second == (_ADJUST, ("vol", None, 5, 0))
    assert p._merge_adjusts(second[1]) == ("vol", None, 5, 0)
    assert p._next() == ("toggle", None)


def test_threaded_plugin_merges_adjusts_only_on_the_same_curve():
    from hubdeck.plugins.base import _ADJUST, PluginContext, ThreadedPlugin

    class Slow(ThreadedPlugin):
        name = "slow"

    p = Slow(PluginContext("slow", lambda *a: None), {})
    for item in [(_ADJUST, ("vol", None, 2, 0.5)), (_ADJUST, ("vol", None, 2, 0.5)), (_ADJUST, ("vol", None, 2, 0))]:
        p._queue.put(item)
    assert p._merge_adjusts(p._next()[1]) == ("vol", None, 4, 0.5)
    assert p._merge_adjusts(p._next()[1]) == ("vol", None, 2, 0)


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex")
def test_windows_single_instance_frees_the_other_hubdecks_mutex(monkeypatch):
    """A copy that found hubdeck running mustn't keep its mutex alive: `hubdeck-tray --replace`
    waits for it to go away. (A test name, so a hubdeck running on this PC doesn't count.)"""
    import ctypes
    win = pytest.importorskip("hubdeck.platforms.windows")
    monkeypatch.setattr(win, "MUTEX_NAME", rf"Local\hubdeck-test-{os.getpid()}")
    first, second = win.WindowsPlatform(), win.WindowsPlatform()
    assert first.acquire_single_instance() is True
    assert second.acquire_single_instance() is False
    assert second.acquire_single_instance() is False
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(first._mutex))   # the first hubdeck quits
    assert second.acquire_single_instance() is True
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(second._mutex))


def test_audio_set_output_and_set_input_switch_the_default_devices(monkeypatch):
    from hubdeck.platforms import AudioBackend
    from hubdeck.plugins import audio
    from hubdeck.plugins.base import PluginContext

    class Backend(AudioBackend):
        def __init__(self):
            self.names = {"speakers": ["Speakers (NuForce uDAC5)", "Speakers (JBL Quantum 800 Game)"],
                          "microphone": ["Microphone (Scarlett 2i2 USB)", "Headset Microphone (JBL Quantum 800 Chat)"]}
            self.default = {kind: names[0] for kind, names in self.names.items()}

        def get_volume(self, kind): return 50.0
        def set_volume(self, kind, level): pass
        def get_mute(self, kind): return False
        def set_mute(self, kind, mute): pass
        def default_name(self, kind): return self.default[kind]
        def devices(self, kind): return self.names[kind]

        def set_default(self, kind, want):
            for name in self.names[kind]:
                if want.lower() in name.lower():
                    self.default[kind] = name
                    return name
            return None

    monkeypatch.setattr(audio, "current", lambda: type("P", (), {"audio": lambda self: Backend()})())
    states = {}
    plugin = audio.AudioPlugin(PluginContext("audio", lambda p, k, v: states.__setitem__(k, v)), {})
    plugin.setup()
    plugin.handle("set_input", "jbl quantum 800 chat")                # any case, part of the name
    assert states["input"] == "Headset Microphone (JBL Quantum 800 Chat)"
    assert states["output"] == "Speakers (NuForce uDAC5)"             # the output stays
    plugin.handle("set_output", "Quantum 800 Game")
    assert states["output"] == "Speakers (JBL Quantum 800 Game)"
    assert states["input"] == "Headset Microphone (JBL Quantum 800 Chat)"
    plugin.handle("set_input", "Yeti")                                # no such device: nothing changes
    assert states["input"] == "Headset Microphone (JBL Quantum 800 Chat)"
    assert audio.AudioPlugin.check_action("set_input", "Scarlett") is None
    assert audio.AudioPlugin.check_action("set_input", None) == "audio.set_input needs (part of) a device name"


def test_audio_app_volume_follows_the_focused_app(monkeypatch):
    from types import SimpleNamespace
    from hubdeck.platforms import AudioBackend
    from hubdeck.plugins import audio
    from hubdeck.plugins.base import PluginContext, value_state

    class Backend(AudioBackend):
        def __init__(self):
            self.levels = {"Discord.exe": 50.0, "game.exe": 80.0}
            self.muted = {"Discord.exe": False, "game.exe": False}

        def get_volume(self, kind): return 50.0
        def set_volume(self, kind, level): pass
        def get_mute(self, kind): return False
        def set_mute(self, kind, mute): pass
        def default_name(self, kind): return "Speakers"
        def devices(self, kind): return ["Speakers"]
        def set_default(self, kind, want): return None
        def get_app_volume(self, app): return self.levels.get(app)
        def set_app_volume(self, app, level): self.levels[app] = level
        def get_app_mute(self, app): return self.muted.get(app) if app in self.levels else None
        def set_app_mute(self, app, mute): self.muted[app] = mute

    monkeypatch.setattr(audio, "current", lambda: type("P", (), {"audio": lambda self: Backend()})())
    states, deck = {}, SimpleNamespace(focused=None)
    plugin = audio.AudioPlugin(PluginContext("audio", lambda p, k, v: states.__setitem__(k, v), deck), {})
    plugin.setup()
    plugin.watch({("app_volume", "focused")})
    key = value_state("app_volume", "focused")
    plugin.tick()
    assert states[key] is None                                     # no window seen yet
    plugin.adjust_value("app_volume", "focused", 5, 0)             # nothing to change

    deck.focused = SimpleNamespace(app="Discord.exe", title="general")
    plugin.adjust_value("app_volume", "focused", 5, 0)
    assert plugin.backend.levels["Discord.exe"] == 55 and states[key] == 55
    deck.focused = SimpleNamespace(app="game.exe", title="")
    plugin.tick()
    assert states[key] == 80                                       # follows focus
    plugin.adjust_value("app_volume", "focused", -10, 0)
    assert plugin.backend.levels == {"Discord.exe": 55, "game.exe": 70}
    assert audio.AudioPlugin.check_value("app_volume", "focused") is None

    plugin.handle("toggle_mute", {"app": "focused"})               # the focused app: the game
    plugin.handle("toggle_mute", {"app": "Discord.exe"})
    assert plugin.backend.muted == {"Discord.exe": True, "game.exe": True}
    plugin.handle("toggle_mute", {"app": "focused"})
    plugin.handle("mute", {"app": "Discord.exe"})                  # already muted: stays
    assert plugin.backend.muted == {"Discord.exe": True, "game.exe": False}
    plugin.handle("toggle_mute", {"app": "nothing.exe"})           # no audio: nothing to do
    assert audio.AudioPlugin.check_action("toggle_mute", {"app": "focused"}) is None
    assert "process name" in audio.AudioPlugin.check_action("mute", {"app": ""})
    assert "{app: <process name>}" in audio.AudioPlugin.check_action("mute", "headphones")


def _media_plugin(monkeypatch, reader):
    from hubdeck.plugins import media
    from hubdeck.plugins.base import PluginContext

    def make():
        if isinstance(reader, Exception):
            raise reader
        return reader

    monkeypatch.setattr(media, "current", lambda: type("P", (), {"media": lambda self: make()})())
    emitted = []
    plugin = media.MediaPlugin(PluginContext("media", lambda p, k, v: emitted.append((k, v))), {})
    plugin.setup()
    return plugin, emitted


def test_media_plugin_publishes_what_changed(monkeypatch):
    from hubdeck.platforms import MediaReader, NowPlaying

    class Reader(MediaReader):
        now: object = NowPlaying("Spotify", True, "Sundust", "Rodg")

        def read(self):
            if isinstance(self.now, Exception):
                raise self.now
            return self.now

    reader = Reader()
    plugin, emitted = _media_plugin(monkeypatch, reader)
    plugin.tick()
    assert emitted == [("playing", True), ("app", "Spotify"), ("title", "Sundust"), ("artist", "Rodg")]
    emitted.clear()
    plugin.tick()
    assert emitted == []                                      # nothing new, nothing sent
    reader.now = NowPlaying("Spotify", False, "Sundust", "Rodg")
    plugin.tick()
    assert emitted == [("playing", False)]                    # paused
    emitted.clear()
    reader.now = OSError("session went away")
    plugin.tick()
    assert emitted == []                                      # a failed read keeps what's shown
    reader.now = None
    plugin.tick()
    assert emitted == [(f, None) for f in ("playing", "app", "title", "artist")]   # no app has media open


def test_media_plugin_without_support_publishes_nothing(monkeypatch):
    plugin, emitted = _media_plugin(monkeypatch, Unsupported("not here"))
    plugin.tick()
    plugin.teardown()
    assert emitted == []


def test_media_app_name_from_the_start_menu_or_the_process():
    from hubdeck.platforms.windows.media import app_name

    start_menu = {"Brave": "Brave", "MSEdge": "Microsoft Edge"}.get
    assert app_name("MSEdge", start_menu) == "Microsoft Edge"
    assert app_name("Spotify.exe", start_menu) == "Spotify"   # no Start menu id: the process
    assert app_name("308046B0AF4A39CB", start_menu) == "308046B0AF4A39CB"


def test_windows_media_keeps_the_icon_between_tracks():
    """CHANGING (skipping to the next track) reads as the app's last state, so a playing
    key doesn't blink to paused on every skip."""
    from types import SimpleNamespace
    control = pytest.importorskip("winrt.windows.media.control")
    from hubdeck.platforms import NowPlaying
    from hubdeck.platforms.windows.media import WinMedia
    status = control.GlobalSystemMediaTransportControlsSessionPlaybackStatus

    session = SimpleNamespace(source_app_user_model_id="Spotify.exe", status=None)
    session.get_playback_info = lambda: SimpleNamespace(playback_status=session.status)
    session.try_get_media_properties_async = lambda: SimpleNamespace(
        get=lambda: SimpleNamespace(title="Sundust", artist=""))
    reader = WinMedia.__new__(WinMedia)                       # no Windows session manager
    reader.manager = SimpleNamespace(get_current_session=lambda: session)
    reader.names, reader.last = {"Spotify.exe": "Spotify"}, None

    def read(s):
        session.status = s
        return reader.read()

    assert read(status.PLAYING) == NowPlaying("Spotify", True, "Sundust", None)   # no artist: unknown
    assert read(status.CHANGING).playing is True
    assert read(status.PAUSED).playing is False
    assert read(status.CHANGING).playing is False
    assert read(status.CLOSED) is None
    reader.manager = SimpleNamespace(get_current_session=lambda: None)
    assert reader.read() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows media sessions")
def test_windows_media_reads_the_current_session():
    pytest.importorskip("winrt.windows.media.control")
    result = []

    def read():                                               # a thread of its own, like the plugin's
        reader = current().media()
        try:
            result.append(reader.read())
        finally:
            reader.close()

    t = threading.Thread(target=read)
    t.start()
    t.join(10)
    now, = result                                             # whatever plays while the tests run
    assert now is None or (now.app and isinstance(now.playing, bool))


def test_windows_app_volume_includes_processes_the_app_started():
    """New Teams plays through a msedgewebview2.exe it starts. Windows' own processes
    (Explorer) don't count as apps, and each process is looked up once."""
    wa = pytest.importorskip("hubdeck.platforms.windows.audio")
    from types import SimpleNamespace
    lookups = []

    def proc(pid, name, folder, *parents):
        def walk():
            lookups.append(name)
            return list(parents)                              # nearest first, like psutil
        return SimpleNamespace(pid=pid, create_time=lambda: 1.0, name=lambda: name,
                               exe=lambda: os.path.join(folder, name), parents=walk)

    class Session:
        def __init__(self, process, level):
            self.Process, self.level, self.muted = process, level, 0
            self.SimpleAudioVolume = SimpleNamespace(GetMasterVolume=lambda: self.level,
                                                     SetMasterVolume=lambda v, _ctx: setattr(self, "level", v),
                                                     GetMute=lambda: self.muted,
                                                     SetMute=lambda m, _ctx: setattr(self, "muted", m))

    programs, explorer = r"C:\Program Files", proc(1, "explorer.exe", wa.WINDOWS_DIR)
    teams = proc(2, "ms-teams.exe", programs, explorer)
    webview, spotify = Session(proc(3, "msedgewebview2.exe", programs, teams, explorer), 0.5), \
        Session(proc(4, "Spotify.exe", programs, explorer), 0.9)
    audio = wa.WinAudio.__new__(wa.WinAudio)                  # no COM
    audio.au, audio._owners = SimpleNamespace(GetAllSessions=lambda: [webview, spotify, Session(None, 1)]), {}

    assert audio.get_app_volume("ms-teams.exe") == 50
    assert audio.get_app_volume("msedgewebview2") == 50       # still by its own name
    assert audio.get_app_volume("explorer.exe") is None
    audio.set_app_volume("MS-Teams", 30)
    assert (webview.level, spotify.level) == (0.3, 0.9)
    assert audio.get_app_mute("ms-teams.exe") is False and audio.get_app_mute("explorer.exe") is None
    audio.set_app_mute("ms-teams.exe", True)
    assert audio.get_app_mute("ms-teams.exe") is True and audio.get_app_mute("spotify") is False
    assert audio.apps() == ["ms-teams.exe", "msedgewebview2.exe", "Spotify.exe"]
    assert sorted(lookups) == ["Spotify.exe", "msedgewebview2.exe"]
