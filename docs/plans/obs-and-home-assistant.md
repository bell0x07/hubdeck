# Plan: OBS Studio and Streamlabs Desktop, and Home Assistant

Status: plan only, nothing built yet (2026-10-12). Facts marked **[verify]** come from
third-party sources or memory and must be checked against the real app before coding.

## What we want

1. **Streaming apps:** keys, knobs and lights that control OBS Studio and Streamlabs Desktop:
   scenes, mute, going live, recording, sources, volumes. Keys and lights show their state.
2. **Home Assistant, both ways:**
   - **A. hubdeck controls entities:** any key or knob can control any Home Assistant entity, and
     show its state.
   - **B. hubdeck is a device in Home Assistant (MQTT):** its controls become triggers there, for
     automations.

The suggested order is at the end. It starts with shared groundwork that all three need.

## 0. Groundwork shared by all three

### A shared WebSocket client

OBS, Streamlabs (remote mode) and Home Assistant all speak WebSocket. The `feat/teams` branch
already has a small standard-library client in `plugins/teams.py`. Move it to
`hubdeck/net/websocket.py` and extend it:

- `wss://` through `ssl.create_default_context()`, plus a `verify_ssl: false` escape hatch for
  self-signed Home Assistant certificates;
- any host, not just 127.0.0.1;
- text messages, fragmentation, ping/pong, close, and non-blocking `pump()`, as it does today.

Move the fake WebSocket server from `tests/test_teams.py` to `tests/fakes/ws_server.py`, so each
integration's tests can build on it. Once `feat/teams` is merged, Teams uses the shared client too.

Each integration is a `ThreadedPlugin` shaped like `discord.py`:

- it retries the connection every 5 s, and its states are unknown (None) while disconnected;
- it logs each distinct error once;
- secrets go in `secrets.yaml` through `!secret`.

### States with an argument

Today a state is `plugin.state`. These integrations need states about *one* scene, source or
entity, so we add an optional argument in brackets, accepted everywhere a state is:

```yaml
state: obs.scene_active[Gameplay]                 # a key lit while that scene is live
face: {text: "{ha.state[sensor.outside_temperature]:.1f}°"}
lighting: {base: {state: ha.on[binary_sensor.front_door], on: {color: red}}}
display: {chart: [ha.state[sensor.power_usage]]}  # the 15-key display's charts work too
```

- **Config:** `TEMPLATE_RE` and `STATE_RE` in `schema.py` accept an optional `[...]`. The loader
  checks it with `check_state(name, config, arg)`.
- **Plugins:** they declare which states take an argument. Like values with `watch()`, they get
  the (state, argument) pairs the config uses through a new `watch_states(pairs)`, and publish
  them as `name[arg]`.
- **Engine:** no change. State keys are just strings.

### Pickers in the editor

The editor needs to offer live choices: scenes, sources, entities. The audio app and output
pickers already work this way. Generalise them as `GET /api/options/<plugin>/<kind>`, answered by
an optional `Plugin.options(kind)` (for example `obs.options("scene")`, `ha.options("entity")`).
Add the actions, states and quick-setup presets to the catalogue in `app.js`, as for Discord.

## 1. OBS Studio and Streamlabs Desktop

### One plugin, two apps

There's a single `obs` plugin with the same actions for both apps:

```yaml
plugins:
  obs:
    app: auto                      # auto | obs | streamlabs
    password: !secret obs_password # OBS: Tools > WebSocket Server Settings
    token: !secret streamlabs_token   # Streamlabs: only needed for the remote (WebSocket) mode
```

`auto` tries OBS on port 4455 first, then Streamlabs. Behind the plugin are two small backends
with one interface: `connect`, `request`, `events`.

### Connections

- **OBS Studio:** obs-websocket 5, built into OBS 28 and later, at `ws://127.0.0.1:4455`.
  - The server says `Hello` (op 0), with `{challenge, salt}` when a password is set. The client
    answers `Identify` (op 1), with
    `base64(sha256(base64(sha256(password + salt)) + challenge))` and an `eventSubscriptions`
    mask: General, Scenes, Inputs, Outputs, SceneItems.
  - After `Identified` (op 2), requests are op 6 `{requestType, requestId, requestData}`,
    responses are op 7, and events are op 5.
