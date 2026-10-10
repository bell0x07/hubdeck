# MasterHub ↔ Discord (prototype)

Shows your **live Discord mute / deafen state** on keys of the MasterHub 15-key screen module (M4315) and toggles it when you press them. If you mute with the mouse in Discord, the key updates too.

Unofficial. It uses the community reverse-engineered protocol from [cooltek-sdk-probe](https://github.com/projectzero7/cooltek-sdk-probe) (MIT) and Discord's local RPC. Untested on real hardware so far, so expect to tweak.

## 1. Install

```powershell
pip install -r requirements.txt
```

## 2. Hardware test (no Discord needed)

```powershell
python masterhub_discord.py --list     # should show VID 0x2516 / PID 0x01fe
python masterhub_discord.py --test     # paints 1-15 on the keys, prints presses
```

Check the following:

- **Numbers appear on the keys.** If they don't, try again with MasterCanvas closed.
- **Numbers are upright.** The yellow `^` marks the top. If they're rotated, add `--rotate 90`, `180` or `270`.
- **Pressing a key prints its number.** Note the numbers you want for mute and deafen.

## 3. Create a Discord app (one time, for your own account)

1. Go to https://discord.com/developers/applications and click **New Application**. Any name works, for example "MasterHub".
2. Under **OAuth2**, copy the **Client ID** and **Client Secret**. Add the redirect `http://localhost` and click **Save Changes**. It must be the **only** redirect (no trailing slash, no port). With none, or more than one, authorization fails with `Missing "redirect_uri" in request`.
3. Copy `config.example.json` to `config.json` and fill in the ID, the secret, and the key numbers from step 2.

## 4. Run

```powershell
python masterhub_discord.py
```

The first time, Discord shows an authorization popup. Approve it. The token is saved in `%APPDATA%\masterhub-discord\token.json`, so later runs connect silently. The keys show **NO DC** while Discord is closed and recover on their own.

## Living with MasterCanvas

The module only accepts these commands in "software mode", and MasterCanvas uses the same mode. In MasterCanvas, leave your mute and deafen keys **unassigned**. The script repaints its keys every `reassert_seconds` in case MasterCanvas draws over them. If the two still conflict, close MasterCanvas while using this.

## Config

| key | meaning |
| --- | --- |
| `mute_key`, `deafen_key` | key numbers 1–15 as shown by `--test` (`deafen_key` may be `null`) |
| `hold_mute_key` | key 1â€“15 that mutes you while held and restores your previous state on release, or `null` |
| `rotate` | 0 / 90 / 180 / 270 |
| `brightness` | 0–100, or `null` to leave it unchanged |
| `reassert_seconds` | how often to repaint the keys (0 = never) |
| `icons` | optional custom images: `{"mute_on": "C:/icons/muted.png", "mute_off": ..., "deaf_on": ..., "deaf_off": ...}` |

## Autostart

Put a shortcut in `shell:startup` that runs:

```
pythonw.exe C:\path\to\masterhub_discord.py
```
