# MasterHub protocol notes

These are the facts hubdeck relies on, with one file per module. For the whole protocol in one
document, see [../masterhub-protocol.md](../masterhub-protocol.md). Each fact is tagged with where it comes from:

- **[hw]**: verified on real hardware by this project
- **[up]**: taken from upstream docs and not re-verified yet
- **[dll]**: read from the disassembly of the vendor SDK DLL, not yet confirmed on hardware
- **[hyp]**: a hypothesis, still to be confirmed

## Credits

- [projectzero7/cooltek-sdk-probe](https://github.com/projectzero7/cooltek-sdk-probe) (MIT): Base, Knob, Roller, Fader and 15-key docs.
- [projectzero7/cooltek-sdk](https://github.com/projectzero7/cooltek-sdk): Dial (M131E) software-mode and serial commands.

## Modules

Each module shows up as its own USB HID device, with VID 0x2516 and usage page 0x0C.

| file | module | PID | family |
| --- | --- | --- | --- |
| [m4315.md](m4315.md) | 15xKey w/ IPS | 0x01FE | screen |
| [m131e.md](m131e.md) | 1xDial w/ IPS | 0x0206 | screen |
| [m0064.md](m0064.md) | Base 4x6 | 0x01F6 | M0 |
| [m003k.md](m003k.md) | 3xKnob | 0x0202 | M0 |
| [m032le.md](m032le.md) | 2xRoller | 0x0204 | M0 |
| [m005s.md](m005s.md) | 5xFader | 0x0200 | M0 |

## Common framing

- **Input report 1** (63 bytes) **[hw]** has the layout `01 <channel tag> <payload len u16 LE> <payload>`. The payload is `<event id> <len> <data…>` **[up]**.
  - M0 family channel tags are `0x2a–0x2e` (channels 0–4) **[up]**.
  - Screen-family channel tags are `0x34–0x38` **[up]**. Channel 0 on the 15-key module is **[hw]**.
- **Output report 2:** 319 bytes plus the report ID on the screen modules, 63 plus the ID on M0 **[hw]** (from the descriptor).
- **Feature report 3** (31 bytes plus the ID) carries commands, `03 <cmd> …` **[hw]**.
- **Extra feature report IDs** declared in each descriptor are the "get" commands, `<id> <len> <data…>` **[up]**.

## How we reverse engineer

1. Read the HID descriptor (`hubdeck probe info` shows the declared report IDs).
2. Passive tests: GET_FEATURE reads and logging inputs while turning or pressing controls.
3. Static analysis of the vendor SDK DLLs in `C:\Program Files\Cooler Master\MasterHUB\resources\build\Release\`. The relevant files are `CoolTEK_SDK_CM_M0`, `CoolTEK_SDK_CM_M131E` and `CoolTEK_SDK_CM_M4315`.
4. USBPcap and Wireshark captures of MasterHUB, decoded with `tools/pcap_decode.py`.
5. Controlled writes, one command at a time, each copied from step 3 or 4.

## Safety

hubdeck refuses to send these commands. The list is hard-coded in `src/hubdeck/device/hid.py`:

- **M0 output commands:** `0x2a` firmware update, `0x2b` set serial, `0x31` save factory data, `0x49` firmware recovery, `0x4a` factory reset.
- **15-key feature commands:** `0x35` set serial, `0x39` firmware recovery, `0x3c` save factory data, `0x3d` factory reset, `0x3e` animation recovery.
- **15-key output commands:** `0x34` firmware update, `0x38` save screen picture.
- **Dial feature commands:** `0x45` set serial, `0x48` firmware recovery, `0x4b` save factory data, `0x4c` factory reset, `0x4d` animation recovery.
- **Dial output commands:** `0x44` firmware update, `0x47` save screen picture.

Feature `0x34` on the 15-key module is **brightness** and is safe. The same number means firmware update only as an output command.

Runtime LED changes use `save = 0`, so they don't wear the flash.
