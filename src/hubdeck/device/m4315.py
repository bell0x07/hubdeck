"""MasterHub M4315: 15 keys with IPS screens.

Protocol from https://github.com/projectzero7/cooltek-sdk-probe (MIT), verified
on real hardware:
  feature 03 3a 01            software mode on (required before anything else)
  feature 03 34 <0-100>       backlight brightness
  output  02 36 <chunk u16> <len u16> <key> <last> + JPEG chunk (320-byte reports)
  input   01 34 <len u16> 02 0f k1..k15
"""

from __future__ import annotations

import io
import struct
import threading

from PIL import Image

from .base import DeviceError

try:
    import hid  # pip install hidapi
except ImportError:  # allow importing for tests without hidapi
    hid = None

VID = 0x2516
PID = 0x01FE
KEY_COUNT = 15
IMAGE_SIZE = 72              # the reference JPEG in cooltek-sdk-probe is 72x72
OUT_REPORT_LEN = 0x140       # 320 bytes incl. report ID
OUT_CHUNK_MAX = OUT_REPORT_LEN - 8
FEATURE_LEN = 0x20           # 32 bytes incl. report ID
INPUT_TAG_CH0 = 0x34         # '4' - channel 0 carries key events


def build_image_reports(index: int, jpeg: bytes) -> list[bytes]:
    """Split a JPEG into output reports (report 0x02, command 0x36)."""
    if not 0 <= index < KEY_COUNT:
        raise ValueError(f"key index must be 0..{KEY_COUNT - 1}")
    reports = []
    remaining = len(jpeg)
    chunk_index = 0
    while remaining > 0:
        chunk_len = min(remaining, OUT_CHUNK_MAX)
        report = bytearray(OUT_REPORT_LEN)
        report[0] = 0x02
        report[1] = 0x36
        report[2:4] = struct.pack("<H", chunk_index)
        report[4:6] = struct.pack("<H", chunk_len)
        report[6] = index
        report[7] = 1 if remaining <= OUT_CHUNK_MAX else 0
        offset = len(jpeg) - remaining
        report[8:8 + chunk_len] = jpeg[offset:offset + chunk_len]
        reports.append(bytes(report))
        chunk_index += 1
        remaining -= chunk_len
    return reports


def parse_key_report(data) -> list[bool] | None:
    """Parse an input report into 15 pressed/not-pressed flags, or None."""
    if not data or len(data) < 4 + 2 + KEY_COUNT:
        return None
    if data[0] != 0x01 or data[1] != INPUT_TAG_CH0:
        return None
    payload_len = data[2] | (data[3] << 8)
    payload = data[4:4 + payload_len]
    if len(payload) < 2 + KEY_COUNT or payload[0] != 0x02 or payload[1] != KEY_COUNT:
        return None
    return [b == 1 for b in payload[2:2 + KEY_COUNT]]


def encode_jpeg(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    if img.size != (IMAGE_SIZE, IMAGE_SIZE):
        img = img.resize((IMAGE_SIZE, IMAGE_SIZE), Image.LANCZOS)
    out = io.BytesIO()
    # baseline, 4:4:4 - matches the JPEG the vendor app sends
    img.save(out, "JPEG", quality=90, subsampling=0, progressive=False, optimize=False)
    return out.getvalue()


class M4315:
    """Opens every HID interface the module exposes and uses whichever one
    accepts each kind of report (Windows splits HID top-level collections
    into separate paths)."""

    key_count = KEY_COUNT
    image_size = IMAGE_SIZE

    def __init__(self, path: bytes | None = None):
        if hid is None:
            raise DeviceError("hidapi is not installed: pip install hidapi")
        paths = [path] if path else [i["path"] for i in hid.enumerate(VID, PID)]
        if not paths:
            raise DeviceError("MasterHub 15-key screen module (M4315) not found")
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
            raise DeviceError("Found the module but could not open it (is another app holding it exclusively?)")
        self._feature_dev = None
        self._write_dev = None
        self._lock = threading.Lock()
        self.pressed = [False] * KEY_COUNT

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

    def _feature(self, cmd: int, value: int):
        buf = bytearray(FEATURE_LEN)
        buf[0], buf[1], buf[2] = 0x03, cmd, value
        with self._lock:
            self._try("_feature_dev", lambda h: h.send_feature_report(bytes(buf)) > 0)

    def set_software_mode(self, on: bool = True):
        self._feature(0x3A, 1 if on else 0)

    def set_brightness(self, percent: int):
        self._feature(0x34, max(0, min(100, int(percent))))

    def encode(self, img: Image.Image) -> bytes:
        return encode_jpeg(img)

    def set_image(self, index: int, data: bytes):
        reports = build_image_reports(index, data)

        def send_all(h):
            for r in reports:
                if h.write(r) != len(r):
                    return False
            return True

        with self._lock:
            self._try("_write_dev", send_all)

    def poll(self) -> list[tuple[int, bool]]:
        edges = []
        failed = 0
        for h in self.handles:
            while True:
                try:
                    data = h.read(OUT_REPORT_LEN)
                except OSError:
                    failed += 1
                    break
                if not data:
                    break
                state = parse_key_report(data)
                if state is None:
                    continue
                for i, now in enumerate(state):
                    if now != self.pressed[i]:
                        edges.append((i, now))
                self.pressed = state
        if failed == len(self.handles):
            raise DeviceError("Lost connection to the module")
        return edges

    def close(self):
        for h in self.handles:
            try:
                h.close()
            except Exception:
                pass


def list_interfaces() -> list[str]:
    """Human-readable lines for every Cooler Master HID interface."""
    if hid is None:
        return ["hidapi is not installed: pip install hidapi"]
    lines = []
    for i in hid.enumerate(VID):
        mark = "  <- 15-key module" if i["product_id"] == PID else ""
        lines.append(f"PID 0x{i['product_id']:04x}  usage_page 0x{i['usage_page']:04x}  usage 0x{i['usage']:04x}  "
                     f"iface {i['interface_number']}  {i.get('product_string') or ''}{mark}\n    path {i['path']!r}")
    return lines or ["No Cooler Master (VID 0x2516) HID devices found."]