- **Streamlabs Desktop:** JSON-RPC 2.0 with `{"jsonrpc":"2.0","id":n,"method":…,"params":{"resource":…,"args":[…]}}`.
  - **Locally,** it listens on the named pipe `slobs`: one request per line, no raw newlines
    inside. hubdeck already talks named pipes for Discord (`platforms.ipc_connect`).
    **[verify]** that the pipe needs no token, which would mean no setup at all on the same PC,
    and find what macOS uses instead.
  - **Remotely,** it speaks WebSocket (SockJS), with the token from Settings > Remote Control >
    QR code > show details. The first request is
    `{"method":"auth","params":{"resource":"TcpServerService","args":[token]}}`.
    **[verify]** the address: port 59650, path `/api/websocket` (raw SockJS).
  - Objects come back with a `resourceId` (`Scene["…"]`, `AudioSource["…"]`, `SceneItem["…"]`)
    to call methods on.
  - Events are subscriptions: sending the event name (`sceneSwitched` on `ScenesService`)
    returns a `SUBSCRIPTION`, and then `EVENT` messages arrive.

### Actions (the same names for both apps)

| action | argument | OBS request | Streamlabs |
| --- | --- | --- | --- |
| `scene` | scene name | `SetCurrentProgramScene` | `ScenesService.makeSceneActive(id)` **[verify]** |
| `toggle_stream`, `start_stream`, `stop_stream` | none | `ToggleStream`, `StartStream`, `StopStream` | `StreamingService.toggleStreaming` **[verify]** |
| `toggle_record`, `start_record`, `stop_record` | none | `ToggleRecord`, … | `StreamingService.toggleRecording` **[verify]** |
| `toggle_mute`, `mute`, `unmute` | source name | `ToggleInputMute`, `SetInputMute` | `AudioSource.setMuted` |
| `toggle_source`, `show_source`, `hide_source` | `source` or `{scene, source}`; the current scene by default | `GetSceneItemId` + `SetSceneItemEnabled` | `Scene.getItem` + `SceneItem.setVisibility` |
| `save_replay` | none | `SaveReplayBuffer` | `StreamingService.saveReplay` **[verify]** |
| `transition` (OBS only) | none | `TriggerStudioModeTransition` | none |
| `toggle_virtualcam` (OBS only) | none | `ToggleVirtualCam` | none |

An unknown scene or source is logged and notified once, not on every press.

### States, values and faces

