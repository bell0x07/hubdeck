"""Home Assistant plugin against a fake Home Assistant: a WebSocket server on localhost
speaking the parts of the WebSocket API the plugin uses (auth, subscribe_entities,
call_service, ping)."""

import logging
import threading
import time

import pytest

from fakes.ws_server import FakeWsServer
from hubdeck.config import ConfigError, load_config
from hubdeck.plugins import ha as ha_mod
from hubdeck.plugins.base import PluginContext
from hubdeck.plugins.ha import HomeAssistantPlugin, is_active, plain_value, ws_url
from test_engine import make, wait_for

TOKEN = "s3cret"


class FakeHA(FakeWsServer):
    """Entities live in `entities` ({entity: (state, attrs)}). Service calls are recorded in
    `calls` and answered with success, except services named fail.*."""

    def __init__(self, entities=None, **kw):
        self.entities = dict(entities or {})
        self.calls: list[dict] = []
        self.subscriptions: list[list[str]] = []
        self.unsubscribed: list[int] = []
        self.sub_id = None
        self.answer_pings = True
        super().__init__(**kw)

    def on_connect(self):
        self.send({"type": "auth_required", "ha_version": "2026.10.1"})

    def on_message(self, msg):
        kind = msg.get("type")
        if kind == "auth":
            ok = msg.get("access_token") == TOKEN
            self.send({"type": "auth_ok" if ok else "auth_invalid", "ha_version": "2026.10.1",
                       **({} if ok else {"message": "Invalid access token or password"})})
        elif kind == "subscribe_entities":
            self.sub_id = msg["id"]
            self.subscriptions.append(msg.get("entity_ids"))
            self.send({"id": msg["id"], "type": "result", "success": True, "result": None})
            self.send({"id": msg["id"], "type": "event", "event": {"a": {
                e: {"s": s, "a": a, "c": "ctx", "lc": 1.0} for e, (s, a) in self.entities.items()
                if e in msg.get("entity_ids", [])}}})
        elif kind == "unsubscribe_events":
            self.unsubscribed.append(msg["subscription"])
            self.send({"id": msg["id"], "type": "result", "success": True, "result": None})
        elif kind == "call_service":
            self.calls.append(msg)
            ok = msg["domain"] != "fail"
            self.send({"id": msg["id"], "type": "result", "success": ok,
                       **({"result": {"context": {}}} if ok else {"error": {"code": "not_found", "message": "Service not found."}})})
        elif kind == "ping" and self.answer_pings:
            self.send({"id": msg["id"], "type": "pong"})

    def change(self, entity, state=None, attrs=None, removed=()):
        plus = {**({"s": state} if state is not None else {}), **({"a": attrs} if attrs else {})}
        diff = {"+": plus, **({"-": {"a": list(removed)}} if removed else {})}
        self.send({"id": self.sub_id, "type": "event", "event": {"c": {entity: diff}}})

    def remove(self, entity):
        self.send({"id": self.sub_id, "type": "event", "event": {"r": [entity]}})

    def services(self):
        return [(c["domain"] + "." + c["service"], c.get("target", {}).get("entity_id"), c["service_data"]) for c in self.calls]


ENTITIES = {
    "light.desk": ("on", {"brightness": 102, "friendly_name": "Desk"}),
    "light.off": ("off", {}),
    "sensor.temp": ("21.5", {"unit_of_measurement": "°C"}),
    "sensor.count": ("3", {}),
    "sensor.gone": ("unavailable", {}),
    "cover.blind": ("closed", {"current_position": 0}),
    "lock.door": ("unlocked", {}),
    "media_player.tv": ("playing", {"volume_level": 0.25, "is_volume_muted": False}),
    "climate.ac": ("cool", {"temperature": 22.0, "min_temp": 16, "max_temp": 30, "target_temp_step": 0.5}),
    "input_number.n": ("5.0", {"min": 0, "max": 10, "step": 1}),
}


