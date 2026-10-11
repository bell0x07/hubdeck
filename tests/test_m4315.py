from PIL import Image

from hubdeck.device.m4315 import (IMAGE_SIZE, OUT_CHUNK_MAX, OUT_REPORT_LEN, build_image_reports,
                                  encode_jpeg, parse_key_report)


def test_image_reports_chunking():
    jpeg = bytes(range(256)) * 3  # 768 bytes -> 312 + 312 + 144
    reports = build_image_reports(4, jpeg)
    assert len(reports) == 3
    assert all(len(r) == OUT_REPORT_LEN for r in reports)
    for i, r in enumerate(reports):
        assert r[0:2] == b"\x02\x36"
        assert int.from_bytes(r[2:4], "little") == i
        assert r[6] == 4
    assert [int.from_bytes(r[4:6], "little") for r in reports] == [OUT_CHUNK_MAX, OUT_CHUNK_MAX, 144]
    assert [r[7] for r in reports] == [0, 0, 1]
    assert b"".join(r[8:8 + int.from_bytes(r[4:6], "little")] for r in reports) == jpeg


def test_generic_chunker_for_dial_picture():
    from hubdeck.device.hid import build_chunked_reports
    reports = build_chunked_reports(0x45, bytes(700))
    assert [r[:2] for r in reports] == [b"\x02\x45"] * 3
    assert [r[7] for r in reports] == [0, 0, 1] and all(r[6] == 0 for r in reports)


def test_parse_key_report():
    payload = bytes([0x02, 0x0F] + [0] * 10 + [1] + [0] * 4)
    data = bytes([0x01, 0x34]) + len(payload).to_bytes(2, "little") + payload
    state = parse_key_report(data)
    assert state[10] is True and sum(state) == 1
    assert parse_key_report(b"\x01\x35" + data[2:]) is None
    assert parse_key_report(b"") is None


def test_encode_is_baseline_72px_jpeg():
    data = encode_jpeg(Image.new("RGB", (288, 288), (200, 30, 40)))
    assert data[:2] == b"\xff\xd8"
    img = Image.open(__import__("io").BytesIO(data))
    assert img.size == (IMAGE_SIZE, IMAGE_SIZE)
    assert not img.info.get("progressive")
