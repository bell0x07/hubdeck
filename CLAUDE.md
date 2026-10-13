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
  - switching pages by app: `_poll_focus` reads `focus_source.current` (a `focus.FocusWatcher`, started by `run()`; tests pass a fake), waits `FOCUS_SETTLE`, skips ignored windows and acts only when `Config.match_page` changes. `_saved_stack` holds the pages you opened yourself while an app's page is shown; `home` clears it. Engine-owned states (`page.auto/app/title`) go through `_set_state`.
  - `def_for(kind, index)` maps a module index to the current page's definition; `def_on(page, (kind, number))` does the same by config number for any page (used by `deck`).
- **`platforms/`:** every OS-specific call. Nothing outside it branches on `sys.platform` (the editor only passes it to the browser).
  - `base.py` is the interface: `Platform` (app folder, single instance, open/edit/run, `activate` (an app's window to the front for `launch.focus`; Windows: `focus.WinWindows`, which lists windows like Alt+Tab in z-order and sends an empty `SendInput` first so Windows lets it take the foreground; elsewhere False, so `focus` opens), message box, IPC, accent colour, label fonts, tray hooks), `AudioBackend`, `Keyboard`, `IpcConnection`, `FocusReader` (the focused window; None for the system's own surfaces), `MediaReader` (what's playing: the session the media keys control). Unsupported features raise `Unsupported`. `run_with_event_loop` runs `hubdeck run` (macOS: engine on a thread, AppKit's loop on the main thread).
  - `current()` picks `windows/`, `macos/` or `posix.py` (shared by macOS and Linux, and the fallback for other OSes, without audio or keystrokes). Linux support goes into a `linux/` package subclassing `PosixPlatform`.
  - `keys.py` holds the canonical key names and aliases (`cmd` → `win`); each `Keyboard` maps them to its own codes.
  - OS libraries (pycaw, pyobjc) are imported inside methods only.
- **`control.py`:** the control channel behind `hubdeck send`. `Engine.run()` starts it: 127.0.0.1 on a random port, one JSON request per connection, with the port and a per-run token in `control.json` (0600). Actions are checked with `Engine.check_runnable` and queued for the engine thread. `send image` stores pictures client-side in `cache/sent` (scaled, one per name) and only sends the path; a face `image` may be a template (`{vars.picture}`), resolved per paint.
- **`deck.py`:** `Deck`, the thread-safe API over a running engine that Python actions (`plugins/python.py`) get as `deck` and plugins as `ctx.deck`. Controls are objects per module (`deck.keys[n]`, `dial`, `knobs[n]`, `rollers[n]`, `faders[n]`, `base`) with the methods that module supports; the engine addresses them as `(module, config number)`. Runtime faces (keys, Dial; per page) live in `Engine.overrides` and are merged last in `face_for` (with the `REPLACE` key, set by `set_face(replace=True)`, they replace the config's faces instead; with `VALUE_OF`, `screen_face` gives `{value}` and the gauge another control's value; `MUTED` holds fields `screen_face` merges on top while that control's target is muted; `set_face(seconds=)` puts an expiry in `Engine.override_until`, which `step()` clears); runtime lighting (global) in `Engine.light_overrides`, merged over the config zones in `_update_light_zones`. Both open the modules they need (`_open_runtime_modules`). Simulated turns go through `_turn_def` like real ones, which also runs the control's `on_turn` actions; actions carry their source `(page, module, number)` so `deck.control` and the page default work. `deck.run` uses `check_runnable(start=True)`, which starts unused plugins. Calls are queued with `Engine.call()` and run on the engine thread; reads are single lookups. Pictures made in code live in `ImageStore` under `memory:` names, dropped once no face uses them.
- **`net/websocket.py`:** a minimal WebSocket client for plugins (`ws://` and `wss://`, JSON text messages, ping/pong). It's single-threaded like the Discord pipe client: `pump()` returns what has arrived without blocking, from the plugin's own thread. Tests run it against `tests/fakes/ws_server.py`, which subclasses answer through `on_message`.
- **`plugins/`:** each plugin declares `actions`, `states` and `values` (adjustable targets via `adjust`/`set`, published through `watch` as `value:<name>:<arg>` states, and optionally `muted:<name>:<arg>` next to them, which `Engine.control_muted` reads). `adjust` gets the turn's `expo`; plugins compute the new level with `base.adjusted(current, delta, expo)`. Slow plugins subclass `ThreadedPlugin`. Plugin modules must not import heavy dependencies at import time.
  - Plugins whose state names come from the config (`vars`: anything set; `shortcuts`: `outputs`) override `check_state`.
  - A state can take an argument, `plugin.state[arg]` (`state_args` lists which; `check_state` gets `arg`). The engine passes the (state, argument) pairs the config shows to `watch_states`, and the plugin publishes them under `state_key(state, arg)`, e.g. `on[light.desk]`. State keys stay plain strings everywhere else.
  - Plugins with `fetches_states` (`ha`) publish only what `watch_states` names. `Engine.read_state` (behind `deck.state` and `hubdeck send get`) adds a state nothing shows yet to `_asked_states` (cleared on reload) and waits up to `ASK_SECONDS` for it, never on the engine's own thread (`_loop_thread`).
  - `check_config` also runs, with `{}`, for a plugin the config uses without a `plugins.<name>` section, so a missing client ID fails validation.
  - `audio.app_volume` with `arg: focused` resolves the app through `ctx.deck.focused` (the engine's last non-ignored window) on every read and adjust; the value state stays `value:app_volume:focused`. `{audio.toggle_mute: {app: ...}}` takes the same names; app mute is `AudioBackend.get_app_mute/set_app_mute` (Windows: each session's `SimpleAudioVolume` mute; elsewhere `Unsupported`).
  - `ha` (Home Assistant) is a `ThreadedPlugin` over `net.websocket`. It subscribes (`subscribe_entities`) to the entities `watch` and `watch_states` name, plus those `toggle_mute` needs, keeps their compressed states in `_entities`, and publishes `on[e]`, `state[e]`, `attr[e:a]` and `value:level:e` (media players also `muted:level:e`). A level hubdeck sets stays in `_local` for `SETTLE_SECONDS`, since Home Assistant's echoes lag, and goes out at most every `SEND_EVERY`. A refused token isn't retried until the plugin restarts (a config change).
  - `platforms = ("darwin",)` limits a plugin to some OSes. Configs still load elsewhere, but the editor's `/api/meta` only lists `available()` plugins, so the UI never offers them (Shortcuts is macOS-only, `media` Windows-only).
  - The `media` plugin only has states (`media.playing`, ...); the `{media: play_pause}` short form is `keyboard.media`.
- **`gui/`:** browser editor.
  - `server.py` is a stdlib HTTP server on 127.0.0.1 with a per-run token and a Host check, serving a JSON API and `assets/gui/` (vanilla JS, no build).
  - `doc.py` edits config.yaml with ruamel.yaml round-trip. It keeps comments, `!secret` tags and the user's spacing on untouched lines, and validates with the real loader before writing.
  - Module positions and the Base orientation (`modules.<kind>.position`, `device.orientation`) are editor-only; the engine ignores them.
- **`packaging/macos/`:** builds hubdeck.app on Linux (Docker, `.gitlab-ci.yml`). `build.py` unpacks python-build-standalone, pip-installs the macOS wheels (`--platform`; macOS-only dependencies are named explicitly, since pip judges markers by the build machine), cross-compiles `launcher.c` with zig and signs with rcodesign. The launcher loads `libpython` in-process (so macOS sees the app's identity) and runs `hubdeck._bundle`: no arguments = tray, otherwise the CLI; `HUBDECK_PYTHON=1` makes it plain Python (used by the accent-colour helper). Bundle id: `hubdeck._bundle.BUNDLE_ID`. Inside the app, notifications go through UNUserNotificationCenter and the tray offers "Start at login" (SMAppService).
  - The version's only source is `hubdeck.__version__` (pyproject reads it). Tags `vX.Y.Z` must match it and build X.Y.Z; branch builds are `<version>-<branch>.<pipeline>`. `packaging/release.py` (CI `release` job, tags only) publishes to the GitLab package registry and release, and to GitHub with `GITHUB_TOKEN`. Branch builds are never released.
- **`cli.py` / `cli_probe.py`:** the `hubdeck` command, including the hidden `probe` tools. `tray.py` is `hubdeck-tray`.
- **`packaging/`:** the macOS app and the Windows MSI and zip, both cross-built on Linux (Docker or GitLab CI) from python-build-standalone, the target's wheels and zig-compiled launchers. `common.py` is shared by both builds; `release.py` publishes `vX.Y.Z` tags to GitLab and GitHub.
  - Windows: the launchers load `python\python313.dll` in-process, and `python313._pth` sets `sys.path` and isolates it from `PYTHON*` variables.
  - The MSI is built with wixl 0.106 (Debian trixie). It leaves file versions out, so a component's key path must never be an .exe, .dll or .pyd, or upgrades lose those files. It also has `Permanent` backwards on `<Environment>`, and supports custom actions only by `FileKey` (not `Directory`). No `--` inside XML comments in `hubdeck.wxs`.
  - Windows swaps an installed hubdeck's files while it runs, and it keeps running the old code. So after installing, the MSI starts `hubdeck-tray.exe --replace` (`instance.replace_running`): it sends `quit` over the control channel, or stops older copies of the same exe that don't know `quit`, then takes over.

## Hardware facts that matter (all verified)

- **Each module is its own USB HID device.** The Base only detects newly inserted modules at power-up, so replug its USB after adding one.
- **Screen modules need software mode** first: 15-key `03 3a 01`, Dial `03 49 01`. Pictures are baseline JPEG (4:4:4) in 320-byte chunked output reports: 15-key `02 36`, 72×72 per key; Dial `02 45`, 240×240, ~70 fps possible.
- **The M0 family** uses output `02 2c/2d/2e <module> <idx> …` for LED mode, colour and brightness, always sent with save = 0. Get-feature returns the *saved* LED state, not the live one.
- **Input frames** are `01 <tag> <len u16> <event> <len> data`. Tags: M0 `0x2a`, 15-key `0x34`, Dial `0x44`.
- **Knob LED index = input index.** Default numbering is reading order: knobs 1 = top, 2 = bottom-left, 3 = bottom-right; rollers 1 = left. The Dial's turn byte is inverted compared with the knobs (`00` = clockwise).
- **MasterHUB, including its elevated `MHDaemon`, fights over the modules.** It must be closed for hardware tests.

## Gotchas

- **pycaw:** `AudioUtilities.GetMicrophone()` returns a raw `IMMDevice`, so wrap it with `CreateDevice`. Per-app volume goes through `GetAllSessions()`, matched by the session process's name or an ancestor's, up to the first one under `%SystemRoot%` (new Teams plays through a `msedgewebview2.exe` it starts). Ancestors are looked up once per process (pid and start time), since values are read every 0.5 s.
- **Media sessions (Windows):** pywinrt's `GlobalSystemMediaTransportControlsSessionManager`; `.get()` blocks on an async call, so the reader lives on the `media` plugin's thread, which `WinMedia` puts in the MTA. A session names its app by AppUserModelID: a Start menu id (`Brave`, resolved through `shell:AppsFolder`) or the process (`Spotify.exe`).
- **LED colours need gamma correction** (`defaults.led_gamma` 2.2). Without it, green shows turquoise and red shows pink.
- **Discord:** the app must have exactly one redirect, `http://localhost`. Voice RPC only works for the app owner and whitelisted testers.
- **Spotify:** the in-app volume isn't the Windows mixer's session volume. UI Automation's `SetValue` on its slider ("Change volume") only changes the hidden page element, which Spotify puts back (verified), so `spotify.volume` goes through the Web API: a PKCE login (no client secret) with the redirect on `http://127.0.0.1:<port>` (Spotify rejects `localhost`). Changing the volume needs Premium. Spotify still reports the old volume for a moment after a change, hence `SETTLE_SECONDS`. The browser opens by itself only at start without a login; after that, only when a Spotify control is used.
- **States with an argument in YAML:** inside a flow mapping, `[` starts a list, so `{state: ha.on[light.x]}` doesn't parse; it needs quotes. Block style (`state: ha.on[light.x]`) is fine.
- **Hot reload applies config changes only.** Code changes need a restart.
- **macOS** (all verified on hardware):
  - hidapi reaches the modules through IOHIDManager, with one path per module, and opens them **exclusively**: `hubdeck probe` can't open a module while hubdeck runs. Reading input needs no permission.
  - Posting keystrokes needs the Accessibility permission, or macOS drops them. AppKit calls from the tray must go through `call_on_ui_thread`.
  - Per-app volume uses AppleScript `sound volume` (Spotify, Music). It runs `osascript` (~0.2 s) on its own worker (`AppVolumes`), never on the audio thread. An Apple event blocks until the user answers macOS's Automation prompt, and that froze the Dial and mute before. **Don't use in-process NSAppleScript:** off the main thread it hung while compiling `tell application "Music"`.
  - `NSColor.controlAccentColor` is cached per process and never refreshes without an app event loop. `MacPlatform.accent_color` watches the setting through CFPreferences and resolves the colour in a fresh process when it changes.
  - Alerts from `osascript display alert` open behind other windows, so use notifications.
  - `NSWorkspace.frontmostApplication` only refreshes while the main thread runs an event loop (Apple's docs; not yet verified on hardware), hence `run_with_event_loop`. Window titles come from the Accessibility API through ctypes (`macos/focus.py`), on the watcher's thread, so a hung app can't stall the controls.
  - The 2xRoller sent no input reports at all on macOS (raw hidapi, after replug); the cause is unknown.
- **Never send firmware, serial, factory or recovery commands.** The denylist in `device/hid.py` enforces this; keep it up to date when adding commands.

## Testing

Run `.\.venv\Scripts\python.exe -m pytest` (macOS: `.venv/bin/python -m pytest`). Engine tests drive `Engine.step()` with a fake clock and fake modules (`factories={kind: lambda: fake}`), plus a `RecorderPlugin` (`tests/conftest.py`) with actions, states and values.