class Harness:
    """A plugin connected to a fake Home Assistant, with the states it publishes."""

    def __init__(self, fake, token=TOKEN):
        self.fake = fake
        self.states = {}
        ctx = PluginContext("ha", lambda plugin, key, value: self.states.__setitem__(key, value))
        self.plugin = HomeAssistantPlugin(ctx, {"url": f"http://127.0.0.1:{fake.port}", "token": token})

    def start(self, states=(), levels=()):
        self.plugin.watch_states(set(states))
        self.plugin.watch(set(levels))
        self.plugin.start()

    def wait(self, key, value, timeout=3):
        wait_for(lambda: self.states.get(key, ...) == value, timeout)

    def wait_calls(self, n, timeout=3):
        wait_for(lambda: len(self.fake.calls) >= n, timeout)
        return self.fake.services()

    def stop(self):
        self.plugin.stop()


@pytest.fixture
def ha(monkeypatch):
    monkeypatch.setattr(ha_mod, "SEND_EVERY", 0)
    made = []

    def start(entities=ENTITIES, states=(), levels=(), token=TOKEN):
        fake = FakeHA(entities)
        h = Harness(fake, token)
        made.append(h)
        h.start(states, levels)
        return h
    yield start
    for h in made:
        h.stop()
        h.fake.close()


# -- pure helpers -----------------------------------------------------------------
def test_active_follows_the_dashboard():
    assert is_active("light.x", "on") is True and is_active("light.x", "off") is False
    assert is_active("cover.x", "closed") is False and is_active("cover.x", "opening") is True
    assert is_active("lock.x", "unlocked") is True and is_active("lock.x", "locked") is False
    assert is_active("media_player.x", "paused") is True and is_active("media_player.x", "standby") is False
    assert is_active("climate.x", "heat") is True and is_active("climate.x", "off") is False
    assert is_active("person.x", "not_home") is False and is_active("timer.x", "idle") is False
    assert is_active("scene.x", "2026-10-13T10:00:00") is True and is_active("scene.x", "unknown") is True
    assert is_active("light.x", "unavailable") is None and is_active("sensor.x", "unknown") is None


def test_plain_values_and_urls():
    assert [plain_value(s) for s in ("21.5", "3", "-4", "on", "unknown", None)] == [21.5, 3, -4, "on", None, None]
    assert ws_url("http://hassio.x") == "ws://hassio.x/api/websocket"
    assert ws_url("https://home.example.com:8443/") == "wss://home.example.com:8443/api/websocket"


# -- config checks ----------------------------------------------------------------
def test_config_needs_url_and_token(write):
    with pytest.raises(ConfigError) as e:
        load_config(write("config.yaml", "keys: {1: {action: {ha.toggle: light.desk}}}"))
    assert "plugins.ha.url" in str(e.value) and "plugins.ha.token" in str(e.value)
    with pytest.raises(ConfigError) as e:
        load_config(write("config.yaml", """
            plugins: {ha: {url: "hassio.x", token: t, port: 1, verify_ssl: maybe}}
            keys: {1: {action: {ha.toggle: light.desk}}}
        """))
    for problem in ("plugins.ha.url", "unknown setting 'port'", "verify_ssl must be true or false"):
        assert problem in str(e.value)


@pytest.mark.parametrize("key, error", [
    ('{action: {ha.toggle: Desk}}', "ha.toggle needs an entity id"),
    ('{action: {ha.turn_on: []}}', "ha.turn_on needs an entity id"),
    ('{action: {ha.toggle_mute: light.desk}}', "ha.toggle_mute needs a media player"),
    ('{action: {ha.call: light.turn_on}}', "ha.call needs \\{service"),
    ('{action: {ha.call: {service: light.turn_on, target: x}}}', "unknown field\\(s\\) target"),
    ('{action: {ha.call: {service: light.turn_on, data: 5}}}', "data must be a mapping"),
    ('{state: "ha.on[desk]", face_on: {bg: red}}', "needs an entity id like light.desk"),
    ('{face: {label: "{ha.attr[light.desk]}"}}', "needs entity_id:attribute"),
    ('{state: ha.on, face_on: {bg: red}}', "ha.on needs an entity id in brackets"),
    ('{state: "ha.connected[x]", face_on: {bg: red}}', "ha.connected takes no \\[argument\\]"),
])
def test_bad_references_are_reported(write, key, error):
    with pytest.raises(ConfigError, match=error):
        load_config(write("config.yaml", f"""
            plugins: {{ha: {{url: "http://h", token: t}}}}
            keys: {{1: {key}}}
        """))


