"""Spotify's own volume (the slider in every Spotify app, on whatever device it plays on),
over Spotify's Web API. Needs Spotify Premium and your own Spotify app (see README).

    plugins:
      spotify:
        client_id: !secret spotify_client_id
    knobs:
      2: {turn: {target: spotify.volume, step: 2}}
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import paths
from .base import ThreadedPlugin, adjusted, muted_state, value_state

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
SCOPES = "user-read-playback-state user-modify-playback-state"
REDIRECT_URI = "http://127.0.0.1:8973/callback"
POLL_SECONDS = 2        # read the volume back this often, for changes made in Spotify itself
SETTLE_SECONDS = 2      # after a change, Spotify still reports the old volume for a moment
LOGIN_SECONDS = 300     # how long the browser login may take
UNMUTE_LEVEL = 50       # unmuting goes back to the last volume above 0, or this if there was none


class SpotifyError(Exception):
    def __init__(self, message: str, status: int = 0, reason: str = "", retry_after: float = 0):
        super().__init__(message)
        self.status, self.reason, self.retry_after = status, reason, retry_after


class SpotifyAPI:
    """Web API calls with a login (authorization code with PKCE, so no client secret),
    kept in a token file and refreshed when it expires."""

    def __init__(self, client_id: str, redirect_uri: str, token_file: Path):
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_file = token_file
        try:
            self.token = json.loads(token_file.read_text())
        except (OSError, ValueError):
            self.token = None

    @property
    def logged_in(self) -> bool:
        return bool(self.token and self.token.get("refresh_token"))

    # -- login -------------------------------------------------------------
    def login(self, stopping, log) -> bool:
        """Open Spotify's consent page in the browser and wait for its redirect back to
        redirect_uri, served once on 127.0.0.1. False if it timed out or `stopping()`."""
        import webbrowser
        from http.server import BaseHTTPRequestHandler, HTTPServer

        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        redirect = urllib.parse.urlsplit(self.redirect_uri)
        reply: dict[str, str] = {}

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                if url.path != (redirect.path or "/") or query.get("state") != state:
                    self.send_error(404)
                    return
                reply.update(query)
                text = ("hubdeck can now change Spotify's volume. You can close this tab." if "code" in query
                        else f"Spotify login failed: {query.get('error')}").encode()
                self.send_response(200 if "code" in query else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(text)))
                self.end_headers()
                self.wfile.write(text)

            def log_message(self, *args):
                pass

        try:
            server = HTTPServer((redirect.hostname, redirect.port), Callback)
        except OSError as e:
            raise SpotifyError(f"can't receive the login at {self.redirect_uri}: {e}")
        server.timeout = 0.5                   # check `stopping` between requests
        try:
            webbrowser.open(f"{ACCOUNTS}/authorize?" + urllib.parse.urlencode({
                "client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_uri,
                "scope": SCOPES, "state": state, "code_challenge_method": "S256", "code_challenge": challenge}))
            log.warning("Log in to Spotify in the browser window that opened...")
            deadline = time.time() + LOGIN_SECONDS
            while not reply and time.time() < deadline and not stopping():
                server.handle_request()
        finally:
            server.server_close()
        if not reply:
            return False
        if "code" not in reply:
            raise SpotifyError(f"login refused: {reply.get('error')}")
        self._token_request({"grant_type": "authorization_code", "code": reply["code"],
                             "redirect_uri": self.redirect_uri, "code_verifier": verifier})
        return True

    def _token_request(self, fields: dict):
        req = urllib.request.Request(
            f"{ACCOUNTS}/api/token", data=urllib.parse.urlencode({"client_id": self.client_id, **fields}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise SpotifyError(f"login failed: {e.code} {e.read().decode(errors='replace')}", e.code)
        except (urllib.error.URLError, OSError) as e:
            raise SpotifyError(f"login failed: {getattr(e, 'reason', e)}")
        if not tok.get("refresh_token"):       # a refresh may keep the old one
            tok["refresh_token"] = (self.token or {}).get("refresh_token")
        tok["obtained_at"] = time.time()
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(tok))
        self.token = tok

    def _refresh(self):
        try:
            self._token_request({"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]})
        except SpotifyError as e:
            if e.status in (400, 401):         # revoked, or the app's client ID changed
                self.token = None
                self.token_file.unlink(missing_ok=True)
                raise SpotifyError("login expired", 401)
            raise

    # -- calls -------------------------------------------------------------
    def request(self, method: str, path: str, query: dict | None = None) -> dict | None:
        """One API call: its JSON reply, or None for an empty one (204)."""
        if not self.logged_in:
            raise SpotifyError("not logged in", 401)
        if time.time() > self.token.get("obtained_at", 0) + self.token.get("expires_in", 3600) - 60:
            self._refresh()
        try:
            return self._call(method, path, query)
        except SpotifyError as e:
            if e.status != 401:
                raise
        self._refresh()                        # revoked early: one more try with a fresh token
        return self._call(method, path, query)

    def _call(self, method: str, path: str, query: dict | None) -> dict | None:
        req = urllib.request.Request(
            API + path + ("?" + urllib.parse.urlencode(query) if query else ""), method=method,
            data=b"" if method != "GET" else None,          # Content-Length: 0, or Spotify answers 411
            headers={"Authorization": f"Bearer {self.token['access_token']}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read()
            return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error") or {}
            except ValueError:
                err = {}
            if not isinstance(err, dict):
                err = {"message": str(err)}
            raise SpotifyError(f"{e.code} {err.get('message') or e.reason}", e.code, err.get("reason", ""),
                               float(e.headers.get("Retry-After") or 0))
        except (urllib.error.URLError, OSError) as e:
            raise SpotifyError(f"can't reach Spotify: {getattr(e, 'reason', e)}")


def _redirect_problem(uri) -> str | None:
    u = urllib.parse.urlsplit(str(uri))
    try:
        port = u.port
    except ValueError:
        port = None
    if u.scheme != "http" or u.hostname != "127.0.0.1" or not port:
        return (f"plugins.spotify.redirect_uri must be http://127.0.0.1:<port>/<path> "
                f"(Spotify doesn't allow localhost), not {uri!r}")
    return None


class SpotifyPlugin(ThreadedPlugin):
    name = "spotify"
    interval = 0.1
    actions = {
        "volume_up": "raise Spotify's volume; argument: step in % (default 5)",
        "volume_down": "lower Spotify's volume; argument: step in % (default 5)",
        "set_volume": "set Spotify's volume; argument: 0-100",
        "toggle_mute": "mute like Spotify's own mute button: volume 0, and back to where it was",
        "mute": "set Spotify's volume to 0", "unmute": "set Spotify's volume back to where it was before 0",
    }
    states = {
        "volume": "Spotify's own volume 0-100; unknown while nothing plays on Spotify",
        "muted": "true while Spotify's volume is 0",
        "device": "the device Spotify plays on, e.g. this computer's name",
    }
    values = {
        "volume": "Spotify's own volume 0-100, the slider in the Spotify app (not the Windows mixer's)",
    }

    @classmethod
    def check_config(cls, config):
        problems = [f"plugins.spotify: unknown setting {k!r}"
                    for k in sorted(set(config) - {"client_id", "redirect_uri", "token_file"})]
        if not config.get("client_id"):
            problems.append("plugins.spotify.client_id is required (see README: Spotify setup)")
        elif str(config["client_id"]).startswith(paths.PLACEHOLDER_PREFIX):
            problems.append("plugins.spotify.client_id is still the placeholder - paste the real value into secrets.yaml")
        if "redirect_uri" in config and (err := _redirect_problem(config["redirect_uri"])):
            problems.append(err)
        return problems

    @classmethod
    def check_value(cls, name, arg):
        return "spotify.volume takes no arg" if arg is not None else None

    @classmethod
    def check_action(cls, action, arg):
        if action in ("volume_up", "volume_down") and arg is not None and not isinstance(arg, (int, float)):
            return f"spotify.{action}: argument must be a number (percent)"
        if action == "set_volume" and not (isinstance(arg, (int, float)) and 0 <= arg <= 100):
            return "spotify.set_volume needs a number 0-100"
        if action in ("toggle_mute", "mute", "unmute") and arg is not None:
            return f"spotify.{action} takes no argument"
        return None

    def setup(self):
        self.level: float | None = None
        self.unmuted = float(UNMUTE_LEVEL)     # the last volume above 0, for unmuting
        self.device: str | None = None
        self.next_poll = self.settle_until = 0.0
        self.last_error: str | None = None
        self.api = SpotifyAPI(str(self.config.get("client_id", "")),
                              str(self.config.get("redirect_uri") or REDIRECT_URI),
                              Path(self.config.get("token_file") or paths.spotify_token_path()))
        self.want_login = not self.api.logged_in   # the browser opens at start; later only when a control is used
        self._publish()

    def _publish(self):
        level = None if self.level is None else round(self.level)
        if level:
            self.unmuted = self.level
        self.ctx.set_state("volume", level)
        self.ctx.set_state("muted", None if level is None else level == 0)
        self.ctx.set_state("device", self.device)
        self.ctx.set_state(value_state("volume", None), level)
        self.ctx.set_state(muted_state("volume", None), None if level is None else level == 0)

    def _fail(self, e: SpotifyError):
        if e.status == 429:
            self.next_poll = time.time() + max(e.retry_after, POLL_SECONDS)
        message = {
            "PREMIUM_REQUIRED": "Spotify only lets Premium accounts change the volume",
            "VOLUME_CONTROL_DISALLOW": "the device Spotify plays on doesn't let hubdeck change its volume",
            "NO_ACTIVE_DEVICE": "nothing is playing on Spotify, so there's no volume to change",
        }.get(e.reason) or f"Spotify: {e}"
        if not self.api.logged_in:
            message += " - use a Spotify control to log in again"
        if message != self.last_error:         # once, not on every poll
            self.log.warning("%s", message)
            self.last_error = message

    def tick(self):
        if not self.api.logged_in:
            if self.want_login and self.api.client_id:
                self.want_login = False
                self._login()
            return
        if time.time() >= self.next_poll:
            self._poll()

    def _login(self):
        try:
            if self.api.login(lambda: self.stopping, self.log):
                self.log.info("logged in to Spotify")
                self.last_error = None
                self.next_poll = 0.0
            elif not self.stopping:
                self.log.warning("no Spotify login - use a Spotify control to try again")
        except SpotifyError as e:
            self._fail(e)

    def _poll(self):
        self.next_poll = time.time() + POLL_SECONDS
        try:
            player = self.api.request("GET", "/me/player") or {}     # empty: nothing playing
        except SpotifyError as e:
            self._fail(e)
            return
        device = player.get("device") or {}
        if time.time() >= self.settle_until:
            self.level = device.get("volume_percent")
        if self.level is not None:
            self.last_error = None
        self.device = device.get("name")
        self._publish()

    def _ready(self) -> bool:
        """Logged in and with a volume to change; a control used before logging in starts the login."""
        if not self.api.logged_in:
            self.want_login = True
            return False
        if self.level is None and time.time() >= self.next_poll:
            self._poll()
        if self.level is None and self.last_error is None:
            self._fail(SpotifyError("", reason="NO_ACTIVE_DEVICE"))
        return self.level is not None

    def _set(self, level: float):
        self.level = max(0.0, min(100.0, level))
        self.settle_until = time.time() + SETTLE_SECONDS
        self._publish()
        try:
            self.api.request("PUT", "/me/player/volume", {"volume_percent": round(self.level)})
            self.last_error = None
        except SpotifyError as e:
            self.settle_until = self.next_poll = 0.0                 # show the real volume again
            self._fail(e)

    def handle(self, action, arg):
        if not self._ready():
            return
        if action == "set_volume":
            self._set(float(arg))
        elif action in ("toggle_mute", "mute", "unmute"):
            mute = {"mute": True, "unmute": False}.get(action, round(self.level) > 0)
            if mute != (round(self.level) == 0):
                self._set(0 if mute else self.unmuted)
        else:
            step = float(arg if arg is not None else 5)
            self._set(self.level + (step if action == "volume_up" else -step))

    def adjust_value(self, name, arg, delta, expo):
        if self._ready():
            self._set(adjusted(self.level, delta, expo))

    def set_value(self, name, arg, value):
        if self._ready():
            self._set(value)
