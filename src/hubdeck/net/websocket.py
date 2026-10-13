"""Minimal WebSocket client (RFC 6455) for plugins: JSON text messages, ping/pong and
close, over ws:// or wss://. Single-threaded, like DiscordIPC: pump() only reads what
has already arrived, so the plugin thread that owns it never blocks on a quiet line."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import urllib.parse
from typing import Any

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HEAD = 65536          # bytes of HTTP handshake answer we wait for at most
SEND_TIMEOUT = 5          # seconds a send may block on a full socket


class WebSocketError(Exception):
    pass


def mask(data: bytes, key: bytes) -> bytes:
    """XOR `data` with the 4-byte `key`, repeated (client frames must be masked)."""
    n = len(data)
    pad = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(n, "big")


class WebSocket:
    """A connected WebSocket. `peer` names the server in errors ("Home Assistant closed
    the connection"). verify_ssl=False accepts any certificate (self-signed ones)."""

    def __init__(self, url: str, timeout: float = 5, verify_ssl: bool = True, peer: str = "the server"):
        self.peer = peer
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise WebSocketError(f"not a ws:// or wss:// address: {url!r}")
        secure = parts.scheme == "wss"
        host, port = parts.hostname, parts.port or (443 if secure else 80)
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise WebSocketError(f"can't reach {peer} at {host}:{port}: {e}") from None
        try:
            if secure:
                context = ssl.create_default_context()
                if not verify_ssl:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                sock = context.wrap_socket(sock, server_hostname=host)
            self.sock = sock
            self._buf = self._handshake(parts.netloc.rpartition("@")[2], parts.path or "/", parts.query)
        except BaseException as e:
            sock.close()
            if isinstance(e, OSError):          # including ssl.SSLError: a bad certificate
                raise WebSocketError(f"can't connect to {peer}: {e}") from None
            raise
        self.sock.setblocking(False)
        self._parts: list[bytes] = []

    def _handshake(self, host: str, path: str, query: str) -> bytes:
        """Send the upgrade request; returns what arrived after the answer's headers."""
        key = base64.b64encode(os.urandom(16))
        target = path + (f"?{query}" if query else "")
        self.sock.sendall(b"GET " + target.encode() + b" HTTP/1.1\r\nHost: " + host.encode() +
                          b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: " + key +
                          b"\r\nSec-WebSocket-Version: 13\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk or len(head) > MAX_HEAD:
                raise WebSocketError(f"{self.peer} closed the connection before answering")
            head += chunk
        head, rest = head.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if lines[0].split(" ")[1:2] != ["101"]:
            raise WebSocketError(f"{self.peer} refused the connection: {lines[0]}")
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
        if headers.get("sec-websocket-accept") != accept:
            raise WebSocketError(f"{self.peer} answered with a bad WebSocket handshake")
        return rest

    def send(self, message: Any):
        """Send `message` as a JSON text message."""
        self._frame(0x1, json.dumps(message).encode())

    def _frame(self, opcode: int, payload: bytes):
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                                    else bytes([0x80 | 127]) + struct.pack(">Q", n))
        key = os.urandom(4)
        self.sock.settimeout(SEND_TIMEOUT)
        try:
            self.sock.sendall(head + key + mask(payload, key))
        except OSError as e:
            raise WebSocketError(f"{self.peer} connection lost: {e}") from None
        finally:
            self.sock.setblocking(False)

    def pump(self) -> list[dict]:
        """The JSON objects that have arrived. Raises WebSocketError once the server has gone."""
        while True:
            try:
                chunk = self.sock.recv(65536)
            except (BlockingIOError, InterruptedError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                break
            except OSError as e:
                raise WebSocketError(f"{self.peer} connection lost: {e}") from None
            if not chunk:
                raise WebSocketError(f"{self.peer} closed the connection")
            self._buf += chunk
        messages = []
        while (frame := self._take_frame()) is not None:
            fin, opcode, payload = frame
            if opcode == 0x8:
                raise WebSocketError(f"{self.peer} closed the connection")
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
        key = b""
        if buf[1] & 0x80:                              # servers don't mask, but allow it
            key, at = buf[at:at + 4], at + 4
        if len(buf) < at + n:
            return None
        payload = buf[at:at + n]
        if key:
            payload = mask(payload, key)
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
