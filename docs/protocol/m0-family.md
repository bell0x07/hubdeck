# M0 family: Base, Knob, Roller, Fader

These four modules share one vendor SDK, `CoolTEK_SDK_CM_M0_V02.05.00.dll`. Module-specific details are in [m0064.md](m0064.md), [m003k.md](m003k.md), [m032le.md](m032le.md) and [m005s.md](m005s.md).

## Reports

- **Output** `02 <cmd> …`, 63 bytes plus the report ID **[hw]** (from the descriptor).
- **Input** `01 <tag> <len u16> <event> <len> data`. Tags `0x2a–0x2e` are channels 0–4 **[up]**.

## Output commands [up]

| cmd | bytes | meaning |
| --- | --- | --- |
| 0x2c | `02 2c <module> <idx> <mode> <save>` | LED mode |
| 0x2d | `02 2d <module> <idx> R G B <save>` | LED colour |
| 0x2e | `02 2e <module> <idx> <0-100>` | LED brightness |
| 0x30 | `02 30 <0-100>` | LED speed (no visible effect reported) |
| 0x32 / 0x33 | | get config data (the reply arrives on input channels 2 and 3) |
| **denied** | 0x2a, 0x2b, 0x31, 0x49, 0x4a | firmware update, set serial, save factory data, firmware recovery, factory reset |

- **LED module IDs:** 0x00 Base, 0x01 Fader, 0x02 Knob. `idx 0xff` selects every zone of the module.
- **LED modes:** 0 off, 1 static, 2 breathing, 3 strobe, 4 spring, 5 cycling, 6 random, 7 wave, 8 water, 9 rainbow. Only modes 1–4 use the colour.
- `save = 1` keeps the setting after a restart. hubdeck uses 0 at runtime.

## Get features [up]

| id | meaning |
| --- | --- |
| 0x2d / 0x2e / 0x2f | firmware info, `2e 0f <4 bytes> <ASCII version>` |
| 0x30 | serial number |
| 0x31 | device info (empty) |
| 0x32 | LED brightness per zone |
| 0x33 | slider values (Fader) |
| 0x34 | LED mode per zone |
| 0x35 | device direction (no response) |
| 0x36 | config info (layout unknown) |
