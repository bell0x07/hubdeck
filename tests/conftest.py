import textwrap

import pytest

from hubdeck.plugins import REGISTRY, Plugin


class RecorderPlugin(Plugin):
    """Records invoked actions; tests set its states through `emit`."""
    name = "rec"
    actions = {"a": "", "b": "", "start": "", "end": ""}
    states = {"on": "", "value": ""}
    values = {"level": "", "app": ""}
    instances: list["RecorderPlugin"] = []

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self.calls = []
        self.stopped = False
        RecorderPlugin.instances.append(self)

    @classmethod
    def check_value(cls, name, arg):
        if name == "app" and not arg:
            return "rec.app needs arg"
        return None

    def invoke(self, action, arg):
        self.calls.append((action, arg))

    def adjust(self, name, arg, delta):
        self.calls.append(("adjust", name, arg, delta))

    def set(self, name, arg, value):
        self.calls.append(("set", name, arg, value))

    def stop(self):
        self.stopped = True

    def emit(self, key, value):
        self.ctx.set_state(key, value)


@pytest.fixture(autouse=True)
def recorder():
    RecorderPlugin.instances.clear()
    REGISTRY["rec"] = RecorderPlugin
    yield RecorderPlugin
    REGISTRY.pop("rec", None)


@pytest.fixture
def write(tmp_path):
    def _write(name, text):
        p = tmp_path / name
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p
    return _write
