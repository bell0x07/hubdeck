"""Spotify plugin against a fake Spotify: urlopen answers for the accounts and Web API endpoints."""

import base64
import hashlib
import http.client
import io
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import pytest

from hubdeck.plugins import PluginContext
from hubdeck.plugins.spotify import SpotifyPlugin


class FakeSpotify:
    """urlopen stand-in. The login's refresh token is r1. Access tokens are a1, a2, ... (one per
    refresh), and only the latest one works."""

    def __init__(self):
        self.volume, self.device = 40, "DESKTOP"
        self.fail: dict[tuple[str, str], tuple[int, dict]] = {}   # (method, path) -> (status, error body)
        self.issued, self.access = 1, "a1"
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, req, timeout=None):
        url = urllib.parse.urlsplit(req.full_url)
        method, query = req.get_method(), {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.netloc == "accounts.spotify.com":
            fields = {k: v[0] for k, v in urllib.parse.parse_qs(req.data.decode()).items()}
            self.calls.append(("TOKEN", fields["grant_type"], fields))
            if fields["grant_type"] == "authorization_code":
                return self._reply(req, 200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600})
            if fields.get("refresh_token") != "r1":
                return self._reply(req, 400, {"error": "invalid_grant"})
            self.issued += 1
            self.access = f"a{self.issued}"
            return self._reply(req, 200, {"access_token": self.access, "expires_in": 3600})   # keeps r1
        self.calls.append((method, url.path, query))
        if (method, url.path) in self.fail:
            return self._reply(req, *self.fail[(method, url.path)])
        if req.get_header("Authorization") != f"Bearer {self.access}":
            return self._reply(req, 401, {"error": {"status": 401, "message": "The access token expired"}})
        if method == "PUT":
            assert req.data == b""                                 # Content-Length: 0
            self.volume = int(query["volume_percent"])
            return self._reply(req, 204, None)
        if self.device is None:
            return self._reply(req, 204, None)                     # nothing playing
        return self._reply(req, 200, {"device": {"name": self.device, "volume_percent": self.volume}})

    @staticmethod
    def _reply(req, status, body):
        data = json.dumps(body).encode() if body is not None else b""
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "error", {"Retry-After": "7"}, io.BytesIO(data))
        return io.BytesIO(data)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSpotify()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def plugin(tmp_path, token: dict | None = None, **config):
    token_file = tmp_path / "spotify_token.json"
    if token is not None:
        token_file.write_text(json.dumps(token))
    states = {}
    p = SpotifyPlugin(PluginContext("spotify", lambda _plugin, key, value: states.__setitem__(key, value)),
                      {"client_id": "c1", "token_file": str(token_file), **config})
    p.setup()
    p.states, p.token_file = states, token_file
    return p


LOGGED_IN = {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600}


def test_turns_set_spotifys_own_volume(fake, tmp_path):
    p = plugin(tmp_path, {**LOGGED_IN, "obtained_at": time.time()})
    p.tick()
    assert p.states == {"volume": 40, "muted": False, "device": "DESKTOP", "value:volume:": 40,
                        "muted:volume:": False}

    p.adjust_value("volume", None, 4, 0)
    assert fake.volume == 44 and p.states["volume"] == 44
    p.adjust_value("volume", None, 0.4, 0)                         # fine steps add up
    p.adjust_value("volume", None, 0.4, 0)
    assert [c[2]["volume_percent"] for c in fake.calls if c[0] == "PUT"] == ["44", "44", "45"]

    fake.volume = 41                                               # Spotify still reports the old volume
    p.next_poll = 0
    p.tick()
    assert p.states["volume"] == 45
    p.settle_until = p.next_poll = 0                               # later: changes made in Spotify show
    p.tick()
    assert p.states["volume"] == 41

    p.handle("volume_up", 70)
    assert fake.volume == 100
    p.handle("set_volume", 10)
    p.set_value("volume", None, 25)
    assert fake.volume == 25


def test_nothing_playing_has_no_volume(fake, tmp_path, caplog):
    fake.device = None
    p = plugin(tmp_path, {**LOGGED_IN, "obtained_at": time.time()})
    p.tick()
    assert p.states["volume"] is None and p.states["device"] is None
    for _ in range(3):
        p.adjust_value("volume", None, 2, 0)
    assert not [c for c in fake.calls if c[0] == "PUT"]
    assert caplog.text.count("nothing is playing on Spotify") == 1


