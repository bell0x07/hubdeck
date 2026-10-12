# hubdeck

A Stream-Deck-style controller for the whole **Cooler Master MasterHub** family:

| module | what hubdeck does with it |
| --- | --- |
| **15-key screen module** | Each key gets a face (a [Tabler icon](https://tabler.io/icons), label, live text, picture or animated GIF) and actions. |
| **Dial with round screen** | Turn it to adjust a value, press it for actions, and show a face with a value gauge, picture or GIF. |
| **3 knobs, 2 rollers, 5 faders** | Volumes (system, microphone, per app) or any action, with knob and fader LEDs that can follow the value. |
| **Base** | RGB lighting that reacts to state, for example red and breathing while you're muted. |

Actions cover keystrokes, media keys, launching apps, system and per-app audio, and Discord mute/deafen/hold-to-mute. Controls can be grouped into pages. Everything lives in one YAML file and applies as soon as you save it. Modules can be plugged in, swapped or mounted rotated while hubdeck runs.

It runs on **Windows** and **macOS**. This is an unofficial tool, tested with real hardware on Windows 11 and macOS. The protocol notes, with sources and what's verified, are in [docs/protocol/](docs/protocol/README.md).

## Install

- **Windows:** run the installer, `hubdeck-<version>-windows-x64.msi` from the [releases](https://github.com/bell0x07/hubdeck/releases). See [Windows](#windows).
- **macOS:** the app, `hubdeck-<version>-macos-arm64.zip` from the releases. See [macOS](#macos).
- **From source** on either: you need Python 3.11 or newer.

**Windows** (PowerShell):

```powershell
git clone https://github.com/bell0x07/hubdeck.git
cd hubdeck
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

**macOS** (Terminal; Python from [python.org](https://www.python.org/downloads/) or `brew install python`):

```bash
git clone https://github.com/bell0x07/hubdeck.git
cd hubdeck
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

This installs two commands into `.venv\Scripts` (Windows) or `.venv/bin` (macOS):
- `hubdeck`: the command line
- `hubdeck-tray`: the background app with a tray (menu bar) icon

## First run

```sh
hubdeck list     # your modules should be marked
hubdeck test     # paints the key numbers and prints key presses
hubdeck init     # writes an example config.yaml and secrets.yaml
hubdeck run      # runs in the foreground (Ctrl+C to quit)
```

From source, run them as `.\.venv\Scripts\hubdeck.exe …` on Windows and `.venv/bin/hubdeck …` on macOS, or activate the venv first. The Windows installer puts `hubdeck` on the PATH.

- **Close MasterHUB first, including its background daemon** (Windows). It drives the same modules, so the two would fight over the screens and lights.
- **For everyday use, run `hubdeck-tray`.** Clicking the tray icon opens the editor (on macOS it opens the menu, where "Open editor" is the first item). The menu can also switch pages, reload, open the log and open this repo.
- **To start at login on Windows,** turn on **Start at login** in the tray menu. It works for the installed app and from source.
- **To start at login on macOS,** see [macOS](#macos) below.

## Windows

### The hubdeck app

1. Download `hubdeck-<version>-windows-x64.msi` from the [releases](https://github.com/bell0x07/hubdeck/releases) (or a development build from the CI artifacts) and run it. It installs for your user only, so it doesn't ask for admin rights.
   - The installer isn't signed, so **SmartScreen** stops it the first time ("Windows protected your PC"). Click **More info › Run anyway**.
2. Start **hubdeck** from the Start menu. It lives in the tray, and clicking the icon opens the editor.
3. `hubdeck` is on your PATH for `hubdeck send …`, `hubdeck list` and the rest. Open a new terminal after installing.
4. **To start at login,** turn on **Start at login** in the tray menu. It shows up in Task Manager › Startup apps as hubdeck.

- **Upgrading:** quit hubdeck from the tray, then run the new MSI. It replaces the installed version, including a development build of the same version. An older version refuses to install over a newer one.
- **Uninstalling:** Settings › Apps › Installed apps › hubdeck. Your config in `%APPDATA%\hubdeck` stays. Turn **Start at login** off first: uninstalling doesn't remove that entry, so Startup apps would keep one that does nothing until you reinstall.
- **What's installed:** `%LOCALAPPDATA%\Programs\hubdeck` holds `hubdeck-tray.exe` (the tray app), `hubdeck.exe` (the command line) and hubdeck's own Python. `hubdeck-<version>-windows-x64.zip` is the same folder, to run from anywhere without installing.

### Building the app

Like the macOS app, it's built on **Linux**, in Docker or GitLab CI, with no Windows or Visual Studio involved. It's made of:
- a standalone Python ([python-build-standalone](https://github.com/astral-sh/python-build-standalone)) and the dependencies' Windows wheels
- `hubdeck.exe` and `hubdeck-tray.exe`, small launchers cross-compiled with **zig**. They run that Python inside their own process, so Windows sees hubdeck (its name and icon, e.g. in Task Manager) rather than python.exe.
- the MSI, made with **wixl** from msitools

Everything is in `packaging/windows/`, and `.gitlab-ci.yml` builds it on every push. In PowerShell, from the repository root:

```powershell
docker build -t hubdeck-windows-builder -f packaging/windows/Dockerfile packaging/windows
docker run --rm -v "${PWD}:/src" -w /src hubdeck-windows-builder
```

That writes `dist\hubdeck-<version>-windows-x64.msi` and the zip. Without Docker, `python packaging/windows/build.py --no-msi` builds just the zip, since there's no wixl for Windows. It needs Python 3.13 with `pip install ziglang pillow packaging`.

## macOS

### The hubdeck app

1. Download `hubdeck-<version>-macos-arm64.zip` (Apple Silicon) from the CI artifacts or releases, unzip it, and drag **hubdeck.app** into **Applications**.
2. Open it. macOS blocks a downloaded build that isn't notarised ("could not verify…"). Allow it once per download, in either of two ways:
   - Try to open it, then go to **System Settings › Privacy & Security** and click **Open Anyway** next to the hubdeck message. Right-click › Open no longer works for this since macOS 15.
   - Or in Terminal: `xattr -dr com.apple.quarantine /Applications/hubdeck.app`
3. hubdeck lives in the menu bar (no Dock icon). Its menu has **Start at login**.
4. For the command line, and for Shortcuts, link it once:

   ```bash
   sudo ln -s /Applications/hubdeck.app/Contents/MacOS/hubdeck /usr/local/bin/hubdeck
   ```

   Then `hubdeck send …`, `hubdeck validate` and the rest work as anywhere else. Started with no arguments, the same file is the menu-bar app.

Running from a source checkout (see [Install](#install)) works too. Permissions then go to Terminal or Python instead of hubdeck, and notifications show the Script Editor icon.

### Differences from Windows

- **Permissions.** macOS asks the first time they're needed, for **hubdeck** (or Terminal/Python when running from source):
  - **Accessibility** (System Settings › Privacy & Security) for the `keyboard` actions. Without it macOS drops the keystrokes, and hubdeck logs which permission is missing. Restart hubdeck after allowing it.
  - **Automation** for per-app volume: macOS asks once whether hubdeck may control the app (e.g. Music). Until you answer, that app's volume is unavailable, but everything else keeps working.
  - The modules themselves need no permission.
- **Keys:** `cmd` (or `command`, `win`) is the Command key and `option` (or `alt`) is Option. `ctrl+c` sends Control-C, so use `cmd+c` to copy. The Mac has no `menu`, `f21`–`f24` or media `stop` key. macOS ignores the Force Quit shortcut (`cmd+option+esc`) when it doesn't come from a real keyboard; `{open: "Activity Monitor"}` is the nearest alternative.
- **Per-app volume:** macOS has no per-app mixer, so `audio.app_volume` only works for apps that expose a volume to AppleScript, such as **Spotify** and **Music**. Name the app as it appears in the Dock, e.g. `arg: Spotify`. Windows names like `spotify.exe` work too, so one config can serve both. Each change takes the app about 0.2 s, so hubdeck sends them in the background and skips to the latest level while you turn.
- **Microphone mute:** a microphone with no mute switch (many USB mics) is muted by setting its input volume to 0 and restoring it afterwards.
- **`open`** also takes an app name, e.g. `{open: Spotify}`.
- **The 2xRoller module** sent no input on macOS in testing (the knobs, Dial and keys work). It's not known yet whether that's macOS or the test unit.
- **Start at login from source:** save this as `~/Library/LaunchAgents/com.github.hubdeck.plist`, with the path to your clone, then run `launchctl load ~/Library/LaunchAgents/com.github.hubdeck.plist`:

  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0">
  <dict>
    <key>Label</key><string>com.github.hubdeck</string>
    <key>ProgramArguments</key><array><string>/Users/YOU/hubdeck/.venv/bin/hubdeck-tray</string></array>
    <key>RunAtLoad</key><true/>
  </dict>
  </plist>
  ```

### Building the app

The app is built on **Linux**, in Docker or GitLab CI, with no Mac or Xcode involved. It's made of:
- a standalone Python ([python-build-standalone](https://github.com/astral-sh/python-build-standalone))
- the dependencies' macOS wheels
- a small launcher cross-compiled with **zig**, so macOS sees hubdeck.app itself: its name, icon, notifications and permissions

It's signed with **rcodesign**. Everything is in `packaging/macos/`, and `.gitlab-ci.yml` runs it on every push.

```bash
docker build --platform linux/amd64 -t hubdeck-macos-builder -f packaging/macos/Dockerfile packaging/macos
docker run --rm --platform linux/amd64 -v "$PWD:/src" -w /src hubdeck-macos-builder
```

That writes `dist/hubdeck-<version>-macos-arm64.zip`. On a Mac, `python packaging/macos/build.py` also works directly, with `pip install ziglang packaging` and rcodesign (or codesign) available.

**Signing** decides what the user sees:

| set in the environment / CI variables | result |
| --- | --- |
| nothing | ad-hoc: works, but **macOS forgets the permissions on every update**, and each download needs **Open Anyway** once |
| `MACOS_SIGN_PEM` (file) with a **self-signed** certificate | permissions **survive updates**; each download still needs **Open Anyway** once |
| the same with a **Developer ID** certificate, plus `MACOS_NOTARY_API_KEY` (file) | signed and notarised: opens normally ($99/year Apple Developer Program) |

To make a self-signed certificate once, run this. Keep the file safe: it holds the private key.

```bash
rcodesign generate-self-signed-certificate --person-name "hubdeck" --validity-days 3650 \
  --pem-unified-file hubdeck-signing.pem
```

Then, in GitLab › Settings › CI/CD › Variables, add `MACOS_SIGN_PEM` with **type File**, and paste the file's contents as the value. A `.p12` works too (`MACOS_SIGN_P12` plus `MACOS_SIGN_P12_PASSWORD`), for local builds.

## Releasing

The version lives in one place, `__version__` in `src/hubdeck/__init__.py`.

- **Branch pipelines** build a development version named after the branch, e.g. `hubdeck-0.2.0-feat-windows-app.48-windows-x64.msi` and `hubdeck-0.2.0-feat-windows-app.48-macos-arm64.zip`. They're kept as CI artifacts for 30 days and never released. On the job page, *Browse* › click a file to download it as is.
- **A release:**
  1. Bump `__version__` and merge to `main`.
  2. Tag that commit `vX.Y.Z`, e.g. `git tag -a v0.2.0 -m "What's new" && git push origin v0.2.0` (with GitLab as `origin`), or **Code › Tags › New tag** in GitLab. The tag message becomes the release notes.
  3. The tag pipeline tests, builds and publishes the release on **GitLab** (the files go to the package registry) and on **GitHub**: the macOS zip, and the Windows MSI and zip. A tag that doesn't match `__version__` fails the build.
- **Setup, once:**
  - protect `v*` under GitLab › Settings › Repository › **Protected tags**
  - add a **masked, protected** CI variable `GITHUB_TOKEN`, a GitHub token for the repository with *Contents: read and write* (the mirror's token works)

  Without the token, releases go to GitLab only.

## Editor

hubdeck has a visual editor that runs in your browser. It's served by hubdeck on `127.0.0.1` only.

- **Open it** by clicking the tray icon ("Open editor"). Opened that way, it shows **live state** and which page is on the device. Without the tray, `hubdeck gui` opens it on its own.
- **Your device is drawn as it sits on the desk.** The Base grid is horizontal 6×4 or vertical 4×6. Modules sit where you drag them, rotated as mounted (↻ on each module), with every control numbered in reading order.
- **Click any control to edit it:**
  - face (icon search, label, colours, image/GIF), with a live preview rendered exactly like the device
  - press, long-press and double-press actions, key down/up
  - state-driven faces
  - turning (a value target or actions)
  - fader targets
- **Every item also has a YAML tab** for anything the form doesn't cover.
- **Global settings:** pages (add, rename, delete, start page), **Lighting**, **Device & modules** (base orientation, mounting, backlight) and the whole file as **YAML**.
- **Saving checks the whole config first.** An invalid change is shown and never written.
- **Your comments and hand-aligned spacing in `config.yaml` are kept;** only the lines you changed are rewritten.
- **A running hubdeck applies saved changes immediately.**

## Files

| file | where |
| --- | --- |
| config | `config.yaml` in the hubdeck folder (or `--config path`) |
| secrets | `secrets.yaml` next to the config |
| log | `hubdeck.log` in the hubdeck folder |
| downloaded images | `cache` in the hubdeck folder |

The hubdeck folder is `%APPDATA%\hubdeck` on Windows and `~/Library/Application Support/hubdeck` on macOS. Set `HUBDECK_HOME` to use another folder.

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

`hubdeck init` writes a commented starter config. **[examples/config.yaml](examples/config.yaml)** is a complete, working config that uses every module and feature: pages, gestures, live text, Discord, GIFs, the Dial gauge, knobs, rollers, faders, lighting and rotated mounting. If you only need one page, put `keys:`, `dial:`, `knobs:`, `rollers:` and `faders:` at the top level instead of `pages:`.

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
- **Colours** are `#rrggbb`, a name like `red`, or `accent`: your system accent colour (macOS: System Settings › Appearance; Windows: Settings › Personalization › Colors), followed within about 2 seconds of a change, and hubdeck green elsewhere. Icons default to `accent`; for example `lighting: {base: {color: accent}}` lights the Base in it.
- **Live text:** `text` and `label` can include `{plugin.state}` or `{plugin.state:format}`, for example `{system.time:%H:%M}` or `{system.cpu:.0f}%`. For multi-line text, `:line1`, `:line2`… picks one line.
- **Control value:** on a control with a turn target, `{value}` shows the target's current value, and `gauge: true` draws a ring around the Dial.
- **Pictures:** `image: file.png`, `.jpg` or **animated `.gif`**, or a picture sent from outside, `image: "{vars.picture}"` (see [`hubdeck send`](#driving-hubdeck-hubdeck-send)).
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

A fader sets its target to its position (0–100). **Soft takeover:** a fader only takes control once it reaches or passes the target's current value, so moving it never makes the volume jump. When the value is changed elsewhere (the keyboard, the app, another fader), the fader lets go and has to reach the new value again.

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
| `keyboard` | `keys`, `key_down`, `key_up`, `media`, `type` (`cmd`/`win` is Command on macOS) | none | none |
| `launch` | `open` (file, folder, app or URL; on macOS also an app name), `run` (shell command) | none | none |
| `audio` | `toggle_mute` / `mute` / `unmute` (speakers or microphone), `volume_up`, `volume_down`, `set_volume`, `set_output` | `speaker_muted`, `mic_muted`, `volume`, `mic_volume`, `output` | `volume`, `mic_volume`, `app_volume` (arg: process name, e.g. `spotify.exe`; on macOS an app with an AppleScript volume, e.g. `Spotify`) |
| `system` | none | `time`, `cpu`, `ram` | none |
| `discord` | `toggle_mute`, `mute`, `unmute`, `toggle_deafen`, `deafen`, `undeafen`, `hold_mute_start`, `hold_mute_end` | `muted`, `deafened`, `holding`, `connected` | none |
| `vars` | `set` (`{vars.set: {mode: work}}`), `toggle` (`{vars.toggle: dnd}`) | any name you set, e.g. `vars.mode` | none |
| `shortcuts` (macOS) | `run` (`{shortcuts.run: Lights off}` or `{name: ..., input: ...}`), `refresh` | one per output you define, e.g. `shortcuts.weather` | none |

- **Media keys:** `play_pause`, `next`, `prev`, `stop`, `vol_up`, `vol_down` and `mute`.
- **Hold to mute:** put `on_down: discord.hold_mute_start` and `on_up: discord.hold_mute_end` on one key. This works even if you switch pages while holding it.
- **App volume:** applies to every audio session of that process. An app that isn't playing audio has no value, so faders don't take it over.

### Discord setup

The Discord plugin needs a Discord app. You create it once, for your own account:

1. Go to https://discord.com/developers/applications and click **New Application**.
2. Under **OAuth2**, copy the **Client ID** and **Client Secret** into `secrets.yaml`.
3. Add the redirect `http://localhost` and click **Save Changes**. It must be the **only** redirect. If there are none, or more than one, authorization fails with `Missing "redirect_uri" in request`.

The first time hubdeck connects, Discord shows an authorization popup. The login token is then kept in `discord_token.json` in the hubdeck folder. Voice RPC only works for the app's owner and for whitelisted testers.

## Shortcuts, scripts and automation

hubdeck works in both directions: controls can run your automations, and automations can drive hubdeck.

### Driving hubdeck: `hubdeck send`

While hubdeck runs (`hubdeck run` or the tray), any script or automation can send it commands:

```sh
hubdeck send discord.toggle_mute       # any action, written as in the config
hubdeck send page media                # ...with an argument, read like YAML
hubdeck send audio.set_volume 30
hubdeck send set focus Work            # set vars.focus
hubdeck send set weather -             # set vars.weather to the text on stdin, as is
hubdeck send image picture photo.jpg   # show a picture: image: "{vars.picture}" (- reads stdin)
hubdeck send get discord.muted         # print a state (empty while unknown)
```

- **Values (`vars`):** `set` stores a value that keys and lighting use like any state: `{vars.focus}` in text or labels, `state: vars.dnd` with `face_on`, or a lighting `state`. Keys can change them too (`{vars.toggle: dnd}`). Starting values go under `plugins: {vars: {focus: none}}`, and a name that was never set is unknown.
- **From the Shortcuts app (macOS):** end a Shortcut with a **Run Shell Script** action, using the full path and **Pass Input: to stdin**. Shortcuts' Advanced settings must allow running scripts.
  - Text: `/Users/you/hubdeck/.venv/bin/hubdeck send set weather -` sends the Shortcut's result. Show it with `{vars.weather}`, or one line of it with `{vars.weather:line1}`.
  - Pictures: `…/hubdeck send image picture -` with a key face `image: "{vars.picture}"`. Put a **Convert Image** (to PNG) action before it, because iPhone photos are HEIC. Pictures are scaled to 480 px and kept in `cache/sent` in the hubdeck folder; each new one replaces the last.
  - A Shortcut that pushes its result can run from anywhere: an automation, the menu bar, or a key (`{shortcuts.run: …}`).
- **Errors:** an unknown action, a bad argument or a plugin that the config doesn't use is reported straight away, with a non-zero exit code.
- **How it works:** hubdeck listens on 127.0.0.1 only, on a random port. It keeps the port and a per-run token in `control.json` in the hubdeck folder, readable only by you. It works the same on Windows.

### Running Shortcuts (macOS)

```yaml
plugins:
  shortcuts:
    outputs:                               # each becomes the state shortcuts.<name>
      weather: {run: "Get weather", every: 600}   # seconds, at least 10 (default 300)

keys:
  1: {action: {shortcuts.run: "Lights off"}, face: {icon: bulb-off, label: Lights}}
  2: {action: {shortcuts.run: {name: "Set scene", input: movie}}}       # input: text the Shortcut receives
  3: {action: {shortcuts.refresh: weather}, face: {text: "{shortcuts.weather}"}}
```

- **`run`** runs the Shortcut in the background, so a slow one never holds up the controls.
- **Outputs** run on their schedule (and on `refresh`), and whatever text the Shortcut returns becomes the state.
- **The editor** offers "Run a Shortcut" with your Shortcuts as suggestions, but only on macOS. A config with Shortcut actions still loads elsewhere; the actions just log that Shortcuts aren't available.

### Example: weather on a key

A Shortcut named **hubdeck weather** with three actions:

1. **Get Current Weather**.
2. **Text**, with two lines: Control-click the box, **Insert Variable** › *Weather Conditions*, click the inserted token and choose **Temperature**. On the second line, do the same and choose **Condition**.
3. **Run Shell Script** `/Users/you/hubdeck/.venv/bin/hubdeck send set weather -`, with **Input:** *Text* and **Pass Input:** *to stdin*.

```yaml
keys:
  15:
    action: {shortcuts.run: "hubdeck weather"}     # press to update now
    face: {text: "{vars.weather:line1}", label: "{vars.weather:line2}"}
```

Run it once with ▶ to allow Location access. Add an hourly automation in Shortcuts to keep the key current.

## Hot-plugging and sharing

- **hubdeck only opens the modules your config uses.** It reconnects to them after they drop off USB, and picks up modules plugged in later.
- **The Base only detects newly inserted modules when it powers up.** After adding a module, unplug and replug the Base's USB cable. Removing a module is noticed immediately.
- **`shared` mode** touches only what the config uses and leaves the rest to other software. **`exclusive` mode** owns everything and blanks unused keys.

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"     # macOS: .venv/bin/python -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest                      # macOS: .venv/bin/python -m pytest
```

- **Tests** use fake modules, so you don't need the hardware.
- **OS-specific code** is all in `src/hubdeck/platforms/`: the interface in `base.py`, one package per OS, and `posix.py` for what macOS and Linux share. Supporting another OS means implementing `Platform` there.
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
