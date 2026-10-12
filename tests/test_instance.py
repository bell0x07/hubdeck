"""hubdeck-tray --replace: the Windows installer's way to swap a running hubdeck for the new one."""

import pytest

from hubdeck import control, instance


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(instance.time, "sleep", lambda s: None)


def test_asks_the_running_hubdeck_to_quit_then_takes_over(monkeypatch, fast):
    sent, frees = [], iter([False, False, True])        # the old one needs a moment to exit
    monkeypatch.setattr(control, "send", lambda req: sent.append(req) or {"ok": True})
    monkeypatch.setattr(instance, "acquire", lambda: next(frees))
    assert instance.replace_running() is True
    assert sent == [{"cmd": "quit"}]


def test_an_old_version_without_quit_is_stopped(monkeypatch, fast):
    def refuse(req):
        raise control.ControlError("unknown command 'quit'")
    monkeypatch.setattr(control, "send", refuse)
    monkeypatch.setattr(instance, "stop_other_copies", lambda: True)
    monkeypatch.setattr(instance, "acquire", lambda: True)
    assert instance.replace_running() is True


def test_gives_up_quietly_when_it_cant(monkeypatch, fast):
    def refuse(req):
        raise control.ControlError("no answer")
    monkeypatch.setattr(control, "send", refuse)
    monkeypatch.setattr(instance, "stop_other_copies", lambda: False)
    assert instance.replace_running() is False

    monkeypatch.setattr(control, "send", lambda req: {"ok": True})
    monkeypatch.setattr(instance, "acquire", lambda: False)   # never quits
    monkeypatch.setattr(instance.time, "monotonic", iter(range(0, 1000, 5)).__next__)
    assert instance.replace_running(timeout=15) is False


def test_never_stops_anything_when_run_from_python():
    """From a venv, sys.executable is a python shared with other programs: hands off."""
    assert instance.stop_other_copies() is False
