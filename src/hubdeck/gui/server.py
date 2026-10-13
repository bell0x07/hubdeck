"""Local web editor: a JSON API + static page on 127.0.0.1.

Every API call needs the per-run token that is embedded in the served page,
and the Host header must be localhost, so other websites can't drive it.
"""

from __future__ import annotations

import io
import json
import logging
import secrets as pysecrets
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

from .. import focus
from ..config import ConfigError, ConfigSource, load_config
from ..config.schema import ALIASES, GESTURES, LED_MODES, ControlDef, FaderDef, KeyDef
from ..render import tabler
from ..render.faces import accent_color, render
from .doc import GLOBALS, SECTIONS, ConfigDoc, to_plain

log = logging.getLogger("hubdeck.gui")

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "gui"
COUNTS = {"keys": 15, "dial": 1, "knobs": 3, "rollers": 2, "faders": 5}
FACE_SIZE = {"keys": 144, "dial": 240}


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


class Previewer:
    """Renders faces like the device does. Uses the running engine (live state) when there is one."""

    def __init__(self, engine=None):
        self.engine = engine
        self._local = None
        self._stamp = None

    def _resolver(self, cfg):
        if self.engine is not None and self.engine.cfg is not None:
            return self.engine
        from ..engine import Engine
        if self._local is None or self._stamp is not cfg:
            self._local = Engine(config=cfg, factories={})
            self._local.cfg = cfg
            self._stamp = cfg
        return self._local

    def render(self, cfg, page: str, section: str, kd, shape: str) -> bytes:
        eng = self._resolver(cfg)
        saved = eng.page_stack
        try:
            eng.page_stack = [page] if page in eng.cfg.pages else [eng.cfg.start_page]
            face = eng.face_for(kd)
            picture = None
            if face.image:
                loaded = eng.images.get(face.image)
                picture = loaded.frames[face.frame] if loaded else None
            from ..device import layout
            rotate = layout.image_rotation(cfg.modules.rotate(section))
            return _png(render(face, FACE_SIZE[section], rotate, shape, picture))
        finally:
            eng.page_stack = saved


def summary(section: str, raw: Any) -> str:
    """One-line description of a control for the overview."""
    if not isinstance(raw, dict):
        return ""
    if section == "faders":
        t = raw.get("target", "")
        return f"{t} {raw.get('arg') or ''}".strip()
    turn = raw.get("turn")
    if isinstance(turn, str):
        return turn
    if isinstance(turn, dict):
        if turn.get("target"):
            return f"{turn['target']} {turn.get('arg') or ''}".strip()
        return "turn: actions"
    face = raw.get("face") or {}
    label = face.get("label") or face.get("text") or face.get("icon") or ""
    action = raw.get("action") or raw.get("on_press") or raw.get("on_down")
    if isinstance(action, dict):
        action = next(iter(action), "")
    elif isinstance(action, list):
        action = f"{len(action)} actions"
    return " · ".join(str(x) for x in (label, action or "") if x)


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return          # browsers drop idle keep-alive connections; not worth a traceback
        super().handle_error(request, client_address)


