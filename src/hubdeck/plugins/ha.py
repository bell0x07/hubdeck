"""Home Assistant: switch, dim and show any entity, over Home Assistant's WebSocket API.

plugins.ha takes `url` (Home Assistant's address, as in the browser) and `token`, a
long-lived access token (Profile > Security). hubdeck subscribes only to the entities
the config uses (subscribe_entities), plus those deck.state or hubdeck send get read,
and Home Assistant pushes their changes. ha.today and ha.month subtract a counter's value
at the start of the day or month, from Home Assistant's statistics; ha.today_above adds up
today's history of an entity, then follows it live."""

from __future__ import annotations

import math
import re
import time
import urllib.parse
from datetime import date, datetime, time as day_time, timedelta, timezone
from typing import Any, Callable

from ..net.websocket import WebSocket, WebSocketError
from .base import ThreadedPlugin, adjusted, muted_state, state_key, value_state

ENTITY = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")       # also a service: light.turn_on
RETRY_SECONDS = 5
TIMEOUT = 5                 # seconds to connect and log in
PING_SECONDS = 30           # the connection is checked this often...
PONG_SECONDS = 10           # ...and dropped when Home Assistant doesn't answer in this long
SETTLE_SECONDS = 2          # after hubdeck changes a level, Home Assistant's older echoes are ignored this long
SEND_EVERY = 0.1            # seconds between level changes sent per entity; the last one always goes
MISSING_AFTER = 2           # seconds after subscribing that an entity Home Assistant hasn't sent counts as missing
WAIT_SECONDS = 2            # toggle_mute waits this long for the state of an entity nothing watched yet
REFETCH_SECONDS = 60        # statistics and history asked again this often until Home Assistant has them
PERIODS = ("today", "month", "today_above")   # states that count from the start of the day or month
UNKNOWN = ("unavailable", "unknown")
LEVEL_DOMAINS = ("light", "media_player", "cover", "fan", "climate", "number", "input_number")
SETTINGS = ("url", "token", "verify_ssl")
WATCH = "\0watch"


def domain_of(entity: str) -> str:
    return entity.split(".", 1)[0]


def is_active(entity: str, state: str | None) -> bool | None:
    """Whether Home Assistant's dashboard shows the entity as active (its stateActive), None
    while unavailable or unknown: on, open, playing, unlocked, home, heating..."""
    domain = domain_of(entity)
    if state is None or state == "unavailable":
        return None
    if domain in ("button", "event", "input_button", "scene"):
        return True                                  # their state is when they last fired
    if state == "unknown":
        return None
    if state == "off" and domain != "alert":
        return False
    inactive = {"alarm_control_panel": ("disarmed",), "alert": ("idle",), "cover": ("closed",),
                "device_tracker": ("not_home",), "person": ("not_home",), "lock": ("locked",),
                "media_player": ("standby",), "vacuum": ("idle", "docked", "paused"), "valve": ("closed",)}
    active = {"lawn_mower": ("mowing", "error"), "plant": ("problem",), "timer": ("active",),
              "camera": ("streaming",), "group": ("on", "home", "open", "locked", "problem")}
    if domain in active:
        return state in active[domain]
    return state not in inactive.get(domain, ())


def plain_value(state: str | None) -> Any:
    """An entity's state as hubdeck shows it: a number where it is one, None while unknown."""
    if state is None or state in UNKNOWN:
        return None
    if re.fullmatch(r"-?\d+", state):
        return int(state)
    number = _num(state)
    return state if number is None else number


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# -- ha.level: each domain in its own unit ("native"), shown as 0-100 -------------------
def read_native(entity: str, state: str | None, attrs: dict) -> float | None:
    domain = domain_of(entity)
    if state is None or state in UNKNOWN:
        return None
    if domain == "light":
        b = _num(attrs.get("brightness"))
        return 0.0 if state == "off" else 100.0 if b is None else b / 255 * 100
    if domain == "fan":
        p = _num(attrs.get("percentage"))
        return 0.0 if state == "off" else 100.0 if p is None else p
    if domain == "cover":
        return _num(attrs.get("current_position"))
    if domain == "media_player":
        v = _num(attrs.get("volume_level"))
        return None if v is None else v * 100
    if domain == "climate":
        return _num(attrs.get("temperature"))
    if domain in ("number", "input_number"):
        return _num(state)
    return None


