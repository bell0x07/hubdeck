import pytest

from hubdeck.device.hid import (MODULE_TYPES, UnsafeCommand, check_write, feature_report_ids, find_type,
                                parse_frame)

# real report descriptor of the 3xKnob module (PID 0x0202), read from hardware
KNOB_DESCRIPTOR = bytes.fromhex(
    "050c0901a10185010a00ff150026ff00750895 3f8102 85020a00ff150026ff00750895 3f9102"
    "85030a00ff150026ff00750895 1fb104 852d0a00ff150026ff00750895 1fb104 852e0a00ff150026ff00750895 1fb104"
    "852f0a00ff150026ff00750895 1fb104 85300a00ff150026ff00750895 1fb104 85310a00ff150026ff00750895 1fb104"
    "85320a00ff150026ff00750895 1fb104 85340a00ff150026ff00750895 1fb104 85360a00ff150026ff00750895 1fb104 c0"
    .replace(" ", ""))


def test_feature_report_ids_from_descriptor():
    assert feature_report_ids(KNOB_DESCRIPTOR) == [0x03, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x34, 0x36]


def test_parse_frame_m0_knob_rotation():
    # 01 <tag ch0> <len 5> | event 00, len 3, knob1 clockwise, others unchanged
    f = parse_frame(bytes.fromhex("012a0500 000301ffff") + bytes(54), 0x2A)
    assert (f.channel, f.event, f.data) == (0, 0x00, b"\x01\xff\xff")


def test_parse_frame_unknown_tag_and_garbage():
    assert parse_frame(bytes.fromhex("01990200 0000"), 0x2A).channel is None
    assert parse_frame(b"\x02\x00", 0x2A) is None
    assert parse_frame(b"", 0x2A) is None


def test_denylist_is_absolute():
    m0 = find_type("knobs")
    for cmd in (0x2A, 0x2B, 0x31, 0x49, 0x4A):
        with pytest.raises(UnsafeCommand, match="denylist"):
            check_write(m0, "output", bytes([0x02, cmd]), unverified=True)
    keys = find_type("keys")
    with pytest.raises(UnsafeCommand, match="denylist"):
        check_write(keys, "feature", bytes([0x03, 0x3D]), unverified=True)   # factory reset
    with pytest.raises(UnsafeCommand, match="denylist"):
        check_write(keys, "output", bytes([0x02, 0x34]), unverified=True)    # firmware update


def test_unverified_commands_need_opt_in():
    dial = find_type("dial")
    check_write(dial, "feature", bytes([0x03, 0x49, 0x01]))                 # software mode: known
    with pytest.raises(UnsafeCommand, match="not verified"):
        check_write(dial, "feature", bytes([0x03, 0x4E, 0x50]))             # unknown command
    check_write(dial, "feature", bytes([0x03, 0x4E, 0x50]), unverified=True)


def test_dial_commands_from_vendor_dll():
    dial = find_type("dial")
    for cmd in (0x45, 0x48, 0x4B, 0x4C, 0x4D):   # serial, fw recovery, factory data, factory reset, anim recovery
        with pytest.raises(UnsafeCommand, match="denylist"):
            check_write(dial, "feature", bytes([0x03, cmd]), unverified=True)
    with pytest.raises(UnsafeCommand, match="denylist"):
        check_write(dial, "output", bytes([0x02, 0x44]), unverified=True)   # firmware update
    check_write(dial, "feature", bytes([0x03, 0x44, 0x32]))                 # backlight
    check_write(dial, "feature", bytes([0x03, 0x46, 0xFF, 0, 0]))           # solid colour
    check_write(dial, "output", bytes([0x02, 0x45]))                        # picture


def test_known_safe_commands():
    keys = find_type("keys")
    check_write(keys, "feature", bytes([0x03, 0x3A, 0x01]))   # software mode
    check_write(keys, "feature", bytes([0x03, 0x34, 0x50]))   # brightness
    check_write(keys, "output", bytes([0x02, 0x36]))          # key image
    check_write(find_type("base"), "output", bytes([0x02, 0x2D, 0, 0, 255, 0, 0, 0]))  # LED colour


def test_find_type_aliases():
    assert find_type("Dial").pid == 0x0206
    assert find_type("M003K").pid == 0x0202
    assert find_type("0x0204").code == "M032LE"
    with pytest.raises(KeyError):
        find_type("toaster")
    assert {t.family for t in MODULE_TYPES.values()} == {"screen", "m0"}
