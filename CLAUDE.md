# Context for Claude Code

hubdeck controls the Cooler Master MasterHub 15-key screen module (M4315, VID 0x2516, PID 0x01FE) on Windows. It's a Python package under `src/hubdeck` and runs in the project's `.venv`, always called as `.\.venv\Scripts\...`. The user-facing docs are in README.md.

## Layout

- `device/m4315.py` contains the HID protocol, verified on real hardware. `device/fake.py` is a fake device for tests.
- `config/schema.py` holds the pydantic models. `config/loader.py` handles YAML, the `!secret` tag, `secrets.yaml`, cross-reference checks and mtime-based change detection.
- `engine.py` is the main loop:
  - polls the device
  - turns key edges into gestures (`gestures.py`) and runs actions
  - turns plugin states into key faces
  - handles hot reload, reconnecting the device and repainting keys
- `plugins/` contains one module per integration. Each plugin declares `actions` and `states`. Slow plugins subclass `ThreadedPlugin`, whose `invoke` only adds the action to a queue. Plugin modules must not import heavy dependencies at import time, because the registry is imported during config validation.
- `render/faces.py` draws faces with Pillow at 4x size and scales them down. `render/tabler.py` reads the bundled Tabler webfont.
- `cli.py` is the `hubdeck` command and `tray.py` is `hubdeck-tray` (pystray). `instance.py` is the guard that allows only one copy to run.

## Protocol (verified)

- **Feature reports** are 32 bytes with report ID 0x03:
  - `03 3a 01` turns software mode on. The module ignores everything else until this is sent.
  - `03 34 <0-100>` sets the brightness.
- **Key image:** 320-byte output reports. The header is `02 36 <chunk u16 LE> <len u16 LE> <key 0-14> <last flag>`, followed by up to 312 bytes of a 72x72 baseline JPEG with 4:4:4 subsampling.
- **Key input:** `01 34 <len u16 LE> 02 0f k1..k15`.
- Windows exposes several HID paths. The driver opens all of them and uses whichever accepts each report type.
- The module sometimes drops off USB by itself (writes stop being accepted). The engine reconnects every 3 s and replays `on_up` for keys that were held.

## Gotchas

- pycaw: `AudioUtilities.GetMicrophone()` returns a raw `IMMDevice`. Wrap it with `CreateDevice`; `GetSpeakers()` already returns the wrapper.
- YAML is loaded with YAML 1.2 booleans only, so `label: ON` stays a string.
- Discord: the app must have exactly one redirect, `http://localhost`. Voice RPC scopes only work for the app owner and whitelisted testers.
- Hot reload applies config changes only. Code changes need a restart.

## Testing

Run `.\.venv\Scripts\python.exe -m pytest`. Engine tests drive `Engine.step()` with a fake clock and `FakeKeyModule`.