def test_level_needs_a_supported_entity(write):
    with pytest.raises(ConfigError, match="ha.level needs an entity as its arg"):
        load_config(write("config.yaml", """
            plugins: {ha: {url: "http://h", token: t}}
            dial: {turn: {target: ha.level, arg: sensor.temp}}
        """))
    cfg = load_config(write("config.yaml", """
        plugins: {ha: {url: "https://h:8123", token: t, verify_ssl: false}}
        dial:
          turn: {target: ha.level, arg: climate.ac}
          action: {ha.call: {service: climate.toggle, entity: climate.ac}}
          face: {text: '{ha.attr[climate.ac:temperature]:.1f}°', gauge: true}
        keys:
          1: {action: {ha.toggle: [light.a, light.b]}, state: "ha.on[light.a]", face_on: {bg: red}}
    """))
    assert ("ha", "attr[climate.ac:temperature]") in cfg.state_refs()


# -- connection and states ----------------------------------------------------------
def test_subscribes_to_what_is_shown_and_publishes_it(ha):
    h = ha(states={("on", "light.desk"), ("state", "sensor.temp"), ("state", "sensor.count"),
                   ("attr", "light.desk:friendly_name"), ("on", "cover.blind"), ("on", "lock.door"),
                   ("state", "sensor.gone")},
           levels={("level", "light.desk")})
    h.wait("connected", True)
    h.wait("value:level:light.desk", 40)
    assert h.fake.subscriptions == [["cover.blind", "light.desk", "lock.door", "sensor.count", "sensor.gone", "sensor.temp"]]
    assert h.states["on[light.desk]"] is True
    assert h.states["state[sensor.temp]"] == 21.5 and h.states["state[sensor.count]"] == 3
    assert h.states["attr[light.desk:friendly_name]"] == "Desk"
    assert h.states["on[cover.blind]"] is False and h.states["on[lock.door]"] is True
    assert h.states["state[sensor.gone]"] is None


def test_changes_and_removals_are_applied(ha):
    h = ha(states={("on", "light.desk"), ("attr", "light.desk:brightness")}, levels={("level", "light.desk")})
    h.wait("value:level:light.desk", 40)
    h.fake.change("light.desk", attrs={"brightness": 255})
    h.wait("value:level:light.desk", 100)
    h.fake.change("light.desk", state="off", removed=["brightness"])
    h.wait("on[light.desk]", False)
    assert h.states["attr[light.desk:brightness]"] is None and h.states["value:level:light.desk"] == 0
    h.fake.remove("light.desk")
    h.wait("on[light.desk]", None)


def test_watching_more_resubscribes(ha):
    h = ha(states={("on", "light.desk")})
    h.wait("on[light.desk]", True)
    first = h.fake.sub_id
    h.plugin.watch_states({("on", "light.desk"), ("state", "sensor.temp")})
    h.wait("state[sensor.temp]", 21.5)
    assert h.fake.unsubscribed == [first]
    assert h.fake.subscriptions[-1] == ["light.desk", "sensor.temp"]
    h.plugin.watch_states({("on", "light.desk"), ("state", "sensor.temp"), ("attr", "sensor.temp:unit_of_measurement")})
    h.wait("attr[sensor.temp:unit_of_measurement]", "°C")          # same entities: no new subscription
    assert len(h.fake.subscriptions) == 2


def test_missing_entities_are_warned_about_once(ha, monkeypatch, caplog):
    monkeypatch.setattr(ha_mod, "MISSING_AFTER", 0.1)
    with caplog.at_level(logging.WARNING, logger="hubdeck.ha"):
        h = ha(states={("on", "light.nope"), ("on", "light.desk")})
        h.wait("on[light.desk]", True)
        wait_for(lambda: "no entity light.nope" in caplog.text)
        time.sleep(0.3)
    assert caplog.text.count("no entity light.nope") == 1
    assert "light.desk" not in caplog.text