def test_refused_change_is_logged_once_and_the_real_volume_comes_back(fake, tmp_path, caplog):
    fake.fail[("PUT", "/v1/me/player/volume")] = (403, {"error": {"status": 403, "message": "Premium required",
                                                                  "reason": "PREMIUM_REQUIRED"}})
    p = plugin(tmp_path, {**LOGGED_IN, "obtained_at": time.time()})
    p.tick()
    p.adjust_value("volume", None, 10, 0)
    p.adjust_value("volume", None, 10, 0)
    assert caplog.text.count("Premium") == 1
    p.tick()
    assert p.states["volume"] == 40


def test_expired_login_is_refreshed_and_a_revoked_one_dropped(fake, tmp_path, caplog):
    p = plugin(tmp_path, {**LOGGED_IN, "obtained_at": time.time() - 4000})
    p.tick()
    assert ("TOKEN", "refresh_token") == fake.calls[0][:2] and p.states["volume"] == 40
    saved = json.loads(p.token_file.read_text())
    assert saved["access_token"] == "a2" and saved["refresh_token"] == "r1"

    fake.access = "revoked"                                        # before it expires: refreshed on the 401
    p.next_poll, fake.volume = 0, 30
    p.tick()
    assert [c[:2] for c in fake.calls[-3:]] == [("GET", "/v1/me/player"), ("TOKEN", "refresh_token"),
                                                ("GET", "/v1/me/player")]
    assert p.states["volume"] == 30

    p.api.token["refresh_token"] = fake.access = "revoked"         # the login itself revoked
    p.next_poll = 0
    p.tick()
    assert not p.api.logged_in and not p.token_file.exists()
    assert "log in again" in caplog.text
    assert not p.want_login                                        # no browser until a control is used
    p.adjust_value("volume", None, 2, 0)
    assert p.want_login


def test_login_in_the_browser(fake, tmp_path, monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    visits, visitors = [], []

    def browser(url):                                              # the user approves in the browser
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        visits.append(query)

        def visit():
            for path in ("/favicon.ico", "/callback?code=c0de&state=wrong",
                         f"/callback?code=c0de&state={query['state']}"):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", path)
                visits.append(conn.getresponse().status)
                conn.close()
        visitors.append(threading.Thread(target=visit, daemon=True))
        visitors[0].start()
        return True
    monkeypatch.setattr(webbrowser, "open", browser)

    p = plugin(tmp_path, redirect_uri=f"http://127.0.0.1:{port}/callback")
    assert not p.api.logged_in
    p.tick()                                                       # opens the browser at start
    assert p.api.logged_in and json.loads(p.token_file.read_text())["refresh_token"] == "r1"
    visitors[0].join(5)
    authorize, *statuses = visits
    assert statuses == [404, 404, 200]
    _, grant, fields = fake.calls[0]
    assert grant == "authorization_code" and fields["code"] == "c0de"
    assert fields["redirect_uri"] == authorize["redirect_uri"] == f"http://127.0.0.1:{port}/callback"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(fields["code_verifier"].encode()).digest()).rstrip(b"=")
    assert authorize["code_challenge"] == challenge.decode() and authorize["code_challenge_method"] == "S256"
    assert set(authorize["scope"].split()) == {"user-read-playback-state", "user-modify-playback-state"}
    p.tick()
    assert p.states["volume"] == 40


def test_login_gives_up_when_stopping(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(webbrowser, "open", lambda url: True)
    p = plugin(tmp_path, redirect_uri="http://127.0.0.1:0/callback")   # any free port
    p._stop.set()
    started = time.time()
    p.tick()
    assert not p.api.logged_in and time.time() - started < 2


def test_mute_like_spotifys_own_button(fake, tmp_path):
    p = plugin(tmp_path, {**LOGGED_IN, "obtained_at": time.time()})
    p.tick()
    p.handle("toggle_mute", None)
    assert fake.volume == 0 and p.states["muted"] is True and p.states["volume"] == 0
    assert p.states["muted:volume:"] is True                       # what a knob's face reads
    p.handle("mute", None)                                         # already muted: nothing sent
    assert [c[2]["volume_percent"] for c in fake.calls if c[0] == "PUT"] == ["0"]
    p.handle("toggle_mute", None)
    assert fake.volume == 40 and p.states["muted"] is False        # back to where it was

    fake.volume = 0                                                # muted in Spotify itself
    p.settle_until = p.next_poll = 0
    p.tick()
    p.handle("unmute", None)
    assert fake.volume == 40                                       # the last volume above 0
    assert SpotifyPlugin.check_action("toggle_mute", 5) == "spotify.toggle_mute takes no argument"
