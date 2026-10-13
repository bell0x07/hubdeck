"""Pages' on_page_enter / on_page_exit: actions when a page comes on or goes off screen."""

import pytest

from hubdeck.config import ConfigError, load_config
from test_engine import make, rec, tap, wait_for

CONFIG = """
device: {reassert_seconds: 0}
pages:
  main:
    on_page_enter: {rec.a: main}
    keys:
      1: {action: {page: sub}, face: {label: Sub}}
  sub:
    home_after: 30
    on_page_enter: [{rec.a: sub}, {rec.b: sub}]
    on_page_exit: {rec.start: sub}
    keys:
      1: {action: page.back, face: {label: Back}}
"""


def test_entering_and_leaving_run_the_actions(write, recorder):
    eng, dev, clock = make(write, CONFIG)
    plugin = rec(recorder)
    assert plugin.calls == [("a", "main")]                     # the start page, at start
    tap(eng, dev, clock, 0)
    assert plugin.calls[1:] == [("a", "sub"), ("b", "sub")]
    tap(eng, dev, clock, 0)
    assert plugin.calls[3:] == [("start", "sub"), ("a", "main")]   # exit, then enter


def test_every_way_of_switching_counts_but_a_reload_does_not(write, recorder):
    eng, dev, clock = make(write, CONFIG)
    plugin = rec(recorder)
    eng.deck.goto("sub"); eng.step()
    assert plugin.calls[-1] == ("b", "sub")
    write("config.yaml", CONFIG.replace("Back", "Back!"))
    eng.reload(); eng.step()                                    # still on sub: nothing runs
    plugin = rec(recorder)
    n = len(plugin.calls)
    clock.t += 31; eng.step()                                   # home_after goes back to main
    assert eng.current_page == "main"
    assert plugin.calls[n:] == [("start", "sub"), ("a", "main")]


def test_python_in_a_hook_acts_on_its_own_page(write):
    eng, dev, clock = make(write, """
        device: {reassert_seconds: 0}
        pages:
          main:
            keys: {1: {action: {page: sub}, face: {label: Sub}}}
          sub:
            on_page_exit: {python: "deck.set_face(1, label=f'left {deck.control} {deck.key}')"}
            keys: {1: {action: page.back, face: {label: Back}}}
    """)
    tap(eng, dev, clock, 0)
    tap(eng, dev, clock, 0)                                     # back to main: sub's exit runs
    assert eng.current_page == "main"
    wait_for(lambda: (eng.step() or eng.resolve_face(1, "sub").label) == "left None None")
    assert eng.resolve_face(1).label == "Sub"                   # main's key untouched


def test_hooks_are_checked_and_start_their_plugins(write):
    cfg = load_config(write("config.yaml", """
        pages:
          main: {on_page_enter: rec.a, keys: {1: {face: {label: x}}}}
    """))
    assert "rec" in cfg.used_plugins()
    with pytest.raises(ConfigError, match=r"pages\.main\.on_page_exit: rec has no action 'nope'"):
        load_config(write("config.yaml", """
            pages:
              main: {on_page_exit: rec.nope, keys: {1: {face: {label: x}}}}
        """))
