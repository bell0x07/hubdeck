"""Microsoft Teams meetings: mute, camera, raise hand, reactions, leave, with live state,
over Teams' local third-party app API (a WebSocket on 127.0.0.1:8124, new Teams on Windows
and macOS). Teams must have it on: Settings > Privacy > Manage API > Enable API. The first
Teams key pressed during a meeting asks you in Teams to allow hubdeck (see README)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.parse
from pathlib import Path

from .. import __version__, paths
from .base import ThreadedPlugin

PORT = 8124
RETRY_SECONDS = 5
CLIENT = {"manufacturer": "hubdeck", "device": "MasterHub", "app": "hubdeck", "app-version": __version__}
REACTIONS = ("like", "love", "applause", "laugh", "wow")
MEETING_STATES = {   # state: field of meetingUpdate.meetingState
    "muted": "isMuted", "video_on": "isVideoOn", "hand_raised": "isHandRaised", "blurred": "isBackgroundBlurred",
    "recording": "isRecordingOn", "sharing": "isSharing", "unread": "hasUnreadMessages",
}
COMMANDS = {         # action: (Teams action, its parameters.type)
    "toggle_mute": ("toggle-mute", None), "toggle_video": ("toggle-video", None),
    "toggle_hand": ("toggle-hand", None), "toggle_blur": ("toggle-background-blur", None),
    "toggle_chat": ("toggle-ui", "chat"), "stop_sharing": ("stop-sharing", None), "leave": ("leave-call", None),
}
ONE_WAY = {          # action: (the toggle it sends, the state it needs to change)
    "mute": ("toggle_mute", "muted", False), "unmute": ("toggle_mute", "muted", True),
    "video_on": ("toggle_video", "video_on", False), "video_off": ("toggle_video", "video_on", True),
}
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class TeamsError(Exception):
    pass


class WebSocket:
    """Minimal WebSocket client (RFC 6455) for a ws:// server on this computer: text
    messages, ping/pong and close. Single-threaded, like DiscordIPC: pump() only reads
    what has already arrived."""

    def __init__(self, host: str, port: int, path: str, timeout: float = 5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16))
            self.sock.sendall(b"GET " + path.encode() + b" HTTP/1.1\r\nHost: " + f"{host}:{port}".encode() +
                              b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: " + key +
                              b"\r\nSec-WebSocket-Version: 13\r\n\r\n")
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = self.sock.recv(4096)
                if not chunk or len(head) > 65536:
                    raise TeamsError("Teams closed the connection before answering")
                head += chunk
            head, self._buf = head.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            if lines[0].split(" ")[1:2] != ["101"]:
                raise TeamsError(f"Teams refused the connection: {lines[0]}")
            headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
            accept = base64.b64encode(hashlib.sha1(key + WS_GUID).digest()).decode()
            if headers.get("sec-websocket-accept") != accept:
                raise TeamsError("Teams answered with a bad WebSocket handshake")
        except BaseException:
            self.sock.close()
            raise
        self.sock.setblocking(False)
        self._parts: list[bytes] = []

    def send(self, message: dict):
        self._frame(0x1, json.dumps(message).encode())

    def _frame(self, opcode: int, payload: bytes):
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                                    else bytes([0x80 | 127]) + struct.pack(">Q", n))
        mask = os.urandom(4)                           # clients must mask what they send
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.settimeout(5)
        try:
            self.sock.sendall(head + mask + body)
        except OSError as e:
            raise TeamsError(f"Teams connection lost: {e}")
        finally:
            self.sock.setblocking(False)

    def pump(self) -> list[dict]:
        """The JSON messages that have arrived. Raises TeamsError once Teams has gone."""
        while True:
            try:
                chunk = self.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as e:
                raise TeamsError(f"Teams connection lost: {e}")
            if not chunk:
                raise TeamsError("Teams closed the connection")
            self._buf += chunk
        messages = []
        while (frame := self._take_frame()) is not None:
            fin, opcode, payload = frame
            if opcode == 0x8:
                raise TeamsError("Teams closed the connection")
            if opcode == 0x9:
                self._frame(0xA, payload)              # ping: answer with the same payload
            elif opcode in (0x0, 0x1):                 # text, or the continuation of one
                self._parts.append(payload)
                if fin:
                    text, self._parts = b"".join(self._parts), []
                    try:
                        msg = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        messages.append(msg)
        return messages

    def _take_frame(self) -> tuple[bool, int, bytes] | None:
        buf = self._buf
        if len(buf) < 2:
            return None
        n, at = buf[1] & 0x7F, 2
        if n == 126:
            if len(buf) < 4:
                return None
            n, at = struct.unpack(">H", buf[2:4])[0], 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n, at = struct.unpack(">Q", buf[2:10])[0], 10
        mask = b""
        if buf[1] & 0x80:                              # servers don't mask, but allow it
            mask, at = buf[at:at + 4], at + 4
        if len(buf) < at + n:
            return None
        payload = buf[at:at + n]
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._buf = buf[at + n:]
        return bool(buf[0] & 0x80), buf[0] & 0x0F, payload

    def close(self):
        try:
            self._frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class TeamsPlugin(ThreadedPlugin):
    name = "teams"
    interval = 0.02
    actions = {
        "toggle_mute": "mute / unmute yourself in the meeting",
        "mute": "mute (if not muted)", "unmute": "unmute (if muted)",
        "toggle_video": "turn your camera on / off",
        "video_on": "turn your camera on", "video_off": "turn your camera off",
        "toggle_hand": "raise / lower your hand",
        "toggle_blur": "turn background blur on / off",
        "react": "send a reaction: like, love, applause, laugh or wow",
        "toggle_chat": "show / hide the meeting chat",
        "stop_sharing": "stop sharing your screen",
        "leave": "leave the meeting or call",
    }
    states = {
        "connected": "true while connected to Teams (its third-party app API is on)",
        "paired": "true once Teams has allowed hubdeck",
        "in_meeting": "true during a meeting or call; unknown while disconnected",
        "muted": "true while you're muted; unknown outside a meeting",
        "video_on": "true while your camera is on; unknown outside a meeting",
        "hand_raised": "true while your hand is raised; unknown outside a meeting",
        "blurred": "true while your background is blurred; unknown outside a meeting",
        "recording": "true while the meeting is recorded; unknown outside a meeting",
        "sharing": "true while you're sharing; unknown outside a meeting",
        "unread": "true while the meeting chat has unread messages; unknown outside a meeting",
    }

    @classmethod
    def check_config(cls, config):
        problems = [f"plugins.teams: unknown setting {k!r}" for k in sorted(set(config) - {"port", "token_file"})]
        port = config.get("port", PORT)
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            problems.append(f"plugins.teams.port must be a port number, not {port!r}")
        return problems

    @classmethod
    def check_action(cls, action, arg):
        if action == "react":
            if arg not in REACTIONS:
                return f"teams.react needs one of: {', '.join(REACTIONS)}"
        elif arg is not None:
            return f"teams.{action} takes no argument"
        return None

    def setup(self):
        self.ws: WebSocket | None = None
        self.next_try = 0.0
        self.last_error = None
        self.request_id = 0
        self.meeting: dict | None = None               # the last meetingState, None until Teams sends one
        self.token_file = Path(self.config.get("token_file") or paths.teams_token_path())
        self.token = self._load_token()
        self.asked_to_pair = False
        self._publish()

    def _load_token(self) -> str | None:
        try:
            return json.loads(self.token_file.read_text()).get("token") or None
        except (OSError, ValueError, AttributeError):
            return None

    def _save_token(self, token: str):
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps({"token": token}))

    def _publish(self):
        online = self.ws is not None
        in_meeting = self.meeting.get("isInMeeting") if online and self.meeting is not None else None
        self.ctx.set_state("connected", online)
        self.ctx.set_state("paired", bool(self.token))
        self.ctx.set_state("in_meeting", None if in_meeting is None else bool(in_meeting))
        for state, field in MEETING_STATES.items():
            self.ctx.set_state(state, bool(self.meeting.get(field)) if in_meeting else None)

    def _drop(self, reason: str):
        if reason != self.last_error:                  # don't log the same thing every 5 s
            self.log.warning("%s - retrying every %ss", reason, RETRY_SECONDS)
            self.last_error = reason
        if self.ws:
            self.ws.close()
        self.ws = None
        self.meeting = None
        self.next_try = time.time() + RETRY_SECONDS
        self._publish()

    def _connect(self):
        query = ({"token": self.token} if self.token else {}) | {"protocol-version": "2.0.0"} | CLIENT
        try:
            self.ws = WebSocket("127.0.0.1", int(self.config.get("port", PORT)), "/?" + urllib.parse.urlencode(query))
        except ConnectionRefusedError:
            self._drop("Teams: not running, or its API is off (Teams > Settings > Privacy > Manage API)")
            return
        except (TeamsError, OSError) as e:
            self._drop(f"Teams: {e}")
            return
        self.last_error = None
        if self.token:
            self.log.info("connected to Teams")
            self._send("query-state")
        else:
            self.log.info("connected to Teams, not paired yet: in a meeting, press a Teams key and allow hubdeck in Teams")
        self._publish()

    def tick(self):
        if self.ws is None:
            if time.time() >= self.next_try:
                self._connect()
            return
        try:
            messages = self.ws.pump()
        except TeamsError as e:
            self._drop(f"Teams disconnected: {e}")
            return
        for msg in messages:
            self._receive(msg)

    def _receive(self, msg: dict):
        if msg.get("tokenRefresh"):
            self.token = str(msg["tokenRefresh"])
            self._save_token(self.token)
            self.log.info("paired with Teams")
            self._publish()
        if msg.get("errorMsg"):
            self.log.info("Teams: %s", msg["errorMsg"])
        update = msg.get("meetingUpdate")
        if isinstance(update, dict) and isinstance(update.get("meetingState"), dict):
            self.meeting = update["meetingState"]
            self._publish()

    def _send(self, action: str, kind: str | None = None):
        self.request_id += 1
        self.ws.send({"action": action, "parameters": {"type": kind} if kind else {}, "requestId": self.request_id})

    def handle(self, action, arg):
        if self.ws is None:
            self.log.info("%s ignored: not connected to Teams", action)
            return
        if action in ONE_WAY:
            action, state, needs = ONE_WAY[action]
            if self.meeting is not None and bool(self.meeting.get(MEETING_STATES[state])) != needs:
                return                                 # already the way it was asked for
        if not self.token and not self.asked_to_pair:
            self.log.warning("Teams: allow hubdeck in the window Teams shows; press the key again if nothing happened")
            self.asked_to_pair = True
        try:
            if action == "react":
                self._send("send-reaction", arg)
            else:
                self._send(*COMMANDS[action])
        except TeamsError as e:
            self._drop(f"Teams: {e}")

    def teardown(self):
        if self.ws:
            self.ws.close()
