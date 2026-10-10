# hubdeck

A Stream-Deck-style controller for the **Cooler Master MasterHub 15-key screen module** (M4315). You give each key a [Tabler icon](https://tabler.io/icons), a label or live text, and an action: keystrokes, media keys, launching apps, Windows audio, or Discord mute and deafen with their live state shown on the key. Keys can be grouped into pages, and you configure everything in one YAML file that applies as soon as you save it.

This is an unofficial tool. The device protocol comes from the community project [cooltek-sdk-probe](https://github.com/projectzero7/cooltek-sdk-probe) (MIT). It has been tested on Windows 11 with a real M4315.

## Install

You need Python 3.11 or newer on Windows.

```powershell
git clone https://github.com/bell0x07/hubdeck.git
cd hubdeck
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

This installs two commands into `.venv\Scripts`: `hubdeck` (the command line) and `hubdeck-tray` (the background app with a tray icon).

## First run

```powershell
.\.venv\Scripts\hubdeck.exe list     # the 15-key module should be marked
.\.venv\Scripts\hubdeck.exe test     # paints 1-15 on the keys and prints key presses
.\.venv\Scripts\hubdeck.exe init     # writes an example config.yaml and secrets.yaml
.\.venv\Scripts\hubdeck.exe run      # runs in the foreground (Ctrl+C to quit)
```

During `test`, the numbers should be upright: the yellow `^` marks the top. If they aren't, set `device.rotate`. If MasterCanvas draws over the keys, unassign those keys in MasterCanvas or close it.

To use it every day, run `hubdeck-tray`. Clicking the tray icon opens the config for editing. The tray menu can switch pages, reload, open the log, and open this repo.

To start it at login, put a shortcut to `.venv\Scripts\hubdeck-tray.exe` in `shell:startup`.

## Files

| file | where |
| --- | --- |
| config | `%APPDATA%\hubdeck\config.yaml` (or `--config path`) |
| secrets | `secrets.yaml` next to the config |
| log | `%APPDATA%\hubdeck\hubdeck.log` |

`hubdeck where` prints these paths. `hubdeck validate` checks the config and lists its pages and plugins. `validate -v` also lists every plugin's actions and states.

## Config

```yaml
device:
  ownership: shared      # shared: only touch keys used in the config | exclusive: own all 15
  rotate: 0              # 0 / 90 / 180 / 270
  brightness: null       # 0-100, or null to leave it alone
  reassert_seconds: 5    # repaint keys this often in case MasterCanvas draws over them

defaults:
  long_press_ms: 500
  double_press_ms: 250
  style: {bg: "#19191e", fg: "#3cc86e", label_color: "#ffffff"}

plugins:
  discord:
    client_id: !secret discord_client_id
    client_secret: !secret discord_client_secret

start_page: main
pages:
  main:
    keys:
      1:
        action: {page: media}                  # open another page
        face: {icon: music, label: Media}
      2:
        action: {keys: "ctrl+shift+esc"}
        on_long_press: {run: "taskmgr.exe"}
        face: {icon: keyboard, label: TaskMgr}
      4:
        face: {text: "{system.time:%H:%M}", label: "{system.cpu:.0f}% CPU"}
      11:
        action: discord.toggle_mute
        state: discord.muted
        face:         {icon: microphone, label: LIVE}
        face_on:      {icon: microphone-off, label: MUTED, bg: "#c81e28", fg: white}
        face_unknown: {icon: plug-connected-x, label: NO DC, bg: "#c81e28", fg: white}
  media:
    keys:
      1: {action: page.back, face: {icon: arrow-left, label: Back}}
      3: {action: {media: play_pause}, face: {icon: player-play}}
```

`hubdeck init` writes a fuller example to start from.

- **Keys** are numbered 1â€“15, as shown by `hubdeck test`. If you only need one page, a top-level `keys:` section is enough; you don't need `pages:`.
- **Gestures:** `action` (same as `on_press`), `on_down`, `on_up`, `on_long_press` and `on_double_press`. A key with only a press action fires immediately on key-down. If the key also has a long or double action, the press fires on release, after the gesture is clear.
- **Actions** are written either as `plugin.action` or as `{plugin.action: argument}`. A list runs several actions in order. The short forms are `page`, `keys`, `type`, `media`, `run` and `open`.
- **Faces:**
  - Fields: `icon` (a Tabler name; `filled: true` uses the filled style), `label`, `text`, `bg`, `fg` (icon colour), `label_color` and `font_size`.
  - Live text: `text` and `label` can include `{plugin.state}` or `{plugin.state:format}`.
- **State:** `state: plugin.state` uses `face_on` while the value is true and `face_unknown` while it's unknown, for example when Discord is closed. Fields you don't set in `face_on` or `face_unknown` are taken from `face`.
- **Secrets:** `!secret name` reads `name` from `secrets.yaml`, or from the environment variable `HUBDECK_<NAME>`.
- **Icons:** search with `hubdeck icons <word>`, or browse at https://tabler.io/icons.
- **YAML note:** only `true` and `false` count as booleans, so `label: ON` stays text.

## Plugins

| plugin | actions | states |
| --- | --- | --- |
| `page` | `goto` (`{page: name}`), `back`, `home` | none |
| `keyboard` | `keys`, `key_down`, `key_up`, `media`, `type` | none |
| `launch` | `open` (file, folder, app or URL), `run` (shell command) | none |
| `audio` | `toggle_mute` / `mute` / `unmute` (speakers or microphone), `volume_up`, `volume_down`, `set_volume`, `set_output` | `speaker_muted`, `mic_muted`, `volume`, `output` |
| `system` | none | `time`, `cpu`, `ram` |
| `discord` | `toggle_mute`, `mute`, `unmute`, `toggle_deafen`, `deafen`, `undeafen`, `hold_mute_start`, `hold_mute_end` | `muted`, `deafened`, `holding`, `connected` |

- **Media keys:** `play_pause`, `next`, `prev`, `stop`, `vol_up`, `vol_down` and `mute`.
- **Hold to mute:** put `on_down: discord.hold_mute_start` and `on_up: discord.hold_mute_end` on one key. This works even if you switch pages while holding it.

### Discord setup

The Discord plugin needs a Discord app. You create it once, for your own account:

1. Go to https://discord.com/developers/applications and click **New Application**.
2. Under **OAuth2**, copy the **Client ID** and **Client Secret** into `secrets.yaml`.
3. Add the redirect `http://localhost` and click **Save Changes**. It must be the **only** redirect. If there are none, or more than one, authorization fails with `Missing "redirect_uri" in request`.

The first time hubdeck connects, Discord shows an authorization popup. The login token is then kept in `%APPDATA%\hubdeck\discord_token.json`. Voice RPC only works for the app's owner and for whitelisted testers.

## Sharing the module with MasterCanvas

In `shared` mode, hubdeck paints only the keys used on any page and leaves the rest to MasterCanvas. Leave those keys unassigned in MasterCanvas. `exclusive` mode is for running without MasterCanvas: hubdeck owns all 15 keys and blanks the unused ones.

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

The tests use a fake device, so you don't need the hardware. To refresh the bundled Tabler font, download `@tabler/icons-webfont` and run `python tools/update_tabler.py <extracted package dir>`.

## Licence

MIT, see [LICENSE.md](LICENSE.md).

## Credits

- Device protocol: [cooltek-sdk-probe](https://github.com/projectzero7/cooltek-sdk-probe) (MIT).
- Icons: [Tabler Icons](https://tabler.io/icons) (MIT, see `src/hubdeck/assets/tabler/LICENSE`).
