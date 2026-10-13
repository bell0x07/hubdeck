"""The shared WebSocket client against a fake server on localhost."""

import json
import socket
import time

import pytest

from fakes.ws_server import FakeWsServer
from hubdeck.net.websocket import WebSocket, WebSocketError, mask


@pytest.fixture
def server():
    fake = FakeWsServer()
    yield fake
    fake.close()


def pump_until(ws, count=1, timeout=2.0):
    got, deadline = [], time.monotonic() + timeout
    while len(got) < count:
        assert time.monotonic() < deadline, f"timed out with {got}"
        got += ws.pump()
        time.sleep(0.01)
    return got


def test_mask_is_its_own_inverse():
    data = bytes(range(256)) * 3 + b"xyz"
    key = b"\x01\x80\xff\x10"
    assert mask(data, key) != data
    assert mask(mask(data, key), key) == data
    assert mask(b"", key) == b""


def test_messages_both_ways(server):
    ws = WebSocket(f"{server.url}/api/websocket?x=1", peer="Fake")
    assert server.connected.wait(2)
    assert server.targets == ["/api/websocket?x=1"]
    assert server.headers[0]["host"] == f"127.0.0.1:{server.port}"
    ws.send({"type": "hello", "n": 1})
    assert server.next_message() == {"type": "hello", "n": 1}
    server.send({"type": "reply"})
    assert pump_until(ws) == [{"type": "reply"}]
    assert ws.pump() == []                                  # nothing more: doesn't block
    ws.close()


def test_large_and_fragmented_messages(server):
    ws = WebSocket(server.url)
    assert server.connected.wait(2)
    big = {"data": "x" * 100_000}
    ws.send(big)
    assert server.next_message() == big
    text = json.dumps({"parts": "joined"}).encode()
    server.send_raw(server.frame(0x1, text[:5], fin=False) + server.frame(0x0, text[5:]))
    server.send({"data": "y" * 70_000})
    got = pump_until(ws, 2)
    assert got[0] == {"parts": "joined"} and len(got[1]["data"]) == 70_000
    ws.close()


def test_ping_gets_a_pong_and_non_objects_are_skipped(server):
    ws = WebSocket(server.url)
    assert server.connected.wait(2)
    server.send_raw(server.frame(0x9, b"beat") + server.frame(0x1, b"not json") + server.frame(0x1, b"[1, 2]"))
    server.send({"ok": True})
    assert pump_until(ws) == [{"ok": True}]
    assert server.pongs.get(timeout=2) == b"beat"
    ws.close()


def test_server_going_away_raises(server):
    ws = WebSocket(server.url, peer="Fake")
    assert server.connected.wait(2)
    server.drop_client()
    with pytest.raises(WebSocketError, match="Fake"):
        pump_until(ws, timeout=2)
    ws.close()


def test_close_frame_raises(server):
    ws = WebSocket(server.url, peer="Fake")
    assert server.connected.wait(2)
    server.send_raw(server.frame(0x8, b""))
    with pytest.raises(WebSocketError, match="Fake closed the connection"):
        pump_until(ws, timeout=2)


def test_refused_unreachable_and_bad_addresses():
    fake = FakeWsServer(refuse=True)
    try:
        with pytest.raises(WebSocketError, match="Fake refused the connection: HTTP/1.1 403"):
            WebSocket(fake.url, peer="Fake")
    finally:
        fake.close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                               # nothing listens there now
    with pytest.raises(WebSocketError, match="can't reach Fake at 127.0.0.1"):
        WebSocket(f"ws://127.0.0.1:{port}", peer="Fake", timeout=1)
    with pytest.raises(WebSocketError, match="not a ws:// or wss:// address"):
        WebSocket("http://127.0.0.1/")
