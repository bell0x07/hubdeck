"""Generic access to MasterHub modules: feature reads/writes, output reports,
framed input, plus a safety policy every write goes through.

Each module is its own USB HID device (VID 0x2516). Windows may expose
several paths per device (one per top-level collection); all are opened and
whichever accepts a report type is remembered.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .base import DeviceError

try:
    import hid  # pip install hidapi
except ImportError:  # allow importing for tests without hidapi
    hid = None

VID = 0x2516

# the engine lists modules while it retries missing ones, and the editor's thread does too;
# hidapi's enumerate keeps shared state (one IOHIDManager on macOS), so take turns
_enumerating = threading.Lock()


def _enumerate(*args) -> list[dict]:
    with _enumerating:
        return hid.enumerate(*args)


@dataclass(frozen=True)
class ModuleType:
    pid: int
    code: str
    name: str
    family: str                       # "screen" or "m0"
    aliases: tuple[str, ...]
    out_len: int                      # output report size incl. report ID
    tag_base: int                     # input channel 0 tag; channels 0-4 follow
    feature_len: int = 32             # feature report size incl. report ID
    # Commands that must never be sent (firmware, serial, factory data/reset, recovery)
    deny_feature: frozenset[int] = field(default_factory=frozenset)
    deny_output: frozenset[int] = field(default_factory=frozenset)
    # Commands known to be harmless; anything else needs an explicit "unverified" opt-in
    safe_feature: frozenset[int] = field(default_factory=frozenset)
    safe_output: frozenset[int] = field(default_factory=frozenset)


_M0 = dict(family="m0", out_len=64, tag_base=0x2A,
           deny_output=frozenset({0x2A, 0x2B, 0x31, 0x49, 0x4A}),
           safe_output=frozenset({0x2C, 0x2D, 0x2E, 0x30, 0x32, 0x33}))

MODULE_TYPES: dict[int, ModuleType] = {t.pid: t for t in (
    ModuleType(0x01FE, "M4315", "15xKey w/ IPS", "screen", ("keys", "m4315"), out_len=320, tag_base=0x34,
               deny_feature=frozenset({0x35, 0x39, 0x3C, 0x3D, 0x3E}), deny_output=frozenset({0x34, 0x38}),
               safe_feature=frozenset({0x34, 0x36, 0x37, 0x3A}), safe_output=frozenset({0x36})),
    # Dial command IDs from the vendor DLL CoolTEK_SDK_CM_M131E_V03.02.00 (see docs/protocol/m131e.md)
    ModuleType(0x0206, "M131E", "1xDial w/ IPS", "screen", ("dial", "m131e"), out_len=320, tag_base=0x44,
               deny_feature=frozenset({0x45, 0x48, 0x4B, 0x4C, 0x4D}), deny_output=frozenset({0x44, 0x47}),
               safe_feature=frozenset({0x44, 0x46, 0x49}), safe_output=frozenset({0x45, 0x48, 0x49})),
    ModuleType(0x01F6, "M0064", "Base 4x6", aliases=("base", "m0064"), **_M0),
    ModuleType(0x0202, "M003K", "3xKnob", aliases=("knobs", "knob", "m003k"), **_M0),
    ModuleType(0x0204, "M032LE", "2xRoller", aliases=("rollers", "roller", "m032le"), **_M0),
    ModuleType(0x0200, "M005S", "5xFader", aliases=("faders", "fader", "m005s"), **_M0),
)}


def find_type(name: str) -> ModuleType:
    """Look up a module type by alias ('dial'), code ('M131E') or PID ('0x0206')."""
    key = name.lower()
    for t in MODULE_TYPES.values():
        if key in t.aliases or key == t.code.lower() or key in (f"0x{t.pid:04x}", str(t.pid)):
            return t
    raise KeyError(f"unknown module {name!r}; use one of: " +
                   ", ".join(t.aliases[0] for t in MODULE_TYPES.values()))


class UnsafeCommand(DeviceError):
    pass


def check_write(mtype: ModuleType, kind: str, data: bytes, unverified: bool = False):
    """Raise UnsafeCommand unless this report may be sent. kind: 'feature' or 'output'."""
    if len(data) < 2:
        raise UnsafeCommand("report too short")
    cmd = data[1]
    deny = mtype.deny_feature if kind == "feature" else mtype.deny_output
    safe = mtype.safe_feature if kind == "feature" else mtype.safe_output
    if cmd in deny:
        raise UnsafeCommand(f"{mtype.code} {kind} command 0x{cmd:02x} is on the denylist "
                            "(firmware/serial/factory/recovery) and is never sent")
    if cmd not in safe and not unverified:
        raise UnsafeCommand(f"{mtype.code} {kind} command 0x{cmd:02x} is not verified as safe; "
                            "only send it when it comes from a capture or the vendor SDK (--unverified)")


def build_chunked_reports(cmd: int, data: bytes, index: int = 0, report_len: int = 320) -> list[bytes]:
    """Screen-family image upload: `02 <cmd> <chunk u16> <len u16> <index> <last> <data>`
    in output reports of report_len bytes (8-byte header)."""
    chunk_max = report_len - 8
    reports = []
    for chunk, offset in enumerate(range(0, len(data), chunk_max)):
        part = data[offset:offset + chunk_max]
        last = offset + chunk_max >= len(data)
        header = bytes([0x02, cmd]) + chunk.to_bytes(2, "little") + len(part).to_bytes(2, "little") \
            + bytes([index, 1 if last else 0])
        reports.append((header + part).ljust(report_len, b"\0"))
    return reports


@dataclass(frozen=True)
class Frame:
    channel: int | None   # 0-4, or None for an unknown tag
    tag: int
    payload: bytes

    @property
    def event(self) -> int | None:
        return self.payload[0] if self.payload else None

    @property
    def data(self) -> bytes:
        """Event data, using the event's own length byte."""
        if len(self.payload) < 2:
            return b""
        return self.payload[2:2 + self.payload[1]]


