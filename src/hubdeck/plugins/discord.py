"""Discord voice: mute / deafen / hold-to-mute with live state, over Discord's
local RPC (a named pipe on Windows, a Unix socket elsewhere). Needs your own
Discord app (see README)."""

from __future__ import annotations

import json
import queue
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .. import paths
from ..platforms import IpcConnection, current
from .base import ThreadedPlugin

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4
SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write"]
RETRY_SECONDS = 5


class DiscordError(Exception):
    pass


class DiscordIPC:
    """Minimal Discord local-RPC client. Single-threaded: reads are only done
    when data is available, because a blocking read on a Windows pipe handle
    would also block writes from other threads."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, token_file: Path):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_file = token_file
        self.pipe: IpcConnection | None = None
        self.events: "queue.Queue[dict]" = queue.Queue()
        self._replies: dict[str, dict] = {}

    # -- transport ---------------------------------------------------------
    def _open(self):
        for i in range(10):
            try:
                self.pipe = current().ipc_connect(f"discord-ipc-{i}")
                return
            except OSError:
                continue
        raise DiscordError("Discord desktop app is not running (no IPC pipe found)")

    def _available(self) -> int:
        try:
            return self.pipe.available()
        except OSError:
            raise DiscordError("Discord closed the connection")

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
            raise DiscordError(f"Discord connection lost: {e}")

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
                     "User-Agent": "hubdeck (0.1)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise DiscordError(f"Token request failed: {e.code} {e.read().decode(errors='replace')}")
        except urllib.error.URLError as e:
            raise DiscordError(f"Token request failed: {e.reason}")
        tok["obtained_at"] = time.time()
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(tok))
        return tok

    def _load_token(self) -> dict | None:
        try:
            return json.loads(self.token_file.read_text())
        except (OSError, ValueError):
            return None

    def connect(self, log):
        self._open()
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
        op, ready = self._recv()
        if op == OP_CLOSE or ready.get("evt") != "READY":
            raise DiscordError(f"Handshake failed: {ready}")

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
        log.warning("Approve the authorization popup in Discord...")
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


class DiscordPlugin(ThreadedPlugin):
    name = "discord"
    interval = 0.02
    actions = {
        "toggle_mute": "toggle microphone mute",
        "mute": "mute", "unmute": "unmute",
        "toggle_deafen": "toggle deafen",
        "deafen": "deafen", "undeafen": "undeafen",
        "hold_mute_start": "mute until hold_mute_end (put in on_down)",
        "hold_mute_end": "restore the mute state from before hold_mute_start (put in on_up)",
    }
    states = {
        "muted": "true while muted or deafened; unknown while disconnected",
        "deafened": "true while deafened; unknown while disconnected",
        "holding": "true while a hold-to-mute key is held; unknown while disconnected",
        "connected": "true while connected to the Discord app",
    }

    @classmethod
    def check_config(cls, config):
        missing = [k for k in ("client_id", "client_secret") if not config.get(k)]
        extra = set(config) - {"client_id", "client_secret", "redirect_uri", "token_file"}
        problems = [f"plugins.discord.{k} is required (see README: create a Discord app)" for k in missing]
        for k in ("client_id", "client_secret"):
            if str(config.get(k, "")).startswith(paths.PLACEHOLDER_PREFIX):
                problems.append(f"plugins.discord.{k} is still the placeholder - paste the real value into secrets.yaml")
        problems += [f"plugins.discord: unknown setting {k!r}" for k in sorted(extra)]
        return problems

    def setup(self):
        self.dc: DiscordIPC | None = None
        self.next_try = 0.0
        self.mute: bool | None = None
        self.deaf: bool | None = None
        self.hold_prev: bool | None = None
        self.last_error = None
        self._publish()

    def _publish(self):
        online = self.dc is not None and self.mute is not None
        self.ctx.set_state("connected", online)
        self.ctx.set_state("muted", bool(self.mute or self.deaf) if online else None)
        self.ctx.set_state("deafened", bool(self.deaf) if online else None)
        self.ctx.set_state("holding", (self.hold_prev is not None) if online else None)

    def _drop(self, reason: str):
        if reason != self.last_error:  # don't spam the log every 5 s
            self.log.warning("%s - retrying every %ss", reason, RETRY_SECONDS)
            self.last_error = reason
        if self.dc:
            self.dc.close()
        self.dc = None
        self.mute = self.deaf = self.hold_prev = None
        self.next_try = time.time() + RETRY_SECONDS
        self._publish()

    def tick(self):
        if self.dc is None:
            if time.time() < self.next_try:
                return
            dc = DiscordIPC(str(self.config["client_id"]), str(self.config["client_secret"]),
                            self.config.get("redirect_uri", "http://localhost"),
                            Path(self.config.get("token_file") or paths.discord_token_path()))
            try:
                self.dc = dc
                dc.connect(self.log)
                vs = dc.command("GET_VOICE_SETTINGS")
                self.mute, self.deaf = vs.get("mute"), vs.get("deaf")
                dc.command("SUBSCRIBE", evt="VOICE_SETTINGS_UPDATE")
                self.log.info("connected to Discord (mute=%s deaf=%s)", self.mute, self.deaf)
                self.last_error = None
                self._publish()
            except (DiscordError, OSError) as e:
                self._drop(f"Discord: {e}")
            return
        try:
            self.dc.pump()
            changed = False
            while not self.dc.events.empty():
                ev = self.dc.events.get_nowait()
                if ev.get("evt") == "VOICE_SETTINGS_UPDATE":
                    d = ev.get("data") or {}
                    self.mute = d.get("mute", self.mute)
                    self.deaf = d.get("deaf", self.deaf)
                    changed = True
            if changed:
                self._publish()
        except DiscordError as e:
            self._drop(f"Discord disconnected: {e}")

    def _set(self, **settings):
        self.dc.command("SET_VOICE_SETTINGS", settings, timeout=5)

    def handle(self, action, arg):
        if self.dc is None or self.mute is None:
            self.log.info("%s ignored: not connected to Discord", action)
            return
        try:
            if action == "toggle_mute":
                self._set(mute=not self.mute)
            elif action in ("mute", "unmute"):
                self._set(mute=action == "mute")
            elif action == "toggle_deafen":
                self._set(deaf=not self.deaf)
            elif action in ("deafen", "undeafen"):
                self._set(deaf=action == "deafen")
            elif action == "hold_mute_start":
                if self.hold_prev is None:
                    self.hold_prev = bool(self.mute)
                    self._set(mute=True)
            elif action == "hold_mute_end":
                if self.hold_prev is not None:
                    prev, self.hold_prev = self.hold_prev, None
                    self._set(mute=prev)
            self._publish()
        except DiscordError as e:
            self._drop(f"Discord: {e}")

    def teardown(self):
        if self.dc:
            self.dc.close()
