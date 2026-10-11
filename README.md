# hubdeck

A Stream-Deck-style controller for the whole **Cooler Master MasterHub** family:

| module | what hubdeck does with it |
| --- | --- |
| **15-key screen module** | Each key gets a face (a [Tabler icon](https://tabler.io/icons), label, live text, picture or animated GIF) and actions. |
| **Dial with round screen** | Turn it to adjust a value, press it for actions, and show a face with a value gauge, picture or GIF. |
| **3 knobs, 2 rollers, 5 faders** | Volumes (system, microphone, per app) or any action, with knob and fader LEDs that can follow the value. |
| **Base** | RGB lighting that reacts to state, for example red and breathing while you're muted. |

Actions cover keystrokes, media keys, launching apps, Windows audio and Discord mute/deafen/hold-to-mute. Controls can be grouped into pages. Everything lives in one YAML file and applies as soon as you save it. Modules can be plugged in, swapped or mounted rotated while hubdeck runs.

This is an unofficial tool, tested on Windows 11 with real hardware. The protocol notes, with sources and what's verified, are in [docs/protocol/](docs/protocol/README.md).

## Install

You need Python 3.11 or newer on Windows.

```powershell
git clone https://github.com/bell0x07/hubdeck.git
cd hubdeck
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

This installs two commands into `.venv\Scripts`:
- `hubdeck`: the command line
- `hubdeck-tray`: the background app with a tray icon

## First run

```powershell
.\.venv\Scripts\hubdeck.exe list     # your modules should be marked
.\.venv\Scripts\hubdeck.exe test     # paints the key numbers and prints key presses
.\.venv\Scripts\hubdeck.exe init     # writes an example config.yaml and secrets.yaml
.\.venv\Scripts\hubdeck.exe run      # runs in the foreground (Ctrl+C to quit)
```

- **Close MasterHUB first, including its background daemon.** It drives the same modules, so the two would fight over the screens and lights.
- **For everyday use, run `hubdeck-tray`.** Clicking the tray icon opens the config for editing. The menu can switch pages, reload, open the log and open this repo.
- **To start at login,** put a shortcut to `.venv\Scripts\hubdeck-tray.exe` in `shell:startup`.

## Files

| file | where |
| --- | --- |
| config | `%APPDATA%\hubdeck\config.yaml` (or `--config path`) |
| secrets | `secrets.yaml` next to the config |
| log | `%APPDATA%\hubdeck\hubdeck.log` |
| downloaded images | `%APPDATA%\hubdeck\cache\` |

- `hubdeck where` prints these paths.
- `hubdeck validate` checks the config and lists the pages, modules and plugins it uses.
- `validate -v` also lists every plugin's actions and states.

## Config overview

```yaml
device:
  ownership: shared      # shared: only touch controls used in the config | exclusive: own everything
  reassert_seconds: 5    # repaint keys this often in case another app draws over them

modules:                 # how each module is mounted, plus screen backlight
  keys:   {rotate: 0, brightness: 80}
  dial:   {rotate: 0, brightness: 100}
  faders: {rotate: 270}  # mounted sideways

plugins:
  discord:
    client_id: !secret discord_client_id
    client_secret: !secret discord_client_secret

lighting:
  base:  {color: "#3cc86e", state: discord.muted, on: {color: "#c81e28", mode: breathing}}
  knobs: {all: {color: white, brightness: value}}

start_page: main
pages:
  main:
    keys:
      1:  {action: {page: media}, face: {icon: music, label: Media}}
      11:
        action: discord.toggle_mute
        state: discord.muted
        face:         {icon: microphone, label: LIVE}
        face_on:      {icon: microphone-off, label: MUTED, bg: "#c81e28", fg: white}
        face_unknown: {icon: plug-connected-x, label: NO DC, bg: "#c81e28", fg: white}
    dial:
      turn: {target: audio.volume, step: 2}
      action: audio.toggle_mute
      face: {icon: volume, label: "{value}%", gauge: true}
    knobs:
      1: {turn: {target: audio.app_volume, arg: spotify.exe}}
    rollers:
      1: {turn: {cw: {media: next}, ccw: {media: prev}}}
    faders:
      1: {target: audio.volume}
  media:
    keys:
      1: {action: page.back, face: {icon: arrow-left, label: Back}}
```

`hubdeck init` writes a fuller, commented example to start from. If you only need one page, put `keys:`, `dial:`, `knobs:`, `rollers:` and `faders:` at the top level instead of `pages:`.

### Numbering and mounting

Controls are numbered **in reading order** (left to right, then top to bottom) as the module is mounted. Default mountings and numbering:

| module | default mounting (`rotate: 0`) | numbers |
| --- | --- | --- |
| keys | 5 wide, 3 tall | 1–15, row by row |
| knobs | one knob on top, two below | 1 = top, 2 = bottom-left, 3 = bottom-right |
| rollers | side by side | 1 = left, 2 = right |
| faders | vertical, 0 at the bottom | 1–5, left to right |

If a module is mounted turned, set `modules.<module>.rotate` to the clockwise turn: 90, 180 or 270. hubdeck then:
- **renumbers** the controls in reading order for the new orientation
- makes faders and rollers **increase upward, or rightward** when sideways
- **turns pictures** on the keys and Dial upright

`hubdeck test` shows the key numbers for the configured rotation.

### Keys, Dial, knobs and rollers

These share the same fields.

- **Gestures:** `action` (same as `on_press`), `on_down`, `on_up`, `on_long_press` and `on_double_press`. A control with only a press action fires immediately on key-down. If it also has a long or double action, the press fires on release, once the gesture is clear. Rollers can't be pressed.
- **Actions** are written either as `plugin.action` or as `{plugin.action: argument}`. A list runs several actions in order. The short forms are `page`, `keys`, `type`, `media`, `run` and `open`.
- **Turning** (Dial, knobs, rollers) works one of two ways:
  - `turn: {target: plugin.value, arg: ..., step: 2, invert: false}` adjusts a value by `step` per click.
  - `turn: {cw: action, ccw: action}` runs actions per click (clockwise = up/right for rollers).
  - `turn: audio.volume` is short for a target.
- **State:** `state: plugin.state` uses `face_on` while the value is true and `face_unknown` while it's unknown, for example when Discord is closed. Fields you don't set in the overlay are taken from `face`.
- **Pages:** each page can have its own `dial`, `knobs`, `rollers` and `faders` sections. A page without one uses the start page's section.

### Faces (keys and the Dial)

- **Fields:** `icon` (a Tabler name; `filled: true` uses the filled style), `label`, `text`, `bg`, `fg` (icon colour), `label_color` and `font_size`.
- **Live text:** `text` and `label` can include `{plugin.state}` or `{plugin.state:format}`, for example `{system.time:%H:%M}` or `{system.cpu:.0f}%`.
- **Control value:** on a control with a turn target, `{value}` shows the target's current value, and `gauge: true` draws a ring around the Dial.
- **Pictures:** `image: file.png`, `.jpg` or **animated `.gif`**.
  - A relative path is resolved next to the config.
  - An `http(s)` URL is downloaded in the background (10 MB limit) and cached.
  - `image_fit: cover` (default) fills the face; `contain` fits the whole picture inside.
  - Labels on top of pictures get an outline so they stay readable.
  - GIFs play at their own speed, capped by `defaults.animation_fps` (default 30).
- **Icons:** search with `hubdeck icons <word>`, or browse at https://tabler.io/icons.

### Faders

```yaml
faders:
  1: {target: audio.volume}
  3: {target: audio.app_volume, arg: discord.exe, invert: false}
```

A fader sets its target to its position (0–100). **Soft takeover:** a fader only takes control once it reaches or passes the target's current value, so moving it never makes the volume jump.

### Lighting (Base, knobs, faders)

```yaml
lighting:
  base:   {color: "#3cc86e", state: audio.speaker_muted, on: {color: red, mode: breathing}}
  knobs:
    all:  {color: white, brightness: value, min_brightness: 10}
    2:    {color: "#c81e28"}
  faders:
    all:  {animate: {effect: pulse, colors: ["#3cc86e", "#000000"], period: 2}}
```

- **`mode`:** `off`, `static`, `breathing`, `strobe`, `spring`, `cycling`, `random`, `wave`, `water` or `rainbow`. These are the module's own effects. A `color` on its own means static.
- **`brightness`:** 0–100, or **`value`** to follow the control's target. A knob at 0% then glows at `min_brightness` (default 10); set 0 for fully dark.
- **`animate`:** software effects computed by hubdeck at about 30 updates per second:
  - `pulse`: fades between `colors[0]` and `colors[1]` (black by default)
  - `blink`: switches between them
  - `cycle`: blends through all the colours
- **State:** `state`, `on` and `unknown` work like key faces.
- **Zones:** `all` sets every knob/fader, and numbered entries override it. The numbering follows the mounting, like the controls.
- **Gamma:** colours are corrected for LEDs with `defaults.led_gamma` (2.2). Without it, green looks turquoise and red looks pink.
- **Nothing is stored on the modules.** Unplugging, or MasterHUB, restores their own lighting.

### Secrets

`!secret name` reads `name` from `secrets.yaml`, or from the environment variable `HUBDECK_<NAME>`. Any value can be a secret.

### YAML note

Only `true` and `false` count as booleans, so `label: ON` stays text.

## Plugins

| plugin | actions | states | values (turn/fader targets) |
| --- | --- | --- | --- |
| `page` | `goto` (`{page: name}`), `back`, `home` | none | none |
| `keyboard` | `keys`, `key_down`, `key_up`, `media`, `type` | none | none |
| `launch` | `open` (file, folder, app or URL), `run` (shell command) | none | none |
| `audio` | `toggle_mute` / `mute` / `unmute` (speakers or microphone), `volume_up`, `volume_down`, `set_volume`, `set_output` | `speaker_muted`, `mic_muted`, `volume`, `mic_volume`, `output` | `volume`, `mic_volume`, `app_volume` (arg: process name, e.g. `spotify.exe`) |
| `system` | none | `time`, `cpu`, `ram` | none |
| `discord` | `toggle_mute`, `mute`, `unmute`, `toggle_deafen`, `deafen`, `undeafen`, `hold_mute_start`, `hold_mute_end` | `muted`, `deafened`, `holding`, `connected` | none |

- **Media keys:** `play_pause`, `next`, `prev`, `stop`, `vol_up`, `vol_down` and `mute`.
- **Hold to mute:** put `on_down: discord.hold_mute_start` and `on_up: discord.hold_mute_end` on one key. This works even if you switch pages while holding it.
- **App volume:** applies to every audio session of that process. An app that isn't playing audio has no value, so faders don't take it over.

### Discord setup

The Discord plugin needs a Discord app. You create it once, for your own account:

1. Go to https://discord.com/developers/applications and click **New Application**.
2. Under **OAuth2**, copy the **Client ID** and **Client Secret** into `secrets.yaml`.
3. Add the redirect `http://localhost` and click **Save Changes**. It must be the **only** redirect. If there are none, or more than one, authorization fails with `Missing "redirect_uri" in request`.

The first time hubdeck connects, Discord shows an authorization popup. The login token is then kept in `%APPDATA%\hubdeck\discord_token.json`. Voice RPC only works for the app's owner and for whitelisted testers.

## Hot-plugging and sharing

- **hubdeck only opens the modules your config uses.** It reconnects to them after they drop off USB, and picks up modules plugged in later.
- **The Base only detects newly inserted modules when it powers up.** After adding a module, unplug and replug the Base's USB cable. Removing a module is noticed immediately.
- **`shared` mode** touches only what the config uses and leaves the rest to other software. **`exclusive` mode** owns everything and blanks unused keys.

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

- **Tests** use fake modules, so you don't need the hardware.
- **`hubdeck probe`** has the reverse-engineering tools:
  - `probe info [module]` reads every feature report.
  - `probe listen [module ...]` prints decoded input live.
  - `probe send` sends one report, guarded by a hard denylist of firmware, serial and factory commands.
  - `probe swmode` and `probe image` are for screen modules.
- **Tabler font:** to refresh the bundled font, download `@tabler/icons-webfont` and run `python tools/update_tabler.py <extracted package dir>`.

## Licence

MIT, see [LICENSE.md](LICENSE.md).

## Credits

- Protocol groundwork: [cooltek-sdk-probe](https://github.com/projectzero7/cooltek-sdk-probe) (MIT) and [cooltek-sdk](https://github.com/projectzero7/cooltek-sdk). Our own verified findings are in [docs/protocol/](docs/protocol/README.md).
- Icons: [Tabler Icons](https://tabler.io/icons) (MIT, see `src/hubdeck/assets/tabler/LICENSE`).