def parse_frame(data, tag_base: int) -> Frame | None:
    """Input report `01 <tag> <len u16 LE> <payload>` -> Frame."""
    if not data or len(data) < 4 or data[0] != 0x01:
        return None
    tag = data[1]
    length = data[2] | (data[3] << 8)
    channel = tag - tag_base if tag_base <= tag < tag_base + 5 else None
    return Frame(channel, tag, bytes(data[4:4 + length]))


def feature_report_ids(descriptor: bytes) -> list[int]:
    """Report IDs of Feature items in a HID report descriptor."""
    ids, report_id, i = [], 0, 0
    while i < len(descriptor):
        prefix = descriptor[i]
        if prefix == 0xFE:  # long item
            i += 3 + descriptor[i + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        value = int.from_bytes(descriptor[i + 1:i + 1 + size], "little")
        tag = prefix & 0xFC
        if tag == 0x84:      # Report ID (global)
            report_id = value
        elif tag == 0xB0 and report_id not in ids:   # Feature (main)
            ids.append(report_id)
        i += 1 + size
    return ids


def enumerate_modules() -> list[tuple[ModuleType, dict]]:
    """[(type, first hidapi info dict)] for every connected known module, one per serial."""
    if hid is None:
        return []
    seen, out = set(), []
    for info in _enumerate(VID):
        mtype = MODULE_TYPES.get(info["product_id"])
        key = (info["product_id"], info.get("serial_number"))
        if mtype and key not in seen:
            seen.add(key)
            out.append((mtype, info))
    return out


class HidModule:
    """One physical module, possibly spread over several HID paths."""

    def __init__(self, mtype: ModuleType, path: bytes | None = None, serial: str | None = None):
        if hid is None:
            raise DeviceError("hidapi is not installed: pip install hidapi")
        self.type = mtype
        infos = [i for i in _enumerate(VID, mtype.pid)
                 if serial is None or i.get("serial_number") == serial]
        paths = [path] if path else [i["path"] for i in infos]
        if not paths:
            raise DeviceError(f"MasterHub {mtype.name} ({mtype.code}) not found")
        self.serial = serial or (infos[0].get("serial_number") if infos else None)
        self.handles = []
        for p in paths:
            try:
                d = hid.device()
                d.open_path(p)
                d.set_nonblocking(True)
                self.handles.append(d)
            except OSError:
                pass
        if not self.handles:
            raise DeviceError(f"Found the {mtype.name} but could not open it (is another app holding it exclusively?)")
        self._feature_dev = None
        self._write_dev = None
        self._lock = threading.Lock()

    def _try(self, attr: str, fn):
        cached = getattr(self, attr)
        order = ([cached] if cached else []) + [h for h in self.handles if h is not cached]
        last = None
        for h in order:
            try:
                if fn(h):
                    setattr(self, attr, h)
                    return
            except (OSError, ValueError) as e:
                last = e
        raise DeviceError(f"No interface accepted the report ({last})")

    # -- writes ------------------------------------------------------------
    def set_feature(self, data: bytes, unverified: bool = False):
        check_write(self.type, "feature", data, unverified)
        buf = bytes(data).ljust(self.type.feature_len, b"\0")
        with self._lock:
            self._try("_feature_dev", lambda h: h.send_feature_report(buf) > 0)

    def write(self, data: bytes, unverified: bool = False):
        check_write(self.type, "output", data, unverified)
        buf = bytes(data).ljust(self.type.out_len, b"\0")
        with self._lock:
            self._try("_write_dev", lambda h: h.write(buf) == len(buf))

    def write_many(self, reports: list[bytes]):
        """Several output reports in a row (e.g. image chunks), same safety check per report."""
        for r in reports:
            check_write(self.type, "output", r)
        bufs = [bytes(r).ljust(self.type.out_len, b"\0") for r in reports]

        def send_all(h):
            return all(h.write(b) == len(b) for b in bufs)

        with self._lock:
            self._try("_write_dev", send_all)

    # -- reads -------------------------------------------------------------
    def get_feature(self, report_id: int, length: int | None = None) -> bytes | None:
        """GET_FEATURE; returns the report without its ID byte, or None if no interface answers."""
        length = length or self.type.feature_len
        with self._lock:
            for h in ([self._feature_dev] if self._feature_dev else []) + self.handles:
                try:
                    data = h.get_feature_report(report_id, length)
                except (OSError, ValueError):
                    continue
                if data:
                    return bytes(data[1:]) if data[0] == report_id else bytes(data)
        return None

    def report_descriptor(self) -> bytes | None:
        for h in self.handles:
            try:
                return bytes(h.get_report_descriptor())
            except (OSError, ValueError, AttributeError):
                continue
        return None

    def read_raw(self) -> list[bytes]:
        """All pending input reports. Raises DeviceError if every interface fails."""
        out, failed = [], 0
        for h in self.handles:
            while True:
                try:
                    data = h.read(self.type.out_len)
                except OSError:
                    failed += 1
                    break
                if not data:
                    break
                out.append(bytes(data))
        if failed == len(self.handles):
            raise DeviceError(f"Lost connection to the {self.type.name}")
        return out

    def read_frames(self) -> list[Frame]:
        return [f for f in (parse_frame(d, self.type.tag_base) for d in self.read_raw()) if f]

    def close(self):
        for h in self.handles:
            try:
                h.close()
            except Exception:
                pass
