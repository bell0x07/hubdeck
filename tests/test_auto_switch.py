import pytest

from hubdeck.config import ConfigError, ConfigSource, load_config
from hubdeck.device.fake import FakeKeyModule
from hubdeck.engine import FOCUS_SETTLE, Engine
from hubdeck.platforms import FocusedWindow

from test_engine import Clock, rec

CONFIG = """
device: {ownership: shared, reassert_seconds: 0}
auto_switch:
  ignore:
    - KeePassXC
    - {app: X2.exe, title: '^Preferences'}
  on_focus: {rec.a: focus}
pages:
  main:
    keys:
      1: {action: {page: media}, face: {label: Media}}
      2: {action: {page: call}, face: {label: Call}}
      3: {action: page.auto_toggle, state: page.auto, face: {label: auto}, face_on: {label: AUTO}}
      4: {face: {text: "{page.app}"}}
  media:
    keys:
      1: {action: page.back, face: {label: Back}}
  altium:
    match: X2.exe
    keys:
      1: {action: {page: placement}, face: {label: Place}}
  altium-pcb:
    match: [{app: X2.exe, title: '\\.PcbDoc'}]
    keys:
      1: {action: page.back, face: {label: PCB}}
  placement:
    keys:
      1: {action: page.back, face: {label: Back}}
  youtube:
    match: [{title: youtube}]
    keys:
      1: {action: page.home, face: {label: Home}}
  call:
    sticky: true
    keys:
      1: {action: page.back, face: {label: Back}}
"""


class FakeFocus:
    current = None


def make(write, text=CONFIG):
    path = write("config.yaml", text)
    clock = Clock()
    focus = FakeFocus()
    eng = Engine(ConfigSource(path), device_factory=FakeKeyModule, clock=clock, focus=focus)
    eng.load_initial()
    eng.step()
    return eng, focus, clock


def show(eng, focus, clock, app, title="", settle=FOCUS_SETTLE + 0.01):
    """Focus a window (app None: one hubdeck ignores, like the taskbar) for `settle` seconds."""
    focus.current = None if app is None else FocusedWindow(app, title)
    eng.step()
    clock.t += settle
    eng.step()


def run(eng, *actions):
    from hubdeck.config.schema import parse_actions
    for a in parse_actions(list(actions)):
        eng.run_action(a)
    eng.step()
    eng.step()          # page.goto queues once more


# -- config ----------------------------------------------------------------------
def test_match_forms_and_errors(write):
    cfg = load_config(write("config.yaml", CONFIG))
    assert [m.app for m in cfg.pages["altium"].match] == ["X2.exe"]
    assert cfg.pages["youtube"].match[0].title == "youtube"
    assert cfg.pages["call"].sticky
    for bad, msg in [("match: [{}]", "needs an 'app', a 'title' or both"),
                     ("match: [{title: '('}]", "not a valid regular expression")]:
        with pytest.raises(ConfigError, match=msg):
            load_config(write("bad.yaml", f"pages:\n  main:\n    {bad}\n"))


def test_on_focus_actions_are_checked(write):
    with pytest.raises(ConfigError, match="auto_switch.on_focus: no page named 'nope'"):
        load_config(write("bad.yaml", "auto_switch: {on_focus: {page: nope}}\nkeys: {1: {face: {label: x}}}\n"))
    with pytest.raises(ConfigError, match="rec has no action 'zzz'"):
        load_config(write("bad.yaml", "auto_switch: {on_focus: rec.zzz}\nkeys: {1: {face: {label: x}}}\n"))


@pytest.mark.parametrize("window, page", [
    (FocusedWindow("X2.EXE", "board.PcbDoc - Altium"), "altium-pcb"),      # app + title beats app
    (FocusedWindow("x2", "sheet.SchDoc"), "altium"),                        # any case, .exe optional
    (FocusedWindow("X2.exe", "YouTube - x.PcbDoc"), "altium-pcb"),         # app + title beats title
    (FocusedWindow("chrome.exe", "Cats - YouTube"), "youtube"),
    (FocusedWindow("Safari", "YouTube", ("com.apple.Safari",)), "youtube"),
    (FocusedWindow("notepad.exe", "YouTube notes"), "youtube"),
    (FocusedWindow("X2.exe", None), "altium"),                              # title unreadable
    (FocusedWindow("notepad.exe", "x"), None),
])
def test_most_specific_match_wins(write, window, page):
    assert load_config(write("config.yaml", CONFIG)).match_page(window) == page


