"""Live values for face text: {system.time:%H:%M}, {system.cpu:.0f}%, {system.ram:.0f}%."""

from __future__ import annotations

from datetime import datetime

from .base import ThreadedPlugin


class SystemPlugin(ThreadedPlugin):
    name = "system"
    interval = 1.0
    states = {
        "time": "current local date/time; format with strftime codes, e.g. {system.time:%H:%M}",
        "cpu": "CPU usage in %",
        "ram": "memory usage in %",
    }

    def setup(self):
        import psutil
        self.psutil = psutil
        psutil.cpu_percent(None)  # first call primes the counter
        self.tick()

    def tick(self):
        self.ctx.set_state("time", datetime.now().replace(microsecond=0))
        self.ctx.set_state("cpu", self.psutil.cpu_percent(None))
        self.ctx.set_state("ram", self.psutil.virtual_memory().percent)
