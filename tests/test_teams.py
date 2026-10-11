"""Teams plugin against a fake Teams: a WebSocket server on localhost speaking the
third-party app API (protocol 2.0.0)."""

import base64
import hashlib
import json
import queue
import socket
import struct
import threading
import time
import urllib.parse

import pytest

from hubdeck.plugins import REGISTRY
from hubdeck.plugins.base import PluginContext
from hubdeck.plugins.teams import TeamsPlugin, WebSocket

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeTeams:
    """Accepts one client at a time; records the handshake query and the messages it gets."""

    def __init__(self, refuse=False):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        self.refuse = refuse
        self.queries: list[dict] = []
        self.received: "queue.Queue[dict]" = queue.Queue()
        self.pongs: "queue.Queue[bytes]" = queue.Queue()
        self.conn = None
        self.connected = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                head += chunk
            lines = head.decode().split("\r\n")
            self.queries.append(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(lines[0].split(" ")[1]).query)))
            if self.refuse:
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                conn.close()
                continue
            key = next(l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("sec-websocket-key"))
            accept = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()
            self.conn = conn                              # before answering: the client may send right away
            self.connected.set()
            conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
            self._read(conn)

    def _read(self, conn):
        buf = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while len(buf) >= 2:
                n, at = buf[1] & 0x7F, 2
                if n == 126:
                    n, at = struct.unpack(">H", buf[2:4])[0], 4
                elif n == 127:
                    n, at = struct.unpack(">Q", buf[2:10])[0], 10
                assert buf[1] & 0x80, "clients must mask their frames"
                if len(buf) < at + 4 + n:
                    break
                mask, payload = buf[at:at + 4], buf[at + 4:at + 4 + n]
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                opcode, buf = buf[0] & 0x0F, buf[at + 4 + n:]
                if opcode == 0x1:
                    self.received.put(json.loads(payload))
                elif opcode == 0xA:
                    self.pongs.put(payload)

    def frame(self, opcode, payload: bytes, fin=True) -> bytes:
        n = len(payload)
        size = bytes([n]) if n < 126 else (bytes([126]) + struct.pack(">H", n) if n < 65536
                                           else bytes([127]) + struct.pack(">Q", n))
        return bytes([(0x80 if fin else 0) | opcode]) + size + payload

    def send(self, message: dict):
        self.conn.sendall(self.frame(0x1, json.dumps(message).encode()))

    def drop_client(self):
        self.connected.clear()
        self._hang_up(self.conn)

    def close(self):
        self.srv.close()
        if self.conn:
            self._hang_up(self.conn)

    @staticmethod
    def _hang_up(conn):
        # shutdown first: on Linux, close() alone sends nothing while another thread is in recv()
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()

    def next_message(self, timeout=2) -> dict:
        return self.received.get(timeout=timeout)


def meeting(**state):
    base = {"isMuted": False, "isVideoOn": False, "isHandRaised": False, "isInMeeting": True, "isRecordingOn": False,
            "isBackgroundBlurred": False, "isSharing": False, "hasUnreadMessages": False}
    return {"meetingUpdate": {"meetingState": base | state, "meetingPermissions": {"canToggleMute": True, "canPair": True}}}