# -- actions ------------------------------------------------------------------------
def test_actions_call_services(ha, caplog):
    h = ha()
    h.wait("connected", True)
    h.plugin.invoke("toggle", "light.desk")
    h.plugin.invoke("turn_off", ["light.desk", "light.off"])
    h.plugin.invoke("call", {"service": "light.turn_on", "entity": "light.desk", "data": {"brightness_pct": 40}})
    h.plugin.invoke("call", {"service": "notify.phone", "data": {"message": "hi"}})
    with caplog.at_level(logging.WARNING, logger="hubdeck.ha"):
        h.plugin.invoke("call", {"service": "fail.nothing"})
        assert h.wait_calls(5) == [
            ("homeassistant.toggle", ["light.desk"], {}),
            ("homeassistant.turn_off", ["light.desk", "light.off"], {}),
            ("light.turn_on", ["light.desk"], {"brightness_pct": 40}),
            ("notify.phone", None, {"message": "hi"}),
            ("fail.nothing", None, {}),
        ]
        wait_for(lambda: "ha.call fail.nothing failed: Service not found." in caplog.text)


def test_toggle_mute_reads_the_mute_even_of_an_unwatched_player(ha):
    h = ha()
    h.wait("connected", True)
    h.plugin.invoke("toggle_mute", "media_player.tv")         # nothing watches it: subscribe first
    assert h.wait_calls(1) == [("media_player.volume_mute", ["media_player.tv"], {"is_volume_muted": True})]
    assert h.fake.subscriptions[-1] == ["media_player.tv"]
    h.fake.change("media_player.tv", attrs={"is_volume_muted": True})
    time.sleep(0.2)
    h.plugin.invoke("toggle_mute", "media_player.tv")
    assert h.wait_calls(2)[1] == ("media_player.volume_mute", ["media_player.tv"], {"is_volume_muted": False})


# -- levels -------------------------------------------------------------------------
def test_light_level_builds_on_what_hubdeck_sent(ha):
    h = ha(levels={("level", "light.desk")})
    h.wait("value:level:light.desk", 40)
    h.plugin.adjust("level", "light.desk", 2)
    h.wait("value:level:light.desk", 42)                       # shown straight away
    h.plugin.adjust("level", "light.desk", 2)                  # before Home Assistant echoes: from 42
    h.wait("value:level:light.desk", 44)
    h.fake.change("light.desk", attrs={"brightness": 107})     # the echo of 42 (old): ignored while settling
    time.sleep(0.2)
    assert h.states["value:level:light.desk"] == 44
    assert [c[2] for c in h.wait_calls(2)] == [{"brightness_pct": 42}, {"brightness_pct": 44}]
    h.plugin.set("level", "light.desk", 0)                     # a fader at the bottom: off
    assert h.wait_calls(3)[2] == ("light.turn_off", ["light.desk"], {})


def test_light_level_falls_back_to_home_assistant_after_settling(ha, monkeypatch):
    monkeypatch.setattr(ha_mod, "SETTLE_SECONDS", 0.2)
    h = ha(levels={("level", "light.desk")})
    h.wait("value:level:light.desk", 40)
    h.plugin.adjust("level", "light.desk", 10)
    h.wait("value:level:light.desk", 50)
    h.wait("value:level:light.desk", 40, timeout=2)            # Home Assistant never changed it


def test_off_light_turns_on_from_the_bottom(ha):
    h = ha(levels={("level", "light.off")})
    h.wait("value:level:light.off", 0)
    h.plugin.adjust("level", "light.off", 2)
    assert h.wait_calls(1) == [("light.turn_on", ["light.off"], {"brightness_pct": 2})]


def test_climate_moves_one_step_per_click(ha):
    h = ha(levels={("level", "climate.ac")})
    h.wait("value:level:climate.ac", 43)                       # 22 °C within 16-30
    h.plugin.adjust("level", "climate.ac", 2)                  # one click at the default step
    h.wait("value:level:climate.ac", 46)
    h.plugin.adjust("level", "climate.ac", -6)                 # three clicks down
    calls = h.wait_calls(2)
    assert [c[2] for c in calls[:2]] == [{"temperature": 22.5}, {"temperature": 21.0}]
    h.plugin.set("level", "climate.ac", 100)
    assert h.wait_calls(3)[2][2] == {"temperature": 30}


def test_volume_number_and_mute_state(ha):
    h = ha(levels={("level", "media_player.tv"), ("level", "input_number.n")})
    h.wait("value:level:media_player.tv", 25)
    assert h.states["muted:level:media_player.tv"] is False
    h.wait("value:level:input_number.n", 50)
    h.plugin.adjust("level", "media_player.tv", 5)
    h.plugin.set("level", "input_number.n", 73)                # snapped to the number's step
    assert h.wait_calls(2) == [("media_player.volume_set", ["media_player.tv"], {"volume_level": 0.3}),
                               ("input_number.set_value", ["input_number.n"], {"value": 7.0})]


