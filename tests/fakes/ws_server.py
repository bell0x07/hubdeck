"""A WebSocket server on localhost for plugin tests: one client at a time, JSON text
messages both ways. Subclasses answer messages in `on_message` (the server's thread)."""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import socket
import struct
import threading

from hubdeck.net.websocket import GUID, mask


class FakeWsServer:
    """Records each handshake's request target in `targets` and every message the client
    sends in `received`; pongs go to `pongs`. refuse=True answers 403 instead of upgrading."""

    def __init__(self, refuse: bool = False):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}"
        self.refuse = refuse
        self.targets: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.received: "queue.Queue[dict]" = queue.Queue()
        self.pongs: "queue.Queue[bytes]" = queue.Queue()
        self.conn: socket.socket | None = None
        self.connected = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    # -- override ---------------------------------------------------------
    def on_connect(self):
        """Called once the client is upgraded, before its first message."""

    def on_message(self, msg: dict):
        """Called for each message the client sends (after it's put in `received`)."""

    # -- serving ----------------------------------------------------------
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
            self.targets.append(lines[0].split(" ")[1])
            self.headers.append({k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)})
            if self.refuse:
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                conn.close()
                continue
            key = self.headers[-1]["sec-websocket-key"]
            accept = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()
            self.conn = conn                              # before answering: the client may send right away
            conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
            self.on_connect()
            self.connected.set()
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
                payload = mask(buf[at + 4:at + 4 + n], buf[at:at + 4])
                opcode, buf = buf[0] & 0x0F, buf[at + 4 + n:]
                if opcode == 0x1:
                    msg = json.loads(payload)
                    self.received.put(msg)
                    self.on_message(msg)
                elif opcode == 0xA:
                    self.pongs.put(payload)

    # -- test helpers -----------------------------------------------------
    @staticmethod
    def frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
        n = len(payload)
        size = bytes([n]) if n < 126 else (bytes([126]) + struct.pack(">H", n) if n < 65536
                                           else bytes([127]) + struct.pack(">Q", n))
        return bytes([(0x80 if fin else 0) | opcode]) + size + payload

    def send(self, message):
        self.conn.sendall(self.frame(0x1, json.dumps(message).encode()))

    def send_raw(self, data: bytes):
        self.conn.sendall(data)

    def next_message(self, timeout: float = 2) -> dict:
        return self.received.get(timeout=timeout)

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