class GuiServer:
    def __init__(self, source: ConfigSource, engine=None, port: int = 8765):
        self.source = source
        self.engine = engine
        self.doc = ConfigDoc(source.path, source.secrets)
        self.token = pysecrets.token_urlsafe(24)
        self.preview = Previewer(engine)
        self.lock = threading.Lock()
        handler = type("Handler", (_Handler,), {"app": self})
        try:
            self.httpd = _HTTPServer(("127.0.0.1", port), handler)
        except OSError:
            self.httpd = _HTTPServer(("127.0.0.1", 0), handler)   # port taken: any free one
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def start(self, open_browser: bool = True):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="hubdeck-gui")
        self.thread.start()
        log.info("editor at %s", self.url)
        if open_browser:
            webbrowser.open(self.url)

    def serve_forever(self, open_browser: bool = True):
        log.info("editor at %s", self.url)
        if open_browser:
            webbrowser.open(self.url)
        self.httpd.serve_forever()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- helpers -----------------------------------------------------------
    def config(self):
        """The current file as a validated Config, or None with the error."""
        try:
            return load_config(self.source.path, self.source.secrets), None
        except ConfigError as e:
            return None, str(e)

    def connected(self) -> list[str]:
        """Modules plugged in, plus those the running hubdeck has open. hubdeck only opens
        the modules the config uses, so a new one must show up from USB, or it couldn't be set up."""
        kinds = set(self.engine.manager.devices) if self.engine is not None else set()
        try:
            from ..device.manager import present_kinds
            kinds |= present_kinds()
        except Exception:
            pass
        return sorted(kinds)

    def layout(self, cfg, connected: list[str], used: list[str]) -> dict:
        """The Base grid and where each module sits on it, rotated, with its controls."""
        from ..device import layout as L
        orientation = cfg.device.orientation if cfg else "horizontal"
        cols, rows = L.BASE_CELLS[orientation]
        taken = [[False] * cols for _ in range(rows)]

        def fits(x, y, w, h):
            return x >= 0 and y >= 0 and x + w <= cols and y + h <= rows and \
                not any(taken[y + j][x + i] for j in range(h) for i in range(w))

        def mark(x, y, w, h):
            for j in range(h):
                for i in range(w):
                    taken[y + j][x + i] = True

        shown = [k for k in COUNTS if k in connected or k in used]
        mods, pending = {}, []
        for kind in shown:
            rot = cfg.modules.rotate(kind) if cfg else 0
            w, h = L.footprint(kind, rot)
            pos = getattr(getattr(cfg.modules, kind), "position", None) if cfg else None
            mods[kind] = {"rotate": rot, "w": w, "h": h, "controls": L.drawing(kind, rot),
                          "connected": kind in connected, "used": kind in used, "placed": bool(pos)}
            if pos and fits(pos[0], pos[1], w, h):
                mods[kind].update(x=pos[0], y=pos[1])
                mark(pos[0], pos[1], w, h)
            else:
                pending.append(kind)
        for kind in pending:                 # auto-place the rest, row by row
            m = mods[kind]
            spot = next(((x, y) for y in range(rows) for x in range(cols) if fits(x, y, m["w"], m["h"])), None)
            if spot:
                m.update(x=spot[0], y=spot[1])
                mark(spot[0], spot[1], m["w"], m["h"])
            else:
                m.update(x=None, y=None)     # doesn't fit: shown beside the base
        return {"orientation": orientation, "cols": cols, "rows": rows, "modules": mods}

    # -- API -----------------------------------------------------------------
    def api_state(self) -> dict:
        with self.lock:
            self.doc.load()
            cfg, error = self.config()
            pages = {}
            for page in self.doc.page_names():
                node = self.doc.page_node(page) or {}
                pages[page] = {}
                for section in SECTIONS:
                    sec = node.get(section)
                    if sec is None:
                        continue
                    if section == "dial":
                        pages[page][section] = {"1": summary(section, to_plain(sec))}
                    else:
                        pages[page][section] = {str(k): summary(section, to_plain(v)) for k, v in sec.items()}
            modules = {k: (cfg.modules.rotate(k) if cfg else 0) for k in COUNTS}
            connected = self.connected()
            used = sorted(cfg.used_modules()) if cfg else []
            flags = {name: {"match": [m.model_dump(exclude_none=True) for m in p.match], "sticky": p.sticky}
                     for name, p in cfg.pages.items()} if cfg else {}
            return {
                "layout": self.layout(cfg, connected, used),
                "path": str(self.source.path),
                "pages": pages,
                "page_flags": flags,
                **self.focus_state(),
                "start_page": self.doc.start_page,
                "current_page": self.engine.current_page if self.engine else None,
                "connected": connected,
                "rotate": modules,
                "used": used,
                "error": error,
                "live": self.engine is not None,
            }

    def focus_state(self) -> dict:
        """What the running hubdeck sees: the focused window, the page it matches, recent windows."""
        eng = self.engine
        if eng is None:
            return {"focus": None, "recent_windows": [], "auto_switch": None}
        window = eng.focus
        return {
            "focus": window and {"app": window.app, "title": window.title, "page": eng.focus_page},
            "recent_windows": [{"app": w.app, "title": w.title} for w in eng.recent_windows],
            "auto_switch": eng.auto_switch,
        }

    def api_page_settings(self, page: str) -> dict:
        with self.lock:
            self.doc.load()
            return {"raw": to_plain(self.doc.get_page_settings(page))}

    def api_set_page_settings(self, body: dict) -> dict:
        with self.lock:
            self.doc.load()
            if body["page"] not in self.doc.page_names():
                raise ValueError(f"no page named {body['page']!r}")
            self.doc.set_page_settings(body["page"], body.get("raw") or {})
            err = self.doc.save()
        return {"ok": err is None, "error": err}

    def api_meta(self) -> dict:
        from ..plugins import REGISTRY
        return {
            "plugins": {n: {"actions": p.actions, "states": p.states, "values": p.values}
                        for n, p in REGISTRY.items() if p.available()},
            "aliases": {k: f"{p}.{a}" for k, (p, a) in ALIASES.items()},
            "gestures": list(GESTURES),
            "led_modes": list(LED_MODES),
            "counts": COUNTS,
            "platform": sys.platform,
            "follows_focus": focus.supported(),
            "accent": "#%02x%02x%02x" % accent_color(),
        }

    def api_item(self, page: str, section: str, num) -> dict:
        with self.lock:
            self.doc.load()
            node = self.doc.get_item(page, section, num)
            return {"raw": to_plain(node), "yaml": self.doc.node_yaml(node)}

    def api_set_item(self, body: dict) -> dict:
        page, section, num = body["page"], body["section"], body.get("num")
        if section not in SECTIONS:
            raise ValueError(f"unknown section {section!r}")
        with self.lock:
            self.doc.load()
            value = self.doc.parse_yaml(body["yaml"]) if "yaml" in body else body.get("raw")
            self.doc.set_item(page, section, num, value)
            err = self.doc.save()
        return {"ok": err is None, "error": err}

    def api_global(self, name: str) -> dict:
        if name not in GLOBALS:
            raise ValueError(f"unknown section {name!r}")
        with self.lock:
            self.doc.load()
            node = self.doc.get_global(name)
            return {"raw": to_plain(node), "yaml": self.doc.node_yaml(node)}

    def api_set_global(self, body: dict) -> dict:
        name = body["name"]
        if name not in GLOBALS:
            raise ValueError(f"unknown section {name!r}")
        with self.lock:
            self.doc.load()
            value = self.doc.parse_yaml(body["yaml"]) if "yaml" in body else body.get("raw")
            self.doc.set_global(name, value)
            err = self.doc.save()
        return {"ok": err is None, "error": err}

    def api_page(self, body: dict) -> dict:
        action, name = body["action"], body["name"].strip()
        with self.lock:
            self.doc.load()
            if action == "add":
                self.doc.add_page(name)
            elif action == "rename":
                self.doc.rename_page(name, body["new"].strip())
            elif action == "delete":
                self.doc.delete_page(name)
            elif action == "start":
                self.doc.data["start_page"] = name
            err = self.doc.save()
        return {"ok": err is None, "error": err}

    def api_yaml(self) -> dict:
        with self.lock:
            return {"yaml": self.source.path.read_text(encoding="utf-8") if self.source.path.exists() else ""}

    def api_set_yaml(self, body: dict) -> dict:
        with self.lock:
            err = self.doc.save(body["yaml"])
        return {"ok": err is None, "error": err}

    def face_png(self, page: str, section: str, num, raw: dict | None = None) -> bytes:
        cfg, error = self.config()
        if cfg is None:
            raise ValueError(error)
        if raw is None:
            with self.lock:
                self.doc.load()
                raw = to_plain(self.doc.get_item(page, section, num))
        model = KeyDef if section == "keys" else ControlDef
        kd = model.model_validate(raw) if raw else None
        if kd is not None:
            for face in kd.faces():   # relative images resolve next to the config, like the loader does
                if face.image and "{" not in face.image and not face.image.lower().startswith(("http://", "https://")):
                    p = Path(face.image).expanduser()
                    face.image = str(p if p.is_absolute() else self.source.path.parent / p)
        return self.preview.render(cfg, page, section, kd, "round" if section == "dial" else "square")

    def audio_info(self) -> dict:
        """Apps currently holding an audio session and the active playback and recording devices, for pickers."""
        out = {"apps": [], "outputs": [], "inputs": []}

        def work():
            import warnings
            warnings.simplefilter("ignore")    # pycaw warns about devices with missing properties
            try:
                from ..platforms import current
                backend = current().audio()
                try:
                    out["apps"] = backend.apps()
                    out["outputs"] = backend.devices("speakers")
                    out["inputs"] = backend.devices("microphone")
                finally:
                    backend.close()
            except Exception:
                log.debug("audio info failed", exc_info=True)

        t = threading.Thread(target=work, daemon=True)   # COM needs its own initialised thread
        t.start()
        t.join(5)
        return out

    def shortcut_names(self) -> dict:
        """The user's Shortcuts (macOS), for the picker; empty elsewhere or on failure."""
        from ..platforms import current
        try:
            return {"names": current().shortcut_names()}
        except Exception:
            log.debug("listing Shortcuts failed", exc_info=True)
            return {"names": []}

    def icon_png(self, name: str, filled: bool) -> bytes:
        cp = tabler.codepoint(name, filled)
        img = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        if cp is not None:
            ImageDraw.Draw(img).text((24, 24), chr(cp), font=tabler.font(40, filled), fill="white", anchor="mm")
        return _png(img)


