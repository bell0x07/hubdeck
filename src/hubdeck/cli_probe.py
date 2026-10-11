"""`hubdeck probe`: reverse-engineering helpers (hidden from normal help).

  probe info [module]               read every declared feature report (read-only)
  probe listen [module ...]         print decoded input frames live (read-only)
  probe send <module> <hex>         send one report (safety policy applies)
  probe swmode <module> on|off      software mode for screen modules
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from .device.base import DeviceError
from .device.hid import (MODULE_TYPES, Frame, HidModule, ModuleType, UnsafeCommand, check_write,
                         enumerate_modules, feature_report_ids, find_type, parse_frame)

SW_MODE_CMD = {0x01FE: 0x3A, 0x0206: 0x49}


def _hex(data: bytes, strip: bool = True) -> str:
    if strip:
        data = data.rstrip(b"\0") or data[:1]
    return data.hex(" ")


def _ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _selected(names: list[str] | None) -> list[tuple[ModuleType, dict]]:
    found = enumerate_modules()
    if names:
        wanted = {find_type(n).pid for n in names}
        found = [(t, i) for t, i in found if t.pid in wanted]
    if not found:
        sys.exit("no matching MasterHub modules connected")
    return found


def decode_feature(mtype: ModuleType, report_id: int, data: bytes) -> str:
    """Best-effort meaning of a GET_FEATURE reply (data excludes the report ID)."""
    if not data or data[0] == 0:
        return "(empty)"
    length, body = data[0], data[1:1 + data[0]]
    text = _ascii(body)
    if body and sum(32 <= b < 127 for b in body) >= len(body) * 0.6:
        return f"len {length}: {text!r}"
    return f"len {length}: {body.hex(' ')}"


def cmd_info(args):
    for mtype, info in _selected([args.module] if args.module else None):
        print(f"== {mtype.code} {mtype.name}  PID 0x{mtype.pid:04x}  serial {info.get('serial_number')!r}")
        try:
            dev = HidModule(mtype, serial=info.get("serial_number"))
        except DeviceError as e:
            print(f"   {e}")
            continue
        try:
            desc = dev.report_descriptor()
            ids = feature_report_ids(desc) if desc else []
            print(f"   feature report ids: {', '.join(f'0x{i:02x}' for i in ids) or 'unknown'}")
            for rid in ids:
                if rid == 0x03:
                    continue  # command report, not a getter
                data = dev.get_feature(rid)
                shown = "no answer" if data is None else f"{_hex(data)}   -> {decode_feature(mtype, rid, data)}"
                print(f"   get 0x{rid:02x}: {shown}")
        finally:
            dev.close()


def describe_frame(mtype: ModuleType, f: Frame) -> str:
    """Human-readable meaning of known input events."""
    ch = "ch?" if f.channel is None else f"ch{f.channel}"
    d = f.data
    dirs = {0: "ccw/up", 1: "cw/down", 0xFF: "-"}
    if mtype.family == "m0" and f.channel == 0:
        if f.event == 0x00:
            return f"{ch} rotate " + " ".join(f"k{i + 1}:{dirs.get(b, hex(b))}" for i, b in enumerate(d))
        if f.event == 0x01:
            return f"{ch} roll " + " ".join(f"r{i + 1}:{dirs.get(b, hex(b))}" for i, b in enumerate(d))
        if f.event == 0x02:
            return f"{ch} faders " + " ".join(f"f{i + 1}:{b}" for i, b in enumerate(d))
        if f.event == 0x03:
            return f"{ch} press " + " ".join(f"k{i + 1}:{'down' if b else 'up'}" for i, b in enumerate(d))
    if mtype.pid == 0x0206 and f.channel == 0 and f.event == 0x03 and d:
        return f"{ch} dial turn dir={d[0]:02x}"
    if mtype.pid == 0x01FE and f.channel == 0 and f.event == 0x02:
        down = [str(i + 1) for i, b in enumerate(d) if b]
        return f"{ch} keys down: {', '.join(down) or 'none'}"
    return f"{ch} event 0x{f.event:02x} data {d.hex(' ')}" if f.event is not None else f"{ch} (empty)"


def cmd_listen(args):
    devices = []
    for mtype, info in _selected(args.modules):
        try:
            dev = HidModule(mtype, serial=info.get("serial_number"))
        except DeviceError as e:
            print(f"{mtype.code}: {e}")
            continue
        if args.swmode and mtype.pid in SW_MODE_CMD:
            dev.set_feature(bytes([0x03, SW_MODE_CMD[mtype.pid], 0x01]))
            print(f"{mtype.code}: software mode on")
        devices.append(dev)
    if not devices:
        sys.exit("nothing to listen to")
    print("listening to " + ", ".join(d.type.code for d in devices) + " - turn/press things. Ctrl+C to stop.")
    try:
        while True:
            for dev in devices:
                try:
                    reports = dev.read_raw()
                except DeviceError as e:
                    print(f"{dev.type.code}: {e}")
                    devices.remove(dev)
                    break
                for raw in reports:
                    f = parse_frame(raw, dev.type.tag_base)
                    if f and not args.all and dev.type.pid == 0x01F6 and f.channel == 1:
                        continue  # base heartbeat every 0.5 s
                    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    meaning = describe_frame(dev.type, f) if f else "(not a framed report)"
                    shown = raw[:4 + len(f.payload)].hex(" ") if f else _hex(raw)
                    print(f"{stamp} {dev.type.code:6} {meaning:48} raw {shown}")
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        for d in devices:
            d.close()


def _parse_hex(text: list[str]) -> bytes:
    try:
        return bytes.fromhex("".join(text).replace(":", "").replace(",", ""))
    except ValueError:
        sys.exit(f"not valid hex: {' '.join(text)}")


def cmd_send(args):
    mtype = find_type(args.module)
    data = _parse_hex(args.hex)
    kind = "feature" if args.feature else "output"
    expected = 0x03 if args.feature else 0x02
    if not data or data[0] != expected:
        sys.exit(f"{kind} reports start with report id 0x{expected:02x}")
    try:
        check_write(mtype, kind, data, args.unverified)
    except UnsafeCommand as e:
        sys.exit(f"refused: {e}")
    size = mtype.feature_len if args.feature else mtype.out_len
    print(f"{mtype.code} {kind} report ({size} bytes, zero padded): {_hex(data)}")
    if not args.yes and input("send it? [y/N] ").strip().lower() != "y":
        sys.exit("not sent")
    dev = HidModule(mtype)
    try:
        (dev.set_feature if args.feature else dev.write)(data, unverified=args.unverified)
        print("sent")
        if args.listen:
            end = time.time() + args.listen
            while time.time() < end:
                for raw in dev.read_raw():
                    print(f"  reply {_hex(raw)}")
                time.sleep(0.01)
    finally:
        dev.close()


def cmd_swmode(args):
    mtype = find_type(args.module)
    if mtype.pid not in SW_MODE_CMD:
        sys.exit(f"{mtype.code} has no known software mode command")
    dev = HidModule(mtype)
    try:
        dev.set_feature(bytes([0x03, SW_MODE_CMD[mtype.pid], 1 if args.state == "on" else 0]))
        print(f"{mtype.code}: software mode {args.state}")
    finally:
        dev.close()


def add_parser(sub):
    p = sub.add_parser("probe", description=__doc__,  # no help= : keeps it out of the main command list
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    ps = p.add_subparsers(dest="probe_cmd", required=True)
    names = ", ".join(t.aliases[0] for t in MODULE_TYPES.values())

    q = ps.add_parser("info", help="read every declared feature report")
    q.add_argument("module", nargs="?", help=names)
    q.set_defaults(fn=cmd_info)

    q = ps.add_parser("listen", help="print input frames live")
    q.add_argument("modules", nargs="*", help=names)
    q.add_argument("--swmode", action="store_true", help="turn software mode on first (screen modules)")
    q.add_argument("--all", action="store_true", help="also show the base heartbeat")
    q.set_defaults(fn=cmd_listen)

    q = ps.add_parser("send", help="send one report")
    q.add_argument("module", help=names)
    q.add_argument("hex", nargs="+", help="report bytes incl. report id, e.g. 02 2d 00 00 ff 00 00 00")
    q.add_argument("--feature", action="store_true", help="send as feature report (03 ...) instead of output (02 ...)")
    q.add_argument("--unverified", action="store_true", help="allow a command not yet known to be safe")
    q.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    q.add_argument("--listen", type=float, default=0, metavar="SEC", help="print input reports for SEC seconds after")
    q.set_defaults(fn=cmd_send)

    q = ps.add_parser("swmode", help="software mode on/off (screen modules)")
    q.add_argument("module", help=names)
    q.add_argument("state", choices=("on", "off"))
    q.set_defaults(fn=cmd_swmode)