def native_range(entity: str, attrs: dict) -> tuple[float, float]:
    domain = domain_of(entity)
    if domain == "climate":
        return _num(attrs.get("min_temp")) or 7.0, _num(attrs.get("max_temp")) or 35.0
    if domain in ("number", "input_number"):
        lo, hi = _num(attrs.get("min")), _num(attrs.get("max"))
        return 0.0 if lo is None else lo, 100.0 if hi is None else hi
    return 0.0, 100.0


def native_step(entity: str, attrs: dict) -> float | None:
    domain = domain_of(entity)
    if domain == "climate":
        return _num(attrs.get("target_temp_step")) or 0.5
    if domain in ("number", "input_number"):
        return _num(attrs.get("step"))
    return None


def to_level(entity: str, native: float, attrs: dict) -> float:
    lo, hi = native_range(entity, attrs)
    return 0.0 if hi <= lo else max(0.0, min(100.0, (native - lo) / (hi - lo) * 100))


def from_level(entity: str, level: float, attrs: dict) -> float:
    lo, hi = native_range(entity, attrs)
    return lo + max(0.0, min(100.0, level)) / 100 * (hi - lo)


def snapped(entity: str, native: float, attrs: dict) -> float:
    """`native` within the entity's range, on its step (climate: target_temp_step)."""
    lo, hi = native_range(entity, attrs)
    step = native_step(entity, attrs)
    if step:
        native = lo + round((native - lo) / step) * step
    return round(max(lo, min(hi, native)), 4)


def level_command(entity: str, native: float, attrs: dict) -> tuple[str, dict]:
    """The service call that sets an entity to `native`: (domain.service, service data)."""
    domain = domain_of(entity)
    if domain == "light":
        return ("light.turn_off", {}) if native < 0.5 else ("light.turn_on", {"brightness_pct": max(1, round(native))})
    if domain == "fan":
        return "fan.set_percentage", {"percentage": round(native)}
    if domain == "cover":
        return "cover.set_cover_position", {"position": round(native)}
    if domain == "media_player":
        return "media_player.volume_set", {"volume_level": round(native) / 100}
    if domain == "climate":
        return "climate.set_temperature", {"temperature": snapped(entity, native, attrs)}
    return f"{domain}.set_value", {"value": snapped(entity, native, attrs)}


def period_start(name: str) -> float:
    """When ha.<name>'s period began: local midnight, or the 1st of the month for ha.month (epoch seconds)."""
    day = date.today()
    return datetime.combine(day.replace(day=1) if name == "month" else day, day_time()).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def time_above(points: list[tuple[float, bool | None]], until: float) -> timedelta:
    """How long `points` (since, above the threshold?) were above it, up to `until`."""
    ends = [t for t, _ in points[1:]] + [until]
    return timedelta(seconds=int(sum(max(0.0, end - t) for (t, above), end in zip(points, ends) if above)))


def _entities(arg: Any) -> list[str]:
    return [arg] if isinstance(arg, str) else list(arg or [])


def _check_entities(arg: Any, what: str) -> str | None:
    items = _entities(arg) if isinstance(arg, (str, list)) else None
    if not items or not all(isinstance(e, str) and ENTITY.fullmatch(e) for e in items):
        return f"{what} needs an entity id like light.desk, or a list of them"
    return None


def ws_url(url: str) -> str:
    """http(s)://host[:port][/path] -> ws(s)://host[:port][/path]/api/websocket"""
    parts = urllib.parse.urlsplit(url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urllib.parse.urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + "/api/websocket", "", ""))


