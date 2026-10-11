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

from PIL import Image

from .hid import MODULE_TYPES, VID, HidModule, hid

PID = 0x01FE
KEY_COUNT = 15
IMAGE_SIZE = 72              # the reference JPEG in cooltek-sdk-probe is 72x72
OUT_REPORT_LEN = 0x140       # 320 bytes incl. report ID
OUT_CHUNK_MAX = OUT_REPORT_LEN - 8
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
    """The 15-key module, on top of the generic HidModule."""

    key_count = KEY_COUNT
    image_size = IMAGE_SIZE

    def __init__(self, path: bytes | None = None, serial: str | None = None):
        self.hid = HidModule(MODULE_TYPES[PID], path, serial)
        self.pressed = [False] * KEY_COUNT

    def set_software_mode(self, on: bool = True):
        self.hid.set_feature(bytes([0x03, 0x3A, 1 if on else 0]))

    def set_brightness(self, percent: int):
        self.hid.set_feature(bytes([0x03, 0x34, max(0, min(100, int(percent)))]))

    def encode(self, img: Image.Image) -> bytes:
        return encode_jpeg(img)

    def set_image(self, index: int, data: bytes):
        self.hid.write_many(build_image_reports(index, data))

    def poll(self) -> list[tuple[int, bool]]:
        edges = []
        for data in self.hid.read_raw():
            state = parse_key_report(data)
            if state is None:
                continue
            for i, now in enumerate(state):
                if now != self.pressed[i]:
                    edges.append((i, now))
            self.pressed = state
        return edges

    def close(self):
        self.hid.close()


def list_interfaces() -> list[str]:
    """Human-readable lines for every Cooler Master HID interface."""
    if hid is None:
        return ["hidapi is not installed: pip install hidapi"]
    lines = []
    for i in hid.enumerate(VID):
        known = MODULE_TYPES.get(i["product_id"])
        mark = f"  <- {known.code} ({known.aliases[0]})" if known else ""
        lines.append(f"PID 0x{i['product_id']:04x}  usage_page 0x{i['usage_page']:04x}  usage 0x{i['usage']:04x}  "
                     f"iface {i['interface_number']}  {i.get('product_string') or ''}{mark}\n    path {i['path']!r}")
    return lines or ["No Cooler Master (VID 0x2516) HID devices found."]
