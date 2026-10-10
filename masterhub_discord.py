"""
MasterHub <-> Discord bridge (prototype)

Shows your live Discord mute / deafen state on a key of the Cooler Master
MasterHub 15-key screen module (M4315) and toggles it when you press the key.

Protocol details come from the community reverse-engineering project
https://github.com/projectzero7/cooltek-sdk-probe (MIT). Unofficial - use at
your own risk.

Usage:
  python masterhub_discord.py --list          # show detected MasterHub HID interfaces
  python masterhub_discord.py --test          # number every key, print key presses (no Discord)
  python masterhub_discord.py                 # run the bridge (uses config.json)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import queue
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

try:
    import hid  # pip install hidapi
except ImportError:  # allow importing for tests without hidapi
    hid = None

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# MasterHub M4315 (15 keys with IPS screens)
# --------------------------------------------------------------------------

VID = 0x2516
PID_M4315 = 0x01FE
KEY_COUNT = 15
KEY_IMAGE_SIZE = 72          # the reference JPEG in cooltek-sdk-probe is 72x72
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


class MasterHubKeys:
    """Talks to the M4315 module. Opens every HID interface it exposes and
    uses whichever one accepts each kind of report (Windows splits HID
    top-level collections into separate paths)."""

    def __init__(self, path: bytes | None = None):
        if hid is None:
            raise RuntimeError("hidapi is not installed: pip install hidapi")
        infos = hid.enumerate(VID, PID_M4315)
        paths = [path] if path else [i["path"] for i in infos]
        if not paths:
            raise RuntimeError("MasterHub 15-key screen module (M4315) not found")
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
            raise RuntimeError("Found the module but could not open it (is another app holding it exclusively?)")
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
        raise RuntimeError(f"No interface accepted the report ({last})")

    def set_software_mode(self, on: bool = True):
        buf = bytearray(FEATURE_LEN)
        buf[0], buf[1], buf[2] = 0x03, 0x3A, 1 if on else 0
        with self._lock:
            self._try("_feature_dev", lambda h: h.send_feature_report(bytes(buf)) > 0)

    def set_backlight(self, brightness: int):
        buf = bytearray(FEATURE_LEN)
        buf[0], buf[1], buf[2] = 0x03, 0x34, max(0, min(100, brightness))
        with self._lock:
            self._try("_feature_dev", lambda h: h.send_feature_report(bytes(buf)) > 0)

    def set_key_image(self, index: int, jpeg: bytes):
        reports = build_image_reports(index, jpeg)
        with self._lock:
            def send_all(h):
                for r in reports:
                    if h.write(r) != len(r):
                        return False
                return True
            self._try("_write_dev", send_all)

    def poll(self) -> list[tuple[int, bool]]:
        """Return [(key_index, pressed)] edges since the last poll."""
        edges = []
        for h in self.handles:
            while True:
                try:
                    data = h.read(OUT_REPORT_LEN)
                except OSError:
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
        return edges

    def close(self):
        for h in self.handles:
            try:
                h.close()
            except Exception:
                pass


def list_interfaces():
    if hid is None:
        print("hidapi is not installed: pip install hidapi")
        return
    found = False
    for i in hid.enumerate(VID):
        found = True
        print(f"PID 0x{i['product_id']:04x}  usage_page 0x{i['usage_page']:04x}  usage 0x{i['usage']:04x}  "
              f"iface {i['interface_number']}  {i.get('product_string') or ''}\n    path {i['path']!r}")
    if not found:
        print("No Cooler Master (VID 0x2516) HID devices found.")


# --------------------------------------------------------------------------
# Key icons
# --------------------------------------------------------------------------

def _font(size: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _to_jpeg(img: Image.Image, rotate: int) -> bytes:
    if rotate:
        img = img.rotate(-rotate, expand=True)
    img = img.resize((KEY_IMAGE_SIZE, KEY_IMAGE_SIZE), Image.LANCZOS).convert("RGB")
    out = io.BytesIO()
    # baseline, 4:4:4 - matches the JPEG the vendor app sends
    img.save(out, "JPEG", quality=90, subsampling=0, progressive=False, optimize=False)
    return out.getvalue()


def _slash(d: ImageDraw.ImageDraw, s: int, bg):
    d.line([s * 0.20, s * 0.10, s * 0.80, s * 0.72], fill=bg, width=int(s * 0.13))
    d.line([s * 0.20, s * 0.10, s * 0.80, s * 0.72], fill=(255, 255, 255), width=int(s * 0.06))


def _draw_mic(d: ImageDraw.ImageDraw, s: int, color, slash):
    # s = canvas size; drawn on a 4x canvas then downscaled for smooth edges
    cx = s // 2
    w = s * 0.12
    d.rounded_rectangle([cx - w, s * 0.10, cx + w, s * 0.50], radius=w, fill=color)
    lw = int(s * 0.045)
    d.arc([cx - s * 0.28, s * 0.22, cx + s * 0.28, s * 0.62], 0, 180, fill=color, width=lw)
    d.line([cx, s * 0.62, cx, s * 0.70], fill=color, width=lw)
    d.line([cx - s * 0.12, s * 0.70, cx + s * 0.12, s * 0.70], fill=color, width=lw)
    if slash:
        _slash(d, s, slash)


def _draw_headphones(d: ImageDraw.ImageDraw, s: int, color, slash):
    lw = int(s * 0.06)
    d.arc([s * 0.20, s * 0.12, s * 0.80, s * 0.72], 180, 360, fill=color, width=lw)
    d.line([s * 0.20 + lw / 2, s * 0.42, s * 0.20 + lw / 2, s * 0.55], fill=color, width=lw)
    d.line([s * 0.80 - lw / 2, s * 0.42, s * 0.80 - lw / 2, s * 0.55], fill=color, width=lw)
    d.rounded_rectangle([s * 0.16, s * 0.45, s * 0.32, s * 0.70], radius=s * 0.04, fill=color)
    d.rounded_rectangle([s * 0.68, s * 0.45, s * 0.84, s * 0.70], radius=s * 0.04, fill=color)
    if slash:
        _slash(d, s, slash)


def make_icon(kind: str, active: bool, label: str, rotate: int = 0) -> bytes:
    """kind: 'mic' or 'deaf'. active=True means muted/deafened (red)."""
    s = KEY_IMAGE_SIZE * 4
    bg = (200, 30, 40) if active else (25, 25, 30)
    fg = (255, 255, 255) if active else (60, 200, 110)
    img = Image.new("RGB", (s, s), bg)
    d = ImageDraw.Draw(img)
    (_draw_mic if kind == "mic" else _draw_headphones)(d, s, fg, slash=bg if active else None)
    f = _font(int(s * 0.17))
    tw = d.textlength(label, font=f)
    d.text(((s - tw) / 2, s * 0.76), label, font=f, fill=(255, 255, 255))
    return _to_jpeg(img, rotate)


def make_number_icon(n: int, rotate: int = 0) -> bytes:
    s = KEY_IMAGE_SIZE * 4
    img = Image.new("RGB", (s, s), (30, 60, 140))
    d = ImageDraw.Draw(img)
    f = _font(int(s * 0.5))
    txt = str(n)
    tw = d.textlength(txt, font=f)
    d.text(((s - tw) / 2, s * 0.18), txt, font=f, fill=(255, 255, 255))
    d.text((s * 0.06, s * 0.02), "^", font=_font(int(s * 0.2)), fill=(255, 220, 0))  # orientation marker
    return _to_jpeg(img, rotate)


def load_custom_icon(path: str, rotate: int) -> bytes:
    return _to_jpeg(Image.open(path), rotate)


# --------------------------------------------------------------------------
# Discord local RPC (IPC over named pipe)
# --------------------------------------------------------------------------

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4
SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write"]


class DiscordError(Exception):
    pass


def _ipc_paths():
    if sys.platform == "win32":
        return [rf"\\?\pipe\discord-ipc-{i}" for i in range(10)]
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return [os.path.join(base, f"discord-ipc-{i}") for i in range(10)]


class DiscordIPC:
    """Minimal Discord local-RPC client. Single-threaded: reads are only done
    when data is available (PeekNamedPipe / select), because a blocking read
    on a Windows pipe handle would also block writes from other threads."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, token_file: Path):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_file = token_file
        self.pipe = None
        self._sock = None
        self.events: "queue.Queue[dict]" = queue.Queue()
        self._replies: dict[str, dict] = {}
        self.connected = False

    # -- transport ---------------------------------------------------------
    def _open(self):
        for p in _ipc_paths():
            try:
                if sys.platform == "win32":
                    self.pipe = open(p, "r+b", buffering=0)
                else:
                    import socket
                    self._sock = socket.socket(socket.AF_UNIX)
                    self._sock.connect(p)
                    self.pipe = self._sock.makefile("rwb", buffering=0)
                return
            except OSError:
                continue
        raise DiscordError("Discord desktop app is not running (no IPC pipe found)")

    def _available(self) -> int:
        if sys.platform == "win32":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            avail = wintypes.DWORD(0)
            h = msvcrt.get_osfhandle(self.pipe.fileno())
            ok = ctypes.windll.kernel32.PeekNamedPipe(
                wintypes.HANDLE(h), None, 0, None, ctypes.byref(avail), None)
            if not ok:
                raise DiscordError("Discord closed the connection")
            return avail.value
        import select
        r, _, _ = select.select([self._sock], [], [], 0)
        return 8 if r else 0

    def _send(self, op: int, payload: dict):
        data = json.dumps(payload).encode()
        self.pipe.write(struct.pack("<II", op, len(data)) + data)

    def _recv(self) -> tuple[int, dict]:
        op, length = struct.unpack("<II", self._read_exact(8))
        return op, json.loads(self._read_exact(length) or b"{}")

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.pipe.read(n - len(buf))
            if not chunk:
                raise DiscordError("Discord closed the connection")
            buf += chunk
        return buf

    def pump(self):
        """Process every frame that is already waiting. Raises DiscordError on disconnect."""
        try:
            while self._available() >= 8:
                op, msg = self._recv()
                if op == OP_PING:
                    self._send(OP_PONG, msg)
                elif op == OP_CLOSE:
                    raise DiscordError(f"Discord closed the connection: {msg}")
                elif msg.get("nonce"):
                    self._replies[msg["nonce"]] = msg
                elif msg.get("cmd") == "DISPATCH":
                    self.events.put(msg)
        except (OSError, ValueError) as e:
            self.connected = False
            raise DiscordError(f"Discord connection lost: {e}")
        except DiscordError:
            self.connected = False
            raise

    def command(self, cmd: str, args: dict | None = None, evt: str | None = None, timeout: float = 120) -> dict:
        nonce = str(uuid.uuid4())
        payload = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            payload["evt"] = evt
        self._send(OP_FRAME, payload)
        deadline = time.time() + timeout
        while nonce not in self._replies:
            if time.time() > deadline:
                raise DiscordError(f"{cmd}: no reply from Discord")
            self.pump()
            time.sleep(0.01)
        msg = self._replies.pop(nonce)
        if msg.get("evt") == "ERROR":
            raise DiscordError(f"{cmd}: {msg.get('data', {}).get('message')}")
        return msg.get("data") or {}

    # -- OAuth -------------------------------------------------------------
    def _token_request(self, fields: dict) -> dict:
        body = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            **fields,
        }).encode()
        req = urllib.request.Request(
            "https://discord.com/api/oauth2/token", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": "masterhub-discord (prototype, 0.1)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise DiscordError(f"Token request failed: {e.code} {e.read().decode(errors='replace')}")
        tok["obtained_at"] = time.time()
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(tok))
        return tok

    def _load_token(self) -> dict | None:
        try:
            return json.loads(self.token_file.read_text())
        except (OSError, ValueError):
            return None

    def connect(self):
        self._open()
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
        op, ready = self._recv()
        if op == OP_CLOSE or ready.get("evt") != "READY":
            raise DiscordError(f"Handshake failed: {ready}")
        self.connected = True

        tok = self._load_token()
        if tok and tok.get("refresh_token") and time.time() > tok.get("obtained_at", 0) + tok.get("expires_in", 0) - 300:
            try:
                tok = self._token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
            except DiscordError:
                tok = None
        if tok:
            try:
                self.command("AUTHENTICATE", {"access_token": tok["access_token"]})
                return
            except DiscordError:
                pass
        print(">> Approve the authorization popup in Discord...")
        code = self.command("AUTHORIZE", {"client_id": self.client_id, "scopes": SCOPES})["code"]
        tok = self._token_request({"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": self.redirect_uri})
        self.command("AUTHENTICATE", {"access_token": tok["access_token"]})

    def close(self):
        try:
            if self.pipe:
                self.pipe.close()
        except Exception:
            pass
        self.connected = False