class HomeAssistantPlugin(ThreadedPlugin):
    name = "ha"
    actions = {
        "toggle": "toggle entities, e.g. {ha.toggle: light.desk} or a list of them",
        "turn_on": "turn entities on (scenes and scripts too), e.g. {ha.turn_on: scene.movie}",
        "turn_off": "turn entities off, e.g. {ha.turn_off: [light.desk, light.lamp]}",
        "call": "call any service: {ha.call: {service: light.turn_on, entity: light.desk, data: {brightness_pct: 40}}}",
        "toggle_mute": "mute or unmute a media player, e.g. {ha.toggle_mute: media_player.tv}",
    }
    states = {
        "connected": "whether Home Assistant is connected",
        "on": "whether an entity is on, or open, playing, unlocked...: ha.on[light.desk]",
        "state": "an entity's state, a number where it is one: ha.state[sensor.outside]",
        "attr": "one attribute of an entity: ha.attr[light.desk:brightness]",
        "today": "how much a counter (an energy meter) went up since midnight: ha.today[sensor.pc_energy]",
        "month": "how much a counter went up since the 1st of the month: ha.month[sensor.pc_energy]",
        "today_above": "how long today an entity was above a number: ha.today_above[sensor.pc_power:100]",
    }
    state_args = {"on": "an entity id", "state": "an entity id", "attr": "entity_id:attribute",
                  "today": "an entity id", "month": "an entity id", "today_above": "entity_id:threshold"}
    values = {
        "level": "an entity's level 0-100 (arg: the entity): light brightness, media player volume, cover "
                 "position, fan speed, number value, climate target temperature",
    }
    fetches_states = True

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        self._ws: WebSocket | None = None
        self._retry_at = 0.0
        self._rejected = False                # the token was refused: wait for a new one (a config change)
        self._last_error: str | None = None
        self._next_id = 1
        self._results: dict[int, str] = {}     # command id -> what it was, for failures
        self._sub_id: int | None = None
        self._subscribed: set[str] = set()
        self._rewatch = False
        self._extra: set[str] = set()          # entities only actions need (toggle_mute)
        self._entities: dict[str, dict] = {}   # entity -> {"s": state, "a": attributes}
        self._published: set[str] = set()
        self._missing_at: float | None = None
        self._warned_missing: set[str] = set()
        self._ping_at = 0.0
        self._pong_due: float | None = None
        self._local: dict[str, tuple[float, float]] = {}   # entity -> (native level hubdeck set, until)
        self._unsent: dict[str, float] = {}               # entity -> native level still to send
        self._sent_at: dict[str, float] = {}
        self._waiting: list[tuple[float, str]] = []        # (until, media player) for toggle_mute
        self._queries: dict[int, tuple[tuple[str, str], Callable[[dict], None]]] = {}   # id -> ((state, arg), on reply)
        self._next_ask: dict[tuple[str, str], float] = {}  # (state, arg) -> don't ask again before
        self._starts: dict[tuple[str, str], tuple[float | None, float | None]] = {}   # (today|month, entity) -> (period, value then)
        self._timelines: dict[str, list[tuple[float, bool | None]]] = {}   # today_above arg -> [(since, above?)]
        self._refresh_at = 0.0

    # -- config checks ----------------------------------------------------
    @classmethod
    def check_config(cls, config):
        problems = []
        url = config.get("url")
        if not (isinstance(url, str) and re.fullmatch(r"https?://[^/\s]+(/\S*)?", url)):
            problems.append("plugins.ha.url: Home Assistant's address as in the browser, "
                            "e.g. http://homeassistant.local:8123")
        token = config.get("token")
        if not (isinstance(token, str) and token.strip()):
            problems.append("plugins.ha.token: a long-lived access token (Home Assistant: Profile > Security), "
                            "e.g. token: !secret ha_token")
        if not isinstance(config.get("verify_ssl", True), bool):
            problems.append("plugins.ha.verify_ssl must be true or false")
        problems += [f"plugins.ha: unknown setting {k!r} (available: {', '.join(SETTINGS)})"
                     for k in sorted(set(config) - set(SETTINGS))]
        return problems

    @classmethod
    def check_state(cls, name, config, arg=None):
        if err := super().check_state(name, config, arg):
            return err
        if name in ("on", "state", "today", "month") and not ENTITY.fullmatch(arg):
            return f"ha.{name}[{arg}]: needs an entity id like light.desk"
        if name == "today_above":
            entity, _, threshold = arg.partition(":")
            if not (ENTITY.fullmatch(entity) and _num(threshold) is not None):
                return f"ha.today_above[{arg}]: needs entity_id:threshold, e.g. ha.today_above[sensor.pc_power:100]"
        if name == "attr":
            entity, _, attr = arg.partition(":")
            if not (ENTITY.fullmatch(entity) and attr):
                return f"ha.attr[{arg}]: needs entity_id:attribute, e.g. ha.attr[light.desk:brightness]"
        return None

    @classmethod
    def check_action(cls, action, arg):
        if action in ("toggle", "turn_on", "turn_off"):
            return _check_entities(arg, f"ha.{action}")
        if action == "toggle_mute":
            if not (isinstance(arg, str) and ENTITY.fullmatch(arg) and domain_of(arg) == "media_player"):
                return "ha.toggle_mute needs a media player, e.g. media_player.tv"
        if action == "call":
            if not isinstance(arg, dict) or not (isinstance(arg.get("service"), str) and ENTITY.fullmatch(arg["service"])):
                return "ha.call needs {service: domain.service, entity: ..., data: {...}}, e.g. {service: light.turn_on}"
            if unknown := set(arg) - {"service", "entity", "data"}:
                return f"ha.call: unknown field(s) {', '.join(sorted(map(str, unknown)))} (available: service, entity, data)"
            if arg.get("entity") is not None and (err := _check_entities(arg["entity"], "ha.call's entity")):
                return err
            if not isinstance(arg.get("data", {}), dict):
                return "ha.call's data must be a mapping, e.g. {brightness_pct: 40}"
        return None

    @classmethod
    def check_value(cls, name, arg):
        if not (isinstance(arg, str) and ENTITY.fullmatch(arg) and domain_of(arg) in LEVEL_DOMAINS):
            return f"ha.level needs an entity as its arg, one of: {', '.join(d + '.…' for d in LEVEL_DOMAINS)}"
        return None

    # -- what to subscribe to (engine thread) -------------------------------
    def watch(self, targets):
        super().watch(targets)
        self.invoke(WATCH, None)

    def watch_states(self, states):
        super().watch_states(states)
        self.invoke(WATCH, None)

    def _wanted(self) -> set[str]:
        out = set(self._extra)
        out.update(arg for name, arg in self.watched if name == "level" and isinstance(arg, str))
        for name, arg in self.watched_states:
            if arg:
                out.add(arg.partition(":")[0])
        return out

    # -- thread -------------------------------------------------------------
    def setup(self):
        self._set("connected", False)

    def teardown(self):
        if self._ws:
            self._ws.close()
            self._ws = None

    def tick(self):
        now = time.monotonic()
        try:
            if self._ws is None:
                if now < self._retry_at or self._rejected:
                    return
                self._connect(now)
                if self._ws is None:
                    return
            if self._rewatch:
                self._subscribe(now)
            for msg in self._ws.pump():
                self._receive(msg)
            if self._pong_due is not None and now > self._pong_due:
                raise WebSocketError("Home Assistant stopped answering")
            if now >= self._ping_at and self._pong_due is None:
                self._command({"type": "ping"})
                self._ping_at, self._pong_due = now + PING_SECONDS, now + PONG_SECONDS
            self._flush(now)
            self._expire(now)
            if now >= self._refresh_at:
                self._refresh_at = now + 1
                self._refresh(now)
        except WebSocketError as e:
            self._drop(str(e), now)

    def _connect(self, now: float):
        try:
            ws = WebSocket(ws_url(self.config["url"]), timeout=TIMEOUT,
                           verify_ssl=self.config.get("verify_ssl", True), peer="Home Assistant")
        except WebSocketError as e:
            return self._failed(str(e), now)
        try:
            self._await(ws, "auth_required")
            ws.send({"type": "auth", "access_token": self.config["token"]})
            reply = self._await(ws, "auth_ok", "auth_invalid")
        except WebSocketError as e:
            ws.close()
            return self._failed(str(e), now)
        if reply["type"] == "auth_invalid":
            ws.close()
            self._rejected = True
            self.log.error("Home Assistant refused the token (%s): make a new long-lived access token "
                           "(Profile > Security) and put it in secrets.yaml", reply.get("message", "no reason"))
            return
        self._ws, self._last_error = ws, None
        self.log.info("connected to Home Assistant %s at %s", reply.get("ha_version", ""), self.config["url"])
        self._next_id, self._results, self._sub_id = 1, {}, None
        self._ping_at, self._pong_due = now + PING_SECONDS, None
        self._set("connected", True)
        self._subscribe(now)

    def _await(self, ws: WebSocket, *types: str) -> dict:
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            for msg in ws.pump():
                if msg.get("type") in types:
                    return msg
            time.sleep(0.02)
        raise WebSocketError(f"Home Assistant didn't answer ({' or '.join(types)})")

    def _failed(self, error: str, now: float):
        if error != self._last_error:                  # each distinct error once
            self.log.warning("%s; retrying every %d s", error, RETRY_SECONDS)
            self._last_error = error
        self._retry_at = now + RETRY_SECONDS

    def _drop(self, error: str, now: float):
        if self._ws:
            self._ws.close()
        self._ws = None
        self._local.clear()
        self._unsent.clear()
        self._waiting.clear()
        self._entities.clear()
        for kept in (self._queries, self._next_ask, self._starts, self._timelines):
            kept.clear()
        for key in self._published - {"connected"}:
            self.ctx.set_state(key, None)
        self._set("connected", False)
        self._failed(error, now)

    def _command(self, msg: dict, what: str | None = None) -> int:
        cid, self._next_id = self._next_id, self._next_id + 1
        self._ws.send({"id": cid, **msg})
        if what:
            self._results[cid] = what
        return cid

    def _subscribe(self, now: float):
        """Subscribe to the entities wanted now, in place of the last subscription."""
        self._rewatch = False
        wanted = self._wanted()
        if wanted != self._subscribed or self._sub_id is None:
            if self._sub_id is not None:
                self._command({"type": "unsubscribe_events", "subscription": self._sub_id})
                self._sub_id = None
            self._subscribed = wanted
            if wanted:
                self._sub_id = self._command({"type": "subscribe_entities", "entity_ids": sorted(wanted)},
                                             "subscribing to Home Assistant entities")
                self._missing_at = now + MISSING_AFTER
        for entity in wanted:                          # states newly shown for entities already here
            self._publish(entity)

    def _receive(self, msg: dict):
        kind = msg.get("type")
        if kind == "event" and msg.get("id") == self._sub_id and self._sub_id is not None:
            self._changed(msg.get("event") or {})
        elif kind == "result" and msg.get("id") in self._queries:
            (name, arg), then = self._queries.pop(msg["id"])
            if msg.get("success"):
                then(msg.get("result") or {})
            else:
                self.log.warning("ha.%s[%s]: Home Assistant didn't answer: %s", name, arg,
                                 (msg.get("error") or {}).get("message", "no reason"))
        elif kind == "result":
            what = self._results.pop(msg.get("id"), None)
            if what and not msg.get("success"):
                self.log.warning("%s failed: %s", what, (msg.get("error") or {}).get("message", "no reason"))
        elif kind == "pong":
            self._pong_due = None

    def _changed(self, event: dict):
        """Apply subscribe_entities' compressed states: a = added, c = changed (+/-), r = removed."""
        changed, stamps = set(), {}                   # stamps: entity -> when its state changed
        for entity, st in (event.get("a") or {}).items():
            self._entities[entity] = {"s": st.get("s"), "a": dict(st.get("a") or {})}
            changed.add(entity)
            stamps[entity] = st.get("lc") or st.get("lu")
        for entity, diff in (event.get("c") or {}).items():
            cur = self._entities.setdefault(entity, {"s": None, "a": {}})
            plus = diff.get("+") or {}
            if "s" in plus:
                cur["s"] = plus["s"]
                stamps[entity] = plus.get("lc") or plus.get("lu")
            cur["a"].update(plus.get("a") or {})
            for attr in (diff.get("-") or {}).get("a") or []:
                cur["a"].pop(attr, None)
            changed.add(entity)
        for entity in event.get("r") or []:
            self._entities.pop(entity, None)
            changed.add(entity)
            stamps[entity] = None
        for entity, stamp in stamps.items():
            self._track(entity, stamp or time.time())
        for entity in changed:
            self._publish(entity)
        for until, entity in list(self._waiting):
            if entity in self._entities:
                self._waiting.remove((until, entity))
                self._toggle_mute(entity)

    # -- states -------------------------------------------------------------
    def _set(self, key: str, value: Any):
        self.ctx.set_state(key, value)
        self._published.add(key)

    def _publish(self, entity: str):
        st = self._entities.get(entity)
        state, attrs = (st["s"], st["a"]) if st else (None, {})
        for name, arg in self.watched_states:
            if not arg or arg.partition(":")[0] != entity:
                continue
            if name == "on":
                self._set(state_key(name, arg), is_active(entity, state))
            elif name == "state":
                self._set(state_key(name, arg), plain_value(state))
            elif name == "attr":
                self._set(state_key(name, arg), attrs.get(arg.partition(":")[2]) if st else None)
            elif name in ("today", "month"):
                start, current = self._starts.get((name, arg), (None, None))[1], _num(state)
                self._set(state_key(name, arg), None if start is None or current is None else current - start)
            elif name == "today_above":
                points = self._timelines.get(arg)
                self._set(state_key(name, arg), None if points is None else time_above(points, time.time()))
        if ("level", entity) in self.watched:
            native = self._native(entity)
            self._set(value_state("level", entity), None if native is None else round(to_level(entity, native, attrs)))
            if domain_of(entity) == "media_player":
                self._set(muted_state("level", entity), attrs.get("is_volume_muted") if st else None)

    # -- today, month, today_above ------------------------------------------
    def _refresh(self, now: float):
        """Ask for the statistics and history the period states need, start a new day, and
        republish today_above, which grows while the entity stays above."""
        for name, arg in self.watched_states:
            if name not in PERIODS:
                continue
            key, start, entity = (name, arg), period_start(name), arg.partition(":")[0]
            asked = now < self._next_ask.get(key, 0.0) or any(k == key for k, _ in self._queries.values())
            if name == "today_above":
                points = self._timelines.get(arg)
                if points is None and not asked:
                    self._ask_history(key, start)
                elif points and points[0][0] < start:     # a new day: carry over where it stands
                    above = [a for t, a in points if t <= start][-1]
                    self._timelines[arg] = [(start, above)] + [p for p in points if p[0] > start]
            elif self._starts.get(key, (None,))[0] != start and not asked:
                self._ask_start(key, start)
            self._publish(entity)

    def _ask(self, key: tuple[str, str], msg: dict, then: Callable[[dict], None]):
        self._next_ask[key] = time.monotonic() + REFETCH_SECONDS
        self._queries[self._command(msg)] = (key, then)

    def _ask_start(self, key: tuple[str, str], start: float):
        """The counter's value when the period began: the state of the last hourly statistic
        before it. Until Home Assistant has the statistic of the hour before, an older one
        stands in, and it's asked for again."""
        name, entity = key

        def then(result: dict):
            rows = result.get(entity) or []
            if not rows and f"{name}[{entity}]" not in self._warned_missing:
                self._warned_missing.add(f"{name}[{entity}]")
                self.log.warning("ha.%s[%s]: Home Assistant has no statistics for it (it needs a state_class)",
                                 name, entity)
            value = _num(rows[-1].get("state")) if rows else None
            exact = bool(rows) and rows[-1].get("end", 0) / 1000 >= start
            self._starts[key] = (start if exact else None, value)
            self._publish(entity)

        self._ask(key, {"type": "recorder/statistics_during_period", "statistic_ids": [entity], "period": "hour",
                        "start_time": iso(start - 86400), "end_time": iso(start), "types": ["state"]}, then)

    def _ask_history(self, key: tuple[str, str], start: float):
        """Today's history of the entity, as when it went above and below the threshold."""
        arg = key[1]
        entity, _, threshold = arg.partition(":")

        def then(result: dict):
            points: list[tuple[float, bool | None]] = []
            for st in result.get(entity) or []:
                value = _num(st.get("s"))
                above = None if value is None else value > float(threshold)
                if not points or points[-1][1] != above:
                    points.append((max(start, st.get("lu") or st.get("lc") or start), above))
            self._timelines[arg] = points or [(start, None)]
            self._track(entity, time.time())          # a change that arrived meanwhile
            self._publish(entity)

        self._ask(key, {"type": "history/history_during_period", "entity_ids": [entity], "start_time": iso(start),
                        "minimal_response": True, "no_attributes": True, "significant_changes_only": False}, then)

    def _track(self, entity: str, stamp: float):
        """The entity's state goes into its today_above timelines, from `stamp` (epoch seconds) on."""
        value = _num((self._entities.get(entity) or {}).get("s"))
        for arg, points in self._timelines.items():
            entity_of, _, threshold = arg.partition(":")
            above = None if value is None else value > float(threshold)
            if entity_of == entity and points[-1][1] != above:
                points.append((max(stamp, points[-1][0]), above))

    def _native(self, entity: str) -> float | None:
        """The entity's level in its own unit: what hubdeck just set, else what Home Assistant says."""
        local = self._local.get(entity)
        if local and time.monotonic() < local[1]:
            return local[0]
        st = self._entities.get(entity)
        return read_native(entity, st["s"], st["a"]) if st else None

    def _expire(self, now: float):
        for entity, (_, until) in list(self._local.items()):
            if now >= until:
                del self._local[entity]
                self._publish(entity)                  # Home Assistant's value again
        if self._missing_at is not None and now >= self._missing_at:
            self._missing_at = None
            for entity in sorted(self._subscribed - set(self._entities) - self._warned_missing):
                self._warned_missing.add(entity)
                self.log.warning("Home Assistant has no entity %s (check its id, and that it's enabled)", entity)
        for until, entity in list(self._waiting):
            if now >= until:
                self._waiting.remove((until, entity))
                self.log.warning("ha.toggle_mute %s: Home Assistant sent no state for it", entity)

    # -- actions and levels -----------------------------------------------
    def handle(self, action, arg):
        if action == WATCH:
            self._rewatch = True
            return
        if self._ws is None:
            self.log.warning("ha.%s %s dropped: Home Assistant isn't connected", action, arg)
            return
        try:
            if action in ("toggle", "turn_on", "turn_off"):
                self._call(f"homeassistant.{action}", _entities(arg), {}, f"ha.{action} {arg}")
            elif action == "call":
                self._call(arg["service"], _entities(arg.get("entity")), dict(arg.get("data") or {}),
                           f"ha.call {arg['service']}")
            elif action == "toggle_mute":
                self._toggle_mute(arg)
        except WebSocketError as e:
            self._drop(f"{e} (ha.{action} {arg} not sent)", time.monotonic())

    def _call(self, service: str, entities: list[str], data: dict, what: str):
        domain, _, name = service.partition(".")
        msg = {"type": "call_service", "domain": domain, "service": name, "service_data": data}
        if entities:
            msg["target"] = {"entity_id": entities}
        self._command(msg, what)

    def _toggle_mute(self, entity: str):
        st = self._entities.get(entity)
        if st is None:                                 # nothing watches it yet: subscribe, then toggle
            if entity not in self._extra:
                self._extra.add(entity)
                self._rewatch = True
            self._waiting.append((time.monotonic() + WAIT_SECONDS, entity))
            return
        muted = st["a"].get("is_volume_muted")
        if not isinstance(muted, bool):
            self.log.warning("ha.toggle_mute %s: its mute isn't known (is it off?)", entity)
            return
        self._call("media_player.volume_mute", [entity], {"is_volume_muted": not muted}, f"ha.toggle_mute {entity}")

    def adjust_value(self, name, entity, delta, expo):
        st, base = self._entities.get(entity), self._native(entity)
        if self._ws is None or st is None or base is None:
            self.log.debug("ha.level %s: no level to adjust yet", entity)
            return
        attrs = st["a"]
        if domain_of(entity) == "climate":             # one of its steps per click (at the default step 2)
            native = base + delta / 2 * native_step(entity, attrs)
            lo, hi = native_range(entity, attrs)
            native = max(lo, min(hi, native))
        else:
            native = from_level(entity, adjusted(to_level(entity, base, attrs), delta, expo), attrs)
        self._change(entity, native)

    def set_value(self, name, entity, value):
        st = self._entities.get(entity)
        if self._ws is None or st is None:
            return
        self._change(entity, from_level(entity, value, st["a"]))

    def _change(self, entity: str, native: float):
        now = time.monotonic()
        self._local[entity] = (native, now + SETTLE_SECONDS)
        self._unsent[entity] = native
        self._publish(entity)
        try:
            self._flush(now)
        except WebSocketError as e:
            self._drop(str(e), now)

    def _flush(self, now: float):
        """Send pending level changes, at most one per entity every SEND_EVERY seconds."""
        for entity, native in list(self._unsent.items()):
            if now - self._sent_at.get(entity, -SEND_EVERY) >= SEND_EVERY:
                del self._unsent[entity]
                self._sent_at[entity] = now
                attrs = (self._entities.get(entity) or {}).get("a", {})
                service, data = level_command(entity, native, attrs)
                self._call(service, [entity], data, f"ha.level {entity}")
