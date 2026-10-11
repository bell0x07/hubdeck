# Context for Claude Code

hubdeck controls the Cooler Master MasterHub modules on Windows: 15-key screen module, Dial with round screen, 3xKnob, 2xRoller, 5xFader and the Base (RGB). It's a Python package under `src/hubdeck` and runs in the project's `.venv`, always called as `.\.venv\Scripts\...`. The user-facing docs are in README.md. Protocol facts, each tagged by source, are in `docs/protocol/`.

## Layout

- **`device/`**
  - `hid.py`: `HidModule` (feature get/set, output reports, framed input) and the per-module safety policy, a hard denylist plus an allowlist.
  - `m4315.py` is the key module. `modules.py` holds the Dial and the M0 family (Base/Knobs/Rollers/Faders) and emits `Event(kind, index, value)`.
  - `manager.py` opens the modules the config uses and handles hot-plug.
  - `layout.py` maps module indexes to config numbers (reading order after `rotate`), plus axis direction and picture rotation.
  - `fake.py` has fake modules for tests.
- **`config/`**
  - `schema.py` holds the pydantic models: pages with keys/dial/knobs/rollers/faders, turn targets, faces, `lighting`, `modules`.
  - `loader.py` handles YAML 1.2 booleans, `!secret`, cross-reference checks and image paths.
- **`engine.py`** is the main loop:
  - manager events → gestures (`gestures.py`), turns and faders (soft takeover)
  - plugin states → faces (`render/faces.py`, `render/images.py`)
  - lighting (`lighting.py`), hot reload
  - `def_for(kind, index)` is the one place that maps a module index to the current page's definition.
- **`plugins/`:** each plugin declares `actions`, `states` and `values` (adjustable targets via `adjust`/`set`, published through `watch` as `value:<name>:<arg>` states). Slow plugins subclass `ThreadedPlugin`. Plugin modules must not import heavy dependencies at import time.
- **`cli.py` / `cli_probe.py`:** the `hubdeck` command, including the hidden `probe` tools. `tray.py` is `hubdeck-tray`.

## Hardware facts that matter (all verified)

- **Each module is its own USB HID device.** The Base only detects newly inserted modules at power-up, so replug its USB after adding one.
- **Screen modules need software mode** first: 15-key `03 3a 01`, Dial `03 49 01`. Pictures are baseline JPEG (4:4:4) in 320-byte chunked output reports: 15-key `02 36`, 72×72 per key; Dial `02 45`, 240×240, ~70 fps possible.
- **The M0 family** uses output `02 2c/2d/2e <module> <idx> …` for LED mode, colour and brightness, always sent with save = 0. Get-feature returns the *saved* LED state, not the live one.
- **Input frames** are `01 <tag> <len u16> <event> <len> data`. Tags: M0 `0x2a`, 15-key `0x34`, Dial `0x44`.
- **Knob LED index = input index.** Default numbering is reading order: knobs 1 = top, 2 = bottom-left, 3 = bottom-right; rollers 1 = left. The Dial's turn byte is inverted compared with the knobs (`00` = clockwise).
- **MasterHUB, including its elevated `MHDaemon`, fights over the modules.** It must be closed for hardware tests.

## Gotchas

- **pycaw:** `AudioUtilities.GetMicrophone()` returns a raw `IMMDevice`, so wrap it with `CreateDevice`. Per-app volume goes through `GetAllSessions()`, matched by process name.
- **LED colours need gamma correction** (`defaults.led_gamma` 2.2). Without it, green shows turquoise and red shows pink.
- **Discord:** the app must have exactly one redirect, `http://localhost`. Voice RPC only works for the app owner and whitelisted testers.
- **Hot reload applies config changes only.** Code changes need a restart.
- **Never send firmware, serial, factory or recovery commands.** The denylist in `device/hid.py` enforces this; keep it up to date when adding commands.

## Testing

Run `.\.venv\Scripts\python.exe -m pytest`. Engine tests drive `Engine.step()` with a fake clock and fake modules (`factories={kind: lambda: fake}`), plus a `RecorderPlugin` (`tests/conftest.py`) with actions, states and values.
