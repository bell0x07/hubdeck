"""Local control channel: Shortcuts, scripts and `hubdeck send` drive a running hubdeck.

The engine listens on 127.0.0.1 (a random port) for one JSON request per connection:

    {"token": ..., "cmd": "action", "action": "discord.toggle_mute"}      any config action
    {"token": ..., "cmd": "set", "name": "focus", "value": "Work"}       sets vars.focus
    {"token": ..., "cmd": "get", "name": "audio.volume"}                 reads a state
    {"token": ..., "cmd": "quit"}                                        stops hubdeck (installer upgrades)

and answers {"ok": true, ...} or {"ok": false, "error": "..."}. The port and a per-run
token are in control.json in the hubdeck folder, readable by this user only. Anything
that can read it could already run commands as this user, so the token is enough.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import socket
import socketserver
import threading
from pathlib import Path
from typing import Any

from . import paths
from .config.schema import parse_action

log = logging.getLogger("hubdeck.control")

MAX_REQUEST = 64 * 1024
MAX_IMAGE = 20 * 1024 * 1024
SENT_IMAGE_SIZE = 480          # px; the Dial is 240, keys 72: enough for both, small in memory


class ControlError(Exception):
    pass


def control_file() -> Path:
    return paths.app_dir() / "control.json"


class ControlServer:
    def __init__(self, engine):
        self.engine = engine
        self.token = secrets.token_urlsafe(24)
        self._server: socketserver.ThreadingTCPServer | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else 0

    def start(self):
        app = self

        class Handler(socketserver.StreamRequestHandler):
            timeout = 5

            def handle(self):
                try:
                    req = json.loads(self.rfile.readline(MAX_REQUEST) or b"null")
                    if not isinstance(req, dict) or not hmac.compare_digest(str(req.get("token", "")), app.token):
                        reply = {"ok": False, "error": "bad token (is control.json from this hubdeck?)"}
                    else:
                        reply = app.handle(req)
                except (ValueError, OSError) as e:
                    reply = {"ok": False, "error": f"bad request: {e}"}
                try:
                    self.wfile.write(json.dumps(reply, default=str).encode() + b"\n")
                except OSError:
                    pass

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, args=(0.1,), name="hubdeck-control", daemon=True).start()
        path = control_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"port": self.port, "token": self.token, "pid": os.getpid()}, f)
        os.replace(tmp, path)
        log.info("control channel on 127.0.0.1:%d (hubdeck send)", self.port)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        path = control_file()
        try:
            if json.loads(path.read_text()).get("token") == self.token:   # not a newer hubdeck's
                path.unlink()
        except (OSError, ValueError):
            pass

    def handle(self, req: dict) -> dict:
        """Answer one request (already authenticated). Runs on the server's threads: actions
        are only queued for the engine thread, and reads are single lookups, except the first
        read of a state fetched on demand (Engine.read_state)."""
        cmd = req.get("cmd")
        try:
            if cmd == "action":
                return self._action(req.get("action"))
            if cmd == "set":
                return self._action({"vars.set": {req.get("name"): req.get("value")}})
            if cmd == "get":
                name = req.get("name")
                if not isinstance(name, str) or "." not in name:
                    return {"ok": False, "error": "get needs a state like audio.volume or vars.focus"}
                value = self.engine.read_state(name)
                return {"ok": True, "value": value, "known": value is not None}
            if cmd == "quit":       # e.g. a freshly installed hubdeck-tray --replace taking over
                self.engine.stop()
                self.engine.notify("quit")
                return {"ok": True}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": f"unknown command {cmd!r}"}

    def _action(self, raw: Any) -> dict:
        action = parse_action(raw)
        if err := self.engine.check_runnable(action):
            return {"ok": False, "error": err}
        self.engine.run_action(action)
        return {"ok": True}


def sent_images_dir() -> Path:
    return paths.app_dir() / "cache" / "sent"


def save_sent_image(name: str, data: bytes) -> Path:
    """Keep a picture for `hubdeck send image <name>` and return its path. Still pictures are
    turned upright and scaled down; animated ones are kept as they are. The previous picture
    for the name is deleted. Raises ControlError."""
    from PIL import Image, ImageOps

    from .plugins.vars import check_name
    if err := check_name(name):
        raise ControlError(err)
    if not data:
        raise ControlError("no picture received (pipe one in, or give a file)")
    if len(data) > MAX_IMAGE:
        raise ControlError(f"picture larger than {MAX_IMAGE // (1024 * 1024)} MB")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ControlError("not a picture hubdeck can read (PNG, JPEG, GIF, WebP...; convert HEIC first)")
    folder = sent_images_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{name}-{hashlib.sha1(data).hexdigest()[:12]}"
    if getattr(im, "n_frames", 1) > 1:
        dest = folder / f"{stem}.{(im.format or 'gif').lower()}"
        dest.write_bytes(data)
    else:
        dest = folder / f"{stem}.png"
        im = ImageOps.exif_transpose(im)
        im.thumbnail((SENT_IMAGE_SIZE, SENT_IMAGE_SIZE))
        im.save(dest)
    for old in folder.glob(f"{name}-*"):
        if old != dest:
            old.unlink(missing_ok=True)
    return dest


def send(request: dict, timeout: float = 5) -> dict:
    """Send one request to the running hubdeck. Raises ControlError."""
    try:
        info = json.loads(control_file().read_text())
    except (OSError, ValueError):
        raise ControlError("hubdeck is not running (no control channel)")
    try:
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout) as s:
            s.sendall(json.dumps({**request, "token": info["token"]}).encode() + b"\n")
            data = s.makefile("rb").readline(MAX_REQUEST)
    except (OSError, KeyError, ValueError):
        raise ControlError("hubdeck is not running (no answer on its control channel)")
    try:
        reply = json.loads(data)
    except ValueError:
        raise ControlError("hubdeck sent an unreadable answer")
    if not reply.get("ok"):
        raise ControlError(reply.get("error") or "hubdeck refused the request")
    return reply