@pytest.fixture
def teams(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBDECK_HOME", str(tmp_path))
    fakes = []

    def start(**kw):
        fake = FakeTeams(**kw)
        fakes.append(fake)
        return fake
    yield start
    for f in fakes:
        f.close()


class Driver:
    """Runs the plugin's setup / tick / handle on the test thread, recording its states."""

    def __init__(self, config):
        self.states = {}
        self.plugin = TeamsPlugin(PluginContext("teams", lambda _p, k, v: self.states.__setitem__(k, v)), config)
        self.plugin.setup()

    def until(self, check, timeout=2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.plugin.tick()
            if check():
                return
            time.sleep(0.005)
        raise AssertionError(f"timed out; states: {self.states}")


def test_registered_and_offered_everywhere():
    assert REGISTRY["teams"] is TeamsPlugin and TeamsPlugin.available()


def test_connects_unpaired_then_saves_the_token_teams_hands_out(teams, tmp_path):
    fake = teams()
    d = Driver({"port": fake.port, "token_file": str(tmp_path / "teams_token.json")})
    d.until(lambda: d.states.get("connected"))
    query = fake.queries[0]
    assert "token" not in query
    assert query["protocol-version"] == "2.0.0" and query["manufacturer"] == "hubdeck" and query["app"] == "hubdeck"
    assert d.states["paired"] is False and d.states["in_meeting"] is None and d.states["muted"] is None

    fake.send(meeting(isMuted=True))
    d.until(lambda: d.states.get("muted") is True)
    d.plugin.handle("toggle_mute", None)                  # the first key press in a meeting asks Teams to pair
    assert fake.next_message() == {"action": "toggle-mute", "parameters": {}, "requestId": 1}
    fake.send({"tokenRefresh": "secret-1"})
    d.until(lambda: d.states.get("paired"))
    assert json.loads((tmp_path / "teams_token.json").read_text()) == {"token": "secret-1"}

    fake.drop_client()                                    # next time it connects with the token
    d.until(lambda: d.states.get("connected") is False)
    assert d.states["muted"] is None and d.states["in_meeting"] is None
    d.plugin.next_try = 0
    d.until(lambda: d.states.get("connected"))
    assert fake.queries[-1]["token"] == "secret-1"
    assert fake.next_message() == {"action": "query-state", "parameters": {}, "requestId": 2}


def test_meeting_state_and_actions(teams, tmp_path):
    (tmp_path / "teams_token.json").write_text(json.dumps({"token": "t"}))
    fake = teams()
    d = Driver({"port": fake.port, "token_file": str(tmp_path / "teams_token.json")})
    d.until(lambda: d.states.get("connected"))
    assert fake.queries[0]["token"] == "t" and fake.next_message()["action"] == "query-state"

    fake.send(meeting(isInMeeting=False))
    d.until(lambda: d.states.get("in_meeting") is False)
    assert d.states["muted"] is None and d.states["video_on"] is None   # unknown outside a meeting

    fake.send(meeting(isMuted=True, isVideoOn=True, isHandRaised=True, hasUnreadMessages=True))
    d.until(lambda: d.states.get("muted") is True)
    assert d.states["video_on"] and d.states["hand_raised"] and d.states["unread"] and d.states["recording"] is False

    d.plugin.handle("mute", None)                         # already muted: nothing to send
    d.plugin.handle("unmute", None)
    d.plugin.handle("video_on", None)                     # already on
    d.plugin.handle("video_off", None)
    d.plugin.handle("react", "applause")
    d.plugin.handle("toggle_chat", None)
    d.plugin.handle("leave", None)
    sent = [fake.next_message() for _ in range(5)]
    assert [(m["action"], m["parameters"]) for m in sent] == [
        ("toggle-mute", {}), ("toggle-video", {}), ("send-reaction", {"type": "applause"}),
        ("toggle-ui", {"type": "chat"}), ("leave-call", {})]
    assert [m["requestId"] for m in sent] == [2, 3, 4, 5, 6]
    with pytest.raises(queue.Empty):
        fake.next_message(timeout=0.1)


def test_waits_for_teams_and_reconnects(teams, tmp_path, caplog):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()                                           # nothing listens there: Teams isn't running
    d = Driver({"port": port, "token_file": str(tmp_path / "teams_token.json")})
    d.plugin.tick()
    assert d.states["connected"] is False and d.states["muted"] is None
    assert "Manage API" in caplog.text and d.plugin.next_try > time.time()
    d.plugin.handle("toggle_mute", None)                  # ignored, no crash

    fake = teams(refuse=True)                             # Teams up, but the API refuses us
    d.plugin.config["port"] = fake.port
    d.plugin.next_try = 0
    d.plugin.tick()
    assert d.states["connected"] is False and "403" in caplog.text


def test_websocket_handles_big_split_and_ping_frames(teams):
    fake = teams()
    ws = WebSocket("127.0.0.1", fake.port, "/?x=1")
    assert fake.connected.wait(2)
    big = {"errorMsg": "x" * 70000}                       # 64-bit length
    text = json.dumps({"meetingUpdate": {"meetingState": {"isMuted": True}}}).encode()
    fake.conn.sendall(fake.frame(0x1, json.dumps(big).encode())
                      + fake.frame(0x9, b"hi")             # ping
                      + fake.frame(0x1, text[:10], fin=False) + fake.frame(0x0, text[10:]))
    got = []
    deadline = time.time() + 2
    while len(got) < 2 and time.time() < deadline:
        got += ws.pump()
    assert got[0] == big and got[1]["meetingUpdate"]["meetingState"]["isMuted"] is True
    assert fake.pongs.get(timeout=2) == b"hi"
    ws.send({"action": "x" * 300})                        # 16-bit length, masked
    assert fake.next_message() == {"action": "x" * 300}
    fake.conn.sendall(fake.frame(0x8, b""))               # close
    with pytest.raises(Exception, match="closed"):
        deadline = time.time() + 2
        while time.time() < deadline:
            ws.pump()
    ws.close()


def test_config_and_action_checks():
    assert TeamsPlugin.check_config({}) == []
    assert TeamsPlugin.check_config({"port": 8124, "token_file": "x"}) == []
    assert TeamsPlugin.check_config({"port": "8124"}) == ["plugins.teams.port must be a port number, not '8124'"]
    assert TeamsPlugin.check_config({"api_key": 1}) == ["plugins.teams: unknown setting 'api_key'"]
    assert TeamsPlugin.check_action("react", "like") is None
    assert "applause" in TeamsPlugin.check_action("react", "clap")
    assert TeamsPlugin.check_action("toggle_mute", "now") == "teams.toggle_mute takes no argument"
