# Context for Claude Code

hubdeck controls the Cooler Master MasterHub modules on Windows and macOS: 15-key screen module, Dial with round screen, 3xKnob, 2xRoller, 5xFader and the Base (RGB). It's a Python package under `src/hubdeck` and runs in the project's `.venv`, always called as `.\.venv\Scripts\...` on Windows and `.venv/bin/...` on macOS. The user-facing docs are in README.md. Protocol facts, each tagged by source, are in `docs/protocol/`.

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
- **`platforms/`:** every OS-specific call. Nothing outside it branches on `sys.platform` (the editor only passes it to the browser).
  - `base.py` is the interface: `Platform` (app folder, single instance, open/edit/run, message box, IPC, accent colour, label fonts, tray hooks), `AudioBackend`, `Keyboard`, `IpcConnection`. Unsupported features raise `Unsupported`.
  - `current()` picks `windows/`, `macos/` or `posix.py` (shared by macOS and Linux, and the fallback for other OSes, without audio or keystrokes). Linux support goes into a `linux/` package subclassing `PosixPlatform`.
  - `keys.py` holds the canonical key names and aliases (`cmd` → `win`); each `Keyboard` maps them to its own codes.
  - OS libraries (pycaw, pyobjc) are imported inside methods only.
- **`control.py`:** the control channel behind `hubdeck send`. `Engine.run()` starts it: 127.0.0.1 on a random port, one JSON request per connection, with the port and a per-run token in `control.json` (0600). Actions are checked with `Engine.check_runnable` and queued for the engine thread. `send image` stores pictures client-side in `cache/sent` (scaled, one per name) and only sends the path; a face `image` may be a template (`{vars.picture}`), resolved per paint.
- **`plugins/`:** each plugin declares `actions`, `states` and `values` (adjustable targets via `adjust`/`set`, published through `watch` as `value:<name>:<arg>` states). Slow plugins subclass `ThreadedPlugin`. Plugin modules must not import heavy dependencies at import time.
  - Plugins whose state names come from the config (`vars`: anything set; `shortcuts`: `outputs`) override `check_state`.
  - `platforms = ("darwin",)` limits a plugin to some OSes. Configs still load elsewhere, but the editor's `/api/meta` only lists `available()` plugins, so the UI never offers them (Shortcuts is macOS-only).
- **`gui/`:** browser editor.
  - `server.py` is a stdlib HTTP server on 127.0.0.1 with a per-run token and a Host check, serving a JSON API and `assets/gui/` (vanilla JS, no build).
  - `doc.py` edits config.yaml with ruamel.yaml round-trip. It keeps comments, `!secret` tags and the user's spacing on untouched lines, and validates with the real loader before writing.
  - Module positions and the Base orientation (`modules.<kind>.position`, `device.orientation`) are editor-only; the engine ignores them.
- **`packaging/macos/`:** builds hubdeck.app on Linux (Docker, `.gitlab-ci.yml`). `build.py` unpacks python-build-standalone, pip-installs the macOS wheels (`--platform`; macOS-only dependencies are named explicitly, since pip judges markers by the build machine), cross-compiles `launcher.c` with zig and signs with rcodesign. The launcher loads `libpython` in-process (so macOS sees the app's identity) and runs `hubdeck._bundle`: no arguments = tray, otherwise the CLI; `HUBDECK_PYTHON=1` makes it plain Python (used by the accent-colour helper). Bundle id: `hubdeck._bundle.BUNDLE_ID`. Inside the app, notifications go through UNUserNotificationCenter and the tray offers "Start at login" (SMAppService).
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
- **macOS** (all verified on hardware):
  - hidapi reaches the modules through IOHIDManager, with one path per module, and opens them **exclusively**: `hubdeck probe` can't open a module while hubdeck runs. Reading input needs no permission.
  - Posting keystrokes needs the Accessibility permission, or macOS drops them. AppKit calls from the tray must go through `call_on_ui_thread`.
  - Per-app volume uses AppleScript `sound volume` (Spotify, Music). It runs `osascript` (~0.2 s) on its own worker (`AppVolumes`), never on the audio thread. An Apple event blocks until the user answers macOS's Automation prompt, and that froze the Dial and mute before. **Don't use in-process NSAppleScript:** off the main thread it hung while compiling `tell application "Music"`.
  - `NSColor.controlAccentColor` is cached per process and never refreshes without an app event loop. `MacPlatform.accent_color` watches the setting through CFPreferences and resolves the colour in a fresh process when it changes.
  - Alerts from `osascript display alert` open behind other windows, so use notifications.
  - The 2xRoller sent no input reports at all on macOS (raw hidapi, after replug); the cause is unknown.
- **Never send firmware, serial, factory or recovery commands.** The denylist in `device/hid.py` enforces this; keep it up to date when adding commands.

## Testing

Run `.\.venv\Scripts\python.exe -m pytest` (macOS: `.venv/bin/python -m pytest`). Engine tests drive `Engine.step()` with a fake clock and fake modules (`factories={kind: lambda: fake}`), plus a `RecorderPlugin` (`tests/conftest.py`) with actions, states and values.