# --------------------------------------------------------------------------
# Bridge
# --------------------------------------------------------------------------

def default_token_file() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "masterhub-discord" / "token.json"


def load_config(path: Path) -> dict:
    cfg = {
        "client_id": os.environ.get("DISCORD_CLIENT_ID", ""),
        "client_secret": os.environ.get("DISCORD_CLIENT_SECRET", ""),
        "redirect_uri": "http://localhost",
        "mute_key": 1,          # 1-15, as printed by --test
        "deafen_key": None,     # 1-15 or null
        "hold_mute_key": None,  # 1-15 or null; muted while held, previous state restored on release
        "rotate": 0,            # 0/90/180/270 if icons appear rotated
        "brightness": None,     # 0-100 or null to leave as is
        "icons": {},            # optional custom images: mute_on, mute_off, deaf_on, deaf_off
        "reassert_seconds": 5,  # repaint our keys this often in case MasterCanvas overwrites them (0 = off)
    }
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


class Bridge:
    def __init__(self, cfg: dict, keys: MasterHubKeys):
        self.cfg = cfg
        self.keys = keys
        self.mute_idx = cfg["mute_key"] - 1 if cfg.get("mute_key") else None
        self.deaf_idx = cfg["deafen_key"] - 1 if cfg.get("deafen_key") else None
        self.hold_idx = cfg["hold_mute_key"] - 1 if cfg.get("hold_mute_key") else None
        self.hold_prev: bool | None = None  # mute state before the hold key went down
        r =int(cfg.get("rotate") or 0)
        ic = cfg.get("icons") or {}

        def icon(name, kind, active, label):
            return load_custom_icon(ic[name], r) if ic.get(name) else make_icon(kind, active, label, r)

        self.img = {
            ("mute", True): icon("mute_on", "mic", True, "MUTED"),
            ("mute", False): icon("mute_off", "mic", False, "LIVE"),
            ("deaf", True): icon("deaf_on", "deaf", True, "DEAF"),
            ("deaf", False): icon("deaf_off", "deaf", False, "AUDIO"),
            ("hold", True): icon("hold_on", "mic", True, "HELD"),
            ("hold", False): icon("hold_off", "mic", False, "HOLD"),
            ("offline", None): make_icon("mic", True, "NO DC", r),
        }
        self.state = {"mute": None, "deaf": None}
        self.shown: dict[int, bytes] = {}

    def show(self, idx, img: bytes, force=False):
        if idx is None:
            return
        if force or self.shown.get(idx) != img:
            self.keys.set_key_image(idx, img)
            self.shown[idx] = img

    def render(self, force=False):
        if self.state["mute"] is None:
            for idx in (self.mute_idx, self.deaf_idx, self.hold_idx):
                self.show(idx, self.img[("offline", None)], force)
            return
        # Discord reports mute=true while deafened; show it as muted too
        self.show(self.mute_idx, self.img[("mute", bool(self.state["mute"] or self.state["deaf"]))], force)
        self.show(self.deaf_idx, self.img[("deaf", bool(self.state["deaf"]))], force)
        self.show(self.hold_idx, self.img[("hold", self.hold_prev is not None)], force)

    def run(self):
        self.keys.set_software_mode(True)
        if self.cfg.get("brightness") is not None:
            self.keys.set_backlight(int(self.cfg["brightness"]))
        self.render(force=True)

        token_file = Path(self.cfg.get("token_file") or default_token_file())
        dc = None
        next_try = 0.0
        last_refresh = time.time()
        while True:
            # (re)connect to Discord
            if dc is None and time.time() >= next_try:
                try:
                    dc = DiscordIPC(self.cfg["client_id"], self.cfg["client_secret"],
                                    self.cfg["redirect_uri"], token_file)
                    dc.connect()
                    vs = dc.command("GET_VOICE_SETTINGS")
                    self.state = {"mute": vs.get("mute"), "deaf": vs.get("deaf")}
                    dc.command("SUBSCRIBE", evt="VOICE_SETTINGS_UPDATE")
                    print(f"Connected to Discord. mute={self.state['mute']} deaf={self.state['deaf']}")
                    self.render()
                except (DiscordError, OSError) as e:
                    print(f"Discord: {e} - retrying in 5s")
                    if dc:
                        dc.close()
                    dc = None
                    next_try = time.time() + 5
                    self.state = {"mute": None, "deaf": None}
                    self.render()

            # Discord events
            if dc:
                try:
                    dc.pump()
                    while not dc.events.empty():
                        ev = dc.events.get_nowait()
                        if ev.get("evt") == "VOICE_SETTINGS_UPDATE":
                            d = ev.get("data") or {}
                            self.state = {"mute": d.get("mute", self.state["mute"]),
                                          "deaf": d.get("deaf", self.state["deaf"])}
                            self.render()
                except DiscordError as e:
                    print(f"Discord disconnected: {e}")
                    dc.close()
                    dc = None
                    next_try = time.time() + 5
                    self.state = {"mute": None, "deaf": None}
                    self.render()

            # key presses
            for idx, pressed in self.keys.poll():
                if dc is None:
                    self.hold_prev = None
                    continue
                try:
                    if idx == self.hold_idx:
                        if pressed and self.hold_prev is None:
                            self.hold_prev = bool(self.state["mute"])
                            dc.command("SET_VOICE_SETTINGS", {"mute": True}, timeout=5)
                        elif not pressed and self.hold_prev is not None:
                            prev, self.hold_prev = self.hold_prev, None
                            dc.command("SET_VOICE_SETTINGS", {"mute": prev}, timeout=5)
                        self.render()
                    elif not pressed:
                        continue
                    elif idx == self.mute_idx:
                        dc.command("SET_VOICE_SETTINGS", {"mute": not self.state["mute"]}, timeout=5)
                    elif idx == self.deaf_idx:
                        dc.command("SET_VOICE_SETTINGS", {"deaf": not self.state["deaf"]}, timeout=5)
                except DiscordError as e:
                    print(f"Could not toggle: {e}")

            # MasterCanvas may repaint keys; re-assert our icons every few seconds
            every = float(self.cfg.get("reassert_seconds") or 0)
            if every and time.time() - last_refresh > every:
                self.render(force=True)
                last_refresh = time.time()

            time.sleep(0.02)


