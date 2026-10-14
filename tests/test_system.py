"""The system plugin's values, with a stand-in for the GPU."""

from datetime import timedelta

from hubdeck.plugins import system as system_mod
from hubdeck.plugins.base import PluginContext
from hubdeck.plugins.system import SystemPlugin


class FakeGpu:
    def read(self):
        return {"gpu": 7, "vram": 50.0, "gpu_temp": 45, "gpu_power": 60.5}


def make(states):
    return SystemPlugin(PluginContext("system", lambda plugin, key, value: states.__setitem__(key, value)), {})


def test_every_state_is_published(monkeypatch):
    monkeypatch.setattr(system_mod, "Nvml", FakeGpu)
    states = {}
    plugin = make(states)
    plugin.setup()
    plugin.tick()
    assert set(states) == set(SystemPlugin.states)
    assert states["gpu"] == 7 and states["gpu_power"] == 60.5
    assert isinstance(states["uptime"], timedelta) and states["net_down"] >= 0 and 0 < states["disk"] <= 100


def test_no_gpu_leaves_its_values_unknown(monkeypatch):
    def no_gpu():
        raise OSError("no NVIDIA driver (NVML) found")
    monkeypatch.setattr(system_mod, "Nvml", no_gpu)
    states = {}
    plugin = make(states)
    plugin.setup()
    assert "gpu" not in states and states["cpu"] is not None