def test_fast_spins_send_at_most_one_change_per_interval(ha, monkeypatch):
    h = ha(levels={("level", "light.desk")})
    h.wait("value:level:light.desk", 40)
    monkeypatch.setattr(ha_mod, "SEND_EVERY", 0.3)
    for _ in range(10):
        h.plugin.adjust("level", "light.desk", 1)
        time.sleep(0.01)
    h.wait("value:level:light.desk", 50)
    time.sleep(0.6)
    sent = [c[2]["brightness_pct"] for c in h.fake.services()]
    assert 1 <= len(sent) <= 4 and sent[-1] == 50


# -- losing the connection ------------------------------------------------------------
def test_reconnects_and_drops_actions_meanwhile(ha, monkeypatch, caplog):
    monkeypatch.setattr(ha_mod, "RETRY_SECONDS", 0.3)
    h = ha(states={("on", "light.desk")}, levels={("level", "light.desk")})
    h.wait("on[light.desk]", True)
    with caplog.at_level(logging.WARNING, logger="hubdeck.ha"):
        h.fake.drop_client()
        h.wait("connected", False)
        assert h.states["on[light.desk]"] is None and h.states["value:level:light.desk"] is None
        h.plugin.invoke("toggle", "light.desk")
        wait_for(lambda: "dropped: Home Assistant isn't connected" in caplog.text)
    h.wait("connected", True, timeout=3)
    h.wait("on[light.desk]", True)
    assert len(h.fake.subscriptions) == 2 and h.fake.calls == []


def test_the_same_error_is_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(ha_mod, "RETRY_SECONDS", 0.05)
    fake = FakeWsServer(refuse=True)
    h = Harness(fake)
    try:
        with caplog.at_level(logging.WARNING, logger="hubdeck.ha"):
            h.start()
            wait_for(lambda: len(fake.targets) >= 3)
    finally:
        h.stop()
        fake.close()
    assert h.states == {"connected": False}
    assert caplog.text.count("Home Assistant refused the connection: HTTP/1.1 403") == 1


def test_a_refused_token_is_not_retried(ha, monkeypatch, caplog):
    monkeypatch.setattr(ha_mod, "RETRY_SECONDS", 0.05)
    with caplog.at_level(logging.ERROR, logger="hubdeck.ha"):
        h = ha(token="wrong")
        wait_for(lambda: "refused the token" in caplog.text)
        time.sleep(0.3)
    assert len(h.fake.targets) == 1 and caplog.text.count("refused the token") == 1
    assert h.states == {"connected": False}


def test_a_silent_server_is_dropped(ha, monkeypatch):
    monkeypatch.setattr(ha_mod, "PING_SECONDS", 0.1)
    monkeypatch.setattr(ha_mod, "PONG_SECONDS", 0.2)
    monkeypatch.setattr(ha_mod, "RETRY_SECONDS", 60)
    h = ha()
    h.wait("connected", True)
    h.fake.answer_pings = False
    h.wait("connected", False, timeout=3)


# -- through the engine -----------------------------------------------------------------
def test_keys_follow_entities_and_python_reads_more(write):
    fake = FakeHA(ENTITIES)
    eng = None
    try:
        eng, _, _ = make(write, f"""
            device: {{reassert_seconds: 0}}
            plugins: {{ha: {{url: "http://127.0.0.1:{fake.port}", token: {TOKEN}}}}}
            keys:
              1: {{state: "ha.on[light.desk]", face: {{label: "{{ha.state[sensor.temp]:.0f}}°"}}, face_on: {{bg: red}}}}
        """)
        wait_for(lambda: (eng.step() or eng.resolve_face(1).bg) == (255, 0, 0))
        assert eng.resolve_face(1).label == "22°"
        out = {}
        t = threading.Thread(target=lambda: out.update(v=eng.deck.state("ha.attr[climate.ac:temperature]")))
        t.start()
        while t.is_alive():
            eng.step()
            time.sleep(0.005)
        assert out["v"] == 22.0
        assert fake.subscriptions[-1] == ["climate.ac", "light.desk", "sensor.temp"]
    finally:
        if eng:
            eng.shutdown()
        fake.close()