def test_app_aliases_and_file_order(write):
    cfg = load_config(write("config.yaml", """
        pages:
          main: {match: com.microsoft.VSCode}
          other: {match: [Code.app, code]}
        """))
    assert cfg.match_page(FocusedWindow("Code", "", ("com.microsoft.VSCode",))) == "main"
    assert cfg.match_page(FocusedWindow("Code.exe", "")) == "other"


# -- switching -------------------------------------------------------------------
def test_switches_once_the_window_keeps_focus(write):
    eng, focus, clock = make(write)
    show(eng, focus, clock, "X2.exe", "a.SchDoc", settle=FOCUS_SETTLE - 0.01)
    assert eng.page_stack == ["main"]          # Alt+Tab passing by
    clock.t += 0.02
    eng.step()
    assert eng.page_stack == ["altium"]
    assert eng.state["page.app"] == "X2.exe" and eng.state["page.title"] == "a.SchDoc"
    show(eng, focus, clock, "X2.exe", "b.PcbDoc")
    assert eng.page_stack == ["altium-pcb"]


def test_app_without_a_page_puts_back_your_stack(write):
    eng, focus, clock = make(write)
    run(eng, {"page": "media"})
    assert eng.page_stack == ["main", "media"]
    show(eng, focus, clock, "X2.exe")
    assert eng.page_stack == ["altium"]
    run(eng, {"page": "placement"})             # belongs to Altium
    assert eng.page_stack == ["altium", "placement"]
    show(eng, focus, clock, "chrome.exe", "Cats - YouTube")
    assert eng.page_stack == ["youtube"]
    show(eng, focus, clock, "notepad.exe")
    assert eng.page_stack == ["main", "media"]
    run(eng, "page.back")
    show(eng, focus, clock, "explorer.exe")
    assert eng.page_stack == ["main"]           # a stack you set stays


def test_ignored_windows_count_as_if_focus_never_moved(write, recorder):
    eng, focus, clock = make(write)
    show(eng, focus, clock, "X2.exe")
    calls = len(rec(recorder).calls)
    for app, title in [(None, ""), ("KeePassXC", "auto-type"), ("X2.exe", "Preferences")]:
        show(eng, focus, clock, app, title)
        assert eng.page_stack == ["altium"]
    show(eng, focus, clock, "X2.exe")           # back where it was: nothing changed
    assert len(rec(recorder).calls) == calls
    assert [(w.app, w.title) for w in eng.recent_windows] == [
        ("X2.exe", ""), ("X2.exe", "Preferences"), ("KeePassXC", "auto-type")]


def test_title_change_to_the_same_page_keeps_the_page_you_picked(write):
    eng, focus, clock = make(write)
    show(eng, focus, clock, "X2.exe", "a.SchDoc")
    run(eng, {"page": "placement"})
    show(eng, focus, clock, "X2.exe", "b.SchDoc")
    assert eng.page_stack == ["altium", "placement"]
    show(eng, focus, clock, "X2.exe", "b.PcbDoc")
    assert eng.page_stack == ["altium-pcb"]


def test_sticky_page_stays_and_does_not_catch_up(write):
    eng, focus, clock = make(write)
    run(eng, {"page": "call"})
    show(eng, focus, clock, "X2.exe")
    assert eng.page_stack == ["main", "call"]
    run(eng, "page.back")
    assert eng.page_stack == ["main"]           # leaving it: no catch-up
    show(eng, focus, clock, "X2.exe", "x.PcbDoc")
    assert eng.page_stack == ["altium-pcb"]


def test_home_makes_the_stack_yours(write):
    eng, focus, clock = make(write)
    show(eng, focus, clock, "X2.exe")
    run(eng, "page.home")
    show(eng, focus, clock, "notepad.exe")
    assert eng.page_stack == ["main"]
    show(eng, focus, clock, "X2.exe")
    assert eng.page_stack == ["altium"]