class _Handler(BaseHTTPRequestHandler):
    app: GuiServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("gui: " + fmt, *args)

    # -- plumbing ----------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _allowed(self, need_token: bool) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            return False
        if need_token:
            q = parse_qs(urlparse(self.path).query)
            token = self.headers.get("X-Hubdeck-Token") or (q.get("t") or [""])[0]
            if not pysecrets.compare_digest(token, self.app.token):
                self._send(HTTPStatus.FORBIDDEN, b"bad token", "text/plain")
                return False
        return True

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            if not self._allowed(False):
                return
            html = (ASSETS / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.app.token)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if url.path in ("/app.js", "/style.css"):
            if not self._allowed(False):
                return
            ctype = "text/javascript" if url.path.endswith(".js") else "text/css"
            return self._send(200, (ASSETS / url.path[1:]).read_bytes(), f"{ctype}; charset=utf-8")
        if not self._allowed(True):
            return
        try:
            a = self.app
            if url.path == "/api/state":
                return self._json(a.api_state())
            if url.path == "/api/meta":
                return self._json(a.api_meta())
            if url.path == "/api/item":
                return self._json(a.api_item(q["page"], q["section"], q.get("num")))
            if url.path == "/api/global":
                return self._json(a.api_global(q["name"]))
            if url.path == "/api/page-settings":
                return self._json(a.api_page_settings(q["page"]))
            if url.path == "/api/yaml":
                return self._json(a.api_yaml())
            if url.path == "/api/audio":
                return self._json(a.audio_info())
            if url.path == "/api/shortcuts":
                return self._json(a.shortcut_names())
            if url.path == "/api/icons":
                hits = tabler.search(q.get("q", ""), q.get("filled") == "1")[:150]
                return self._json({"icons": hits})
            if url.path == "/api/icon.png":
                return self._send(200, a.icon_png(q.get("name", ""), q.get("filled") == "1"), "image/png")
            if url.path == "/api/face.png":
                return self._send(200, a.face_png(q["page"], q["section"], q.get("num")), "image/png")
        except Exception as e:
            log.debug("gui GET %s failed", url.path, exc_info=True)
            return self._json({"ok": False, "error": str(e)}, 400)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        if not self._allowed(True):
            return
        try:
            body = self._body()
            a = self.app
            if url.path == "/api/item":
                return self._json(a.api_set_item(body))
            if url.path == "/api/global":
                return self._json(a.api_set_global(body))
            if url.path == "/api/page":
                return self._json(a.api_page(body))
            if url.path == "/api/page-settings":
                return self._json(a.api_set_page_settings(body))
            if url.path == "/api/yaml":
                return self._json(a.api_set_yaml(body))
            if url.path == "/api/to-yaml":      # unsaved form state -> YAML text for the YAML tab
                from .doc import from_plain
                return self._json({"yaml": a.doc.node_yaml(from_plain(body.get("raw")) if body.get("raw") else None)})
            if url.path == "/api/preview.png":
                return self._send(200, a.face_png(body["page"], body["section"], body.get("num"), body.get("raw") or {}),
                                  "image/png")
        except Exception as e:
            log.debug("gui POST %s failed", url.path, exc_info=True)
            msg = str(e)
            if hasattr(e, "errors"):   # pydantic: short, readable
                msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            return self._json({"ok": False, "error": msg}, 400)
        self._send(404, b"not found", "text/plain")