def run_test(keys: MasterHubKeys, rotate: int):
    keys.set_software_mode(True)
    for i in range(KEY_COUNT):
        keys.set_key_image(i, make_number_icon(i + 1, rotate))
    print("Every key should now show its number (yellow ^ marks the top).")
    print("Press keys to see their numbers here. Ctrl+C to quit.")
    while True:
        for idx, pressed in keys.poll():
            print(f"key {idx + 1} {'down' if pressed else 'up'}")
        time.sleep(0.02)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    ap.add_argument("--list", action="store_true", help="list MasterHub HID interfaces and exit")
    ap.add_argument("--test", action="store_true", help="number every key and print presses (no Discord)")
    ap.add_argument("--rotate", type=int, default=None, help="rotate icons by 90/180/270 degrees")
    ap.add_argument("--path", default=None, help="open a specific HID path (from --list)")
    args = ap.parse_args()

    if args.list:
        list_interfaces()
        return

    cfg = load_config(Path(args.config))
    if args.rotate is not None:
        cfg["rotate"] = args.rotate

    keys = MasterHubKeys(args.path.encode() if args.path else None)
    try:
        if args.test:
            run_test(keys, int(cfg.get("rotate") or 0))
        else:
            if not cfg["client_id"] or not cfg["client_secret"]:
                sys.exit("Set client_id and client_secret in config.json (see README).")
            Bridge(cfg, keys).run()
    except KeyboardInterrupt:
        pass
    finally:
        keys.close()


if __name__ == "__main__":
    main()