- **States:** `connected`, `scene` (the live scene's name), `streaming`, `recording`, `replay`
  (the buffer is on), and with an argument `scene_active[Gameplay]`, `muted[Mic/Aux]` and
  `visible[Webcam]`.
- **Value:** `volume` with a source as its argument, for a knob, fader or the Dial.
  - OBS: `SetInputVolume` with `inputVolumeMul`, mapped to 0–100 on a dB curve so the knob feels
    even. `InputVolumeChanged` keeps it in sync.
  - Streamlabs: `AudioSource.setDeflection` (0–1) **[verify]**.
- **Faces and lights:** a scene key lit while live; the Base red while streaming
  (`lighting: {base: {state: obs.streaming, on: {color: red, mode: breathing}}}`).
- **Editor presets:** "OBS scene", "Mute a source", "Go live", "Record", and "Source volume" on a
  knob.

### Tests

- A fake obs-websocket server covering `Hello`/`Identify` with password auth, requests and events.
- A fake Streamlabs JSON-RPC server for both the pipe and the WebSocket.
- **Live:** Streamlabs Desktop 1.20.9 is installed on the Windows PC, so verify its API there
  first. OBS Studio isn't installed there: install it, or test on another machine.

## 2A. Home Assistant: hubdeck controls entities

```yaml
plugins:
  ha:
    url: http://homeassistant.local:8123
    token: !secret ha_token        # Profile > Security > Long-lived access tokens
```

### Connection

- It connects to `ws(s)://<host>/api/websocket`. The server sends `auth_required`, the client
  answers `{"type":"auth","access_token":…}`, and the server replies `auth_ok`.
- It subscribes with `subscribe_entities` filtered to the entity ids the config uses, which sends
  compact state diffs. Without the filter, it would fall back to `get_states` plus
  `subscribe_events: state_changed`.
- It calls services with `call_service {domain, service, service_data, target}`.

### Actions

```yaml
action: {ha.toggle: light.desk}                    # homeassistant.toggle
action: {ha.turn_on: scene.movie_night}            # scenes and scripts too
action: {ha.call: {service: light.turn_on, entity: light.desk, data: {brightness_pct: 40, color_name: red}}}
```

`ha.call` reaches any service: media players, covers, climate, notify, and so on.

### States, with an entity as the argument

- `on[light.desk]`: true for `on`, `open`, `home`, `playing`, `unlocked` and similar, false for
  their opposites, unknown when the entity is unavailable.
- `state[sensor.outside_temperature]`: the raw state, a number where it is one.
- `attr[light.desk:brightness]`: one attribute.

### Values (knobs, faders, the Dial)

`turn: {target: ha.level, arg: light.desk}` maps each domain to 0–100:

| domain | reads | sets with |
| --- | --- | --- |
| light | brightness | `light.turn_on` `brightness_pct` |
| media_player | `volume_level` | `media_player.volume_set` |
| cover | position | `cover.set_cover_position` |
| fan | percentage | `fan.set_percentage` |
| climate | target temperature | `climate.set_temperature` (step 0.5, scaled within min–max) |
| number, input_number | value | `set_value` (scaled within min–max) |

Fast knob spins already merge into one adjustment per round trip (`ThreadedPlugin._merge_adjusts`).

### Editor

- An entity picker with friendly names, searchable, from `get_states`.
- Presets: "Toggle a light", "Light brightness on a knob", "Show a sensor", "Run a scene".

## 2B. Home Assistant: hubdeck as an MQTT device

```yaml
plugins:
  mqtt:
    host: homeassistant.local
    username: hubdeck
    password: !secret mqtt_password
    discovery_prefix: homeassistant
```

### MQTT client

Choice: depend on `paho-mqtt`, or write a ~300-line standard-library MQTT 3.1.1 client.
**Recommended: paho-mqtt.** It's pure Python, so it builds into both apps unchanged, and it is
proven for TLS, reconnects and QoS. The WebSocket protocols are small enough to keep in-house;
MQTT, with its edge cases, isn't worth it.

### Discovery

hubdeck publishes retained config messages to
`homeassistant/<component>/hubdeck_<id>/<object>/config`. All of them share one `device` block:
identifiers `hubdeck_<machine id>`, name "hubdeck (<computer name>)", model "MasterHub" and
`sw_version`. Availability uses the topic `hubdeck/<id>/status`, with `online` and a last-will
`offline`.

| what | Home Assistant gets | published when |
| --- | --- | --- |
| each key 1–15, the Dial press, each knob press | an `event` entity with `event_types: [press, long_press, double_press]` | `{"event_type": "press"}` on its state topic. This is confirmed in the MQTT event docs; retained messages are ignored. |
| the same presses as device triggers | `device_automation` with `type: button_short_press` / `button_long_press` / `button_double_press` and `subtype: button_N`; one discovery topic per trigger | the payload on the trigger topic. Listed in the automation editor's trigger picker. |
| knob, roller and Dial turns | an `event` entity with `event_types: [cw, ccw]` and a `steps` attribute | each turn, merged per ~100 ms |
| faders | a `sensor`, 0–100 | on movement, limited to about 5 a second |
| the current page | a `select` with the page names | both ways: Home Assistant can switch hubdeck's page |
| commands from Home Assistant | a command topic `hubdeck/<id>/send` taking the same JSON as `hubdeck send` | lets automations set `vars`, show pictures and run actions |

A setting decides which presses are published:

- `publish: all` (default): every press is published, and hubdeck still runs its own action.
- `publish: unassigned`: only controls with no action on the current page are published.

### Tests

- Unit tests against a fake broker or paho's loopback.
- An integration test against an `eclipse-mosquitto` container in Docker.
- Live: the user's Home Assistant with the Mosquitto add-on.

## Suggested order

Each phase is its own branch: tests, a branch build to try out, then merging.

1. **Groundwork:** the shared WebSocket client with TLS, states with an argument, and the
   option pickers.
2. **OBS and Streamlabs.** Start by checking the **[verify]** items against Streamlabs 1.20.9 on
   the Windows PC.
3. **Home Assistant, direct control (2A).** Needs your Home Assistant's address and a long-lived
   token, which you put in `secrets.yaml` yourself.
4. **Home Assistant as an MQTT device (2B).** Needs the Mosquitto add-on and an MQTT user.

## Risks and open questions

- The Streamlabs API is thinly documented. Scene switching, streaming, recording and volume method
  names need checking first, and the local pipe's behaviour on macOS is unknown.
- Self-signed or Nabu Casa URLs for Home Assistant need `wss://` and maybe `verify_ssl: false`.
- Publishing every press to MQTT while hubdeck also acts on it could double-trigger things; that
  is what `publish: unassigned` is for.
- The bracket syntax `plugin.state[arg]` is new, so it must stay unambiguous next to format
  specs: `{ha.state[sensor.x]:.1f}`.

## Sources

- obs-websocket 5 protocol: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md
- Streamlabs Desktop API docs: https://streamlabs.github.io/streamlabs-desktop-api-docs/docs/index.html and https://github.com/stream-labs/streamlabs-obs-api-docs
- PySLOBS (a Streamlabs client, for the WebSocket details): https://pypi.org/project/PySLOBS/
- Home Assistant WebSocket API: https://developers.home-assistant.io/docs/api/websocket
- MQTT device triggers: https://www.home-assistant.io/integrations/device_trigger.mqtt/
- MQTT event entities: https://www.home-assistant.io/integrations/event.mqtt
