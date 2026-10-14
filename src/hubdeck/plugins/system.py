"""Live values for face text: {system.time:%H:%M}, {system.cpu:.0f}%, {system.ram:.0f}%, {system.gpu:.0f}%."""

from __future__ import annotations

import ctypes
import os
import time
from datetime import datetime, timedelta

from .base import ThreadedPlugin

NVML_LIBRARIES = ("nvml.dll", "libnvidia-ml.so.1")   # Windows (System32, from the driver), Linux


class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class Nvml:
    """The first NVIDIA GPU through the driver's NVML library. Raises OSError without one."""

    def __init__(self):
        for name in NVML_LIBRARIES:
            try:
                self.lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        else:
            raise OSError("no NVIDIA driver (NVML) found")
        self.handle = ctypes.c_void_p()
        self._check(self.lib.nvmlInit_v2())
        self._check(self.lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.handle)))

    @staticmethod
    def _check(result: int):
        if result:
            raise OSError(f"NVML error {result}")

    def _uint(self, fn, *args) -> int:
        out = ctypes.c_uint()
        self._check(fn(self.handle, *args, ctypes.byref(out)))
        return out.value

    def read(self) -> dict:
        util = (ctypes.c_uint * 2)()                 # nvmlUtilization_t: gpu %, memory bandwidth %
        self._check(self.lib.nvmlDeviceGetUtilizationRates(self.handle, util))
        mem = _Memory()
        self._check(self.lib.nvmlDeviceGetMemoryInfo(self.handle, ctypes.byref(mem)))
        return {"gpu": util[0], "vram": mem.used / mem.total * 100,
                "gpu_temp": self._uint(self.lib.nvmlDeviceGetTemperature, 0),   # 0 = the GPU die
                "gpu_power": self._uint(self.lib.nvmlDeviceGetPowerUsage) / 1000}


class SystemPlugin(ThreadedPlugin):
    name = "system"
    interval = 1.0
    states = {
        "time": "current local date/time; format with strftime codes, e.g. {system.time:%H:%M}",
        "cpu": "CPU usage in %",
        "ram": "memory usage in %",
        "gpu": "GPU usage in % (NVIDIA)",
        "vram": "GPU memory usage in % (NVIDIA)",
        "gpu_temp": "GPU temperature in °C (NVIDIA)",
        "gpu_power": "GPU power draw in W (NVIDIA)",
        "net_down": "network download in Mbit/s, all adapters",
        "net_up": "network upload in Mbit/s, all adapters",
        "disk": "system drive usage in %",
        "uptime": "time since the computer started, e.g. {system.uptime:%H:%M}",
    }

    def setup(self):
        import psutil
        self.psutil = psutil
        psutil.cpu_percent(None)  # first call primes the counter
        try:
            self.nvml: Nvml | None = Nvml()
        except (OSError, AttributeError) as e:
            self.nvml = None
            self.log.info("no GPU values: %s", e)
        self.net = None
        self.tick()

    def tick(self):
        set_state, ps = self.ctx.set_state, self.psutil
        set_state("time", datetime.now().replace(microsecond=0))
        set_state("cpu", ps.cpu_percent(None))
        set_state("ram", ps.virtual_memory().percent)
        set_state("disk", ps.disk_usage(os.environ.get("SystemDrive", "") + os.sep).percent)
        set_state("uptime", timedelta(seconds=int(time.time() - ps.boot_time())))
        now, after = time.monotonic(), ps.net_io_counters()
        if self.net:
            then, before = self.net
            set_state("net_down", (after.bytes_recv - before.bytes_recv) * 8 / 1e6 / (now - then))
            set_state("net_up", (after.bytes_sent - before.bytes_sent) * 8 / 1e6 / (now - then))
        self.net = (now, after)
        if self.nvml:
            try:
                for key, value in self.nvml.read().items():
                    set_state(key, value)
            except OSError as e:
                self.log.warning("GPU values stopped: %s", e)
                self.nvml = None
                for key in ("gpu", "vram", "gpu_temp", "gpu_power"):
                    set_state(key, None)
