import textwrap

import pytest

from hubdeck.plugins import REGISTRY, Plugin


class RecorderPlugin(Plugin):
    """Records invoked actions; tests set its states through `emit`."""
    name = "rec"
    actions = {"a": "", "b": "", "start": "", "end": ""}
    states = {"on": "", "value": ""}
    instances: list["RecorderPlugin"] = []

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self.calls = []
        self.stopped = False
        RecorderPlugin.instances.append(self)

    def invoke(self, action, arg):
        self.calls.append((action, arg))

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
