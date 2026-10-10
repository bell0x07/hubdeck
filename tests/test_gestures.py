from hubdeck.gestures import GestureTracker, KeyGesture


def run(k: KeyGesture, script):
    """script: list of (time, 'down'|'up'|'tick')"""
    out = []
    for t, what in script:
        out += k.tick(t) if what == "tick" else k.edge(what == "down", t)
    return out


def test_simple_key_fires_on_down():
    k = KeyGesture(None, None)
    assert run(k, [(0, "down")]) == ["down", "press"]
    assert run(k, [(0.1, "up")]) == ["up"]


def test_long_press():
    k = KeyGesture(0.5, None)
    assert run(k, [(0, "down"), (0.4, "tick")]) == ["down"]
    assert run(k, [(0.5, "tick"), (0.6, "tick"), (0.7, "up")]) == ["long_press", "up"]


def test_short_press_with_long_defined_fires_on_release():
    k = KeyGesture(0.5, None)
    assert run(k, [(0, "down"), (0.2, "up")]) == ["down", "up", "press"]


def test_double_press():
    k = KeyGesture(None, 0.25)
    assert run(k, [(0, "down"), (0.05, "up"), (0.15, "down"), (0.2, "up")]) == \
        ["down", "up", "down", "up", "double_press"]
    assert run(k, [(1, "tick")]) == []


def test_single_press_with_double_defined_waits_for_window():
    k = KeyGesture(None, 0.25)
    assert run(k, [(0, "down"), (0.05, "up"), (0.2, "tick")]) == ["down", "up"]
    assert run(k, [(0.31, "tick")]) == ["press"]


def test_reconfigure_waits_until_release():
    g = GestureTracker(2)
    assert g.edge(0, True, 0) == [(0, "down"), (0, "press")]
    g.configure(0, 0.5, None)          # page switched while held
    assert g.tick(1.0) == []           # old (simple) settings: no long press
    assert g.edge(0, False, 1.1) == [(0, "up")]
    assert g.edge(0, True, 2) == [(0, "down")]   # new settings now active
    assert g.tick(2.6) == [(0, "long_press")]
