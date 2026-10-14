"""HWiNFO's readings (Windows): any sensor HWiNFO reports to its Gadget, by its label.

HWiNFO reads what Windows doesn't show other apps, such as the CPU's temperature. In its
Sensors window, Configure > HWiNFO Gadget: tick Enable reporting to Gadget, then Report
value in Gadget for each reading wanted.

    keys:
      5: {face: {text: "{hwinfo.value[CPU (Tctl/Tdie)]:.0f}°", label: CPU}}
"""

from __future__ import annotations

from ..platforms import Unsupported, current
from .base import ThreadedPlugin, state_key


class HwinfoPlugin(ThreadedPlugin):
    name = "hwinfo"
    platforms = ("win32",)
    interval = 1.0
    states = {"value": "a reading HWiNFO reports to its Gadget, by its label: hwinfo.value[CPU (Tctl/Tdie)]"}
    state_args = {"value": "the reading's label in HWiNFO"}
    fetches_states = True

    def setup(self):
        self.read = current().hwinfo
        self.silent = False                       # HWiNFO reports nothing: warned once
        self.missing: set[str] = set()            # labels warned about

    def tick(self):
        if self.read is None or not self.watched_states:
            return
        try:
            readings = self.read()
        except Unsupported as e:
            self.log.warning("%s", e)
            self.read = None
            return
        if not readings and not self.silent:
            self.log.warning("HWiNFO reports nothing: start it, and in its Sensors window tick "
                             "Configure > HWiNFO Gadget > Enable reporting to Gadget")
        self.silent = not readings
        for name, label in self.watched_states:
            if readings and label not in readings and label not in self.missing:
                self.missing.add(label)
                self.log.warning("HWiNFO reports no %r (it reports: %s); tick Report value in Gadget for it",
                                 label, ", ".join(sorted(readings)))
            self.ctx.set_state(state_key(name, label), readings.get(label))