def test_turning_it_off_and_on(write):
    eng, focus, clock = make(write)
    assert eng.state["page.auto"] is True and eng.auto_switch
    run(eng, "page.auto_off")
    show(eng, focus, clock, "X2.exe")
    assert eng.page_stack == ["main"] and eng.state["page.auto"] is False
    assert eng.resolve_face(3).label == "auto"
    run(eng, "page.auto_toggle")                # on again: follows the focus right away
    assert eng.page_stack == ["altium"] and eng.auto_switch
    eng.deck.auto_switch = False
    eng.step()
    assert not eng.auto_switch
    with pytest.raises(TypeError):
        eng.deck.auto_switch = "yes"


def test_runtime_setting_lasts_until_the_config_changes_it(write):
    eng, focus, clock = make(write)
    run(eng, "page.auto_off")
    eng.source.path.write_text(CONFIG.replace("Media}}", "Media2}}"), encoding="utf-8")
    eng._try_reload()
    assert not eng.auto_switch
    eng.source.path.write_text(CONFIG.replace("auto_switch:\n", "auto_switch:\n  enabled: false\n"),
                               encoding="utf-8")
    eng._try_reload()
    assert not eng.auto_switch and eng._auto is None
    eng.source.path.write_text(CONFIG, encoding="utf-8")
    eng._try_reload()
    assert eng.auto_switch and eng.state["page.auto"] is True


def test_on_focus_runs_on_every_change_even_while_off(write, recorder):
    eng, focus, clock = make(write)
    show(eng, focus, clock, "X2.exe", "a")
    show(eng, focus, clock, "X2.exe", "b")
    run(eng, "page.auto_off")
    show(eng, focus, clock, "notepad.exe")
    assert rec(recorder).calls == [("a", "focus")] * 3


def test_states_and_deck(write):
    eng, focus, clock = make(write)
    assert eng.deck.focused is None and eng.state.get("page.app") is None
    show(eng, focus, clock, "X2.exe", "board")
    assert eng.deck.focused == ("X2.exe", "board") and eng.deck.focused.app == "X2.exe"
    assert eng.resolve_face(4, "main").text == "X2.exe"


def test_reload_keeps_the_page_and_drops_a_removed_app_page(write):
    eng, focus, clock = make(write)
    run(eng, {"page": "media"})
    show(eng, focus, clock, "notepad.exe")
    eng.source.path.write_text(CONFIG.replace("youtube}]", "youtube}, notepad]"), encoding="utf-8")
    eng._try_reload()
    assert eng.page_stack == ["main", "media"]  # no switch on reload...
    show(eng, focus, clock, "notepad.exe", "other")
    assert eng.page_stack == ["main", "media"]  # ...nor later, while it matches the same page
    show(eng, focus, clock, "X2.exe")
    eng.source.path.write_text(CONFIG.replace("    match: X2.exe\n", ""), encoding="utf-8")
    eng._try_reload()
    assert eng.page_stack == ["altium"]         # it no longer matches, but stays until focus changes
    eng.source.path.write_text(CONFIG.replace("  altium:\n    match: X2.exe\n    keys:\n"
                                              "      1: {action: {page: placement}, face: {label: Place}}\n", ""),
                               encoding="utf-8")
    eng._try_reload()
    assert eng.page_stack == ["main", "media"]


def test_app_page_removed_on_reload_puts_back_your_stack(write):
    eng, focus, clock = make(write)
    run(eng, {"page": "media"})
    show(eng, focus, clock, "chrome", "YouTube")
    assert eng.page_stack == ["youtube"]
    eng.source.path.write_text(CONFIG.replace("  youtube:\n    match: [{title: youtube}]\n    keys:\n"
                                              "      1: {action: page.home, face: {label: Home}}\n", ""),
                               encoding="utf-8")
    eng._try_reload()
    assert eng.page_stack == ["main", "media"]


def test_unsupported_platform_warns_only_when_the_config_matches(write, monkeypatch, caplog):
    from hubdeck.platforms import Unsupported

    class NoFocus:
        def focus(self):
            raise Unsupported("switching pages by app is not supported on Plan 9 yet")

    monkeypatch.setattr("hubdeck.engine.current", lambda: NoFocus())
    eng = Engine(ConfigSource(write("config.yaml", CONFIG)), device_factory=FakeKeyModule)
    eng.load_initial()
    eng._watch_focus()
    assert eng.focus_source is None and "Plan 9" in caplog.text
