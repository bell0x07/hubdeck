# Cooler Master MasterHub: USB HID protocol

This is everything hubdeck knows about talking to the MasterHub modules directly, without
Cooler Master's MasterHUB app, in one document. The per-module notes in
[protocol/](protocol/README.md) hold the raw observations behind it.

Every fact is tagged with where it comes from:

- **[hw]**: verified on real hardware by this project
- **[up]**: from upstream reverse-engineering docs, not re-verified yet
- **[dll]**: read from the disassembly of the vendor SDK DLL, not confirmed on hardware
- **[hyp]**: a hypothesis

> **Warning.** Some commands rewrite firmware, serial numbers or factory data. They're listed in
> [Commands that must never be sent](#7-commands-that-must-never-be-sent). Don't send anything not
> documented here without a capture or vendor-SDK evidence for it.

## 1. Devices

The modules snap onto the Base. Each one is **its own USB HID device**, with vendor ID `0x2516`
and usage page `0x0C` **[hw]**. They form two families with different report layouts:

| module | code | PID | family | output report | input tag (channel 0) | controls | LEDs | screen |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15xKey w/ IPS | M4315 | `0x01FE` | screen | 320 B | `0x34` | 15 keys | none | 15 × 72×72 |
| 1xDial w/ IPS | M131E | `0x0206` | screen | 320 B | `0x44` | turn, press | none | 240×240, round |
| Base 4x6 | M0064 | `0x01F6` | M0 | 64 B | `0x2A` | none | 1 zone | none |
| 3xKnob | M003K | `0x0202` | M0 | 64 B | `0x2A` | 3 knobs: turn, press | 3 zones | none |
| 2xRoller | M032LE | `0x0204` | M0 | 64 B | `0x2A` | 2 rollers: scroll | none | none |
| 5xFader | M005S | `0x0200` | M0 | 64 B | `0x2A` | 5 faders, 0–100 | 5 zones | none |

Report sizes include the report ID byte. Sizes, PIDs and tags are **[hw]**.

- **Windows** may expose several HID paths per module, one per top-level collection. Open all of
  them and remember which one accepts each report type **[hw]**.
- **macOS** gives one path per module through IOHIDManager and opens it **exclusively**: while
  one program has a module open, no other can **[hw]**. Reading input needs no permission.
- **Attaching modules [hw]:** the Base only detects a newly attached module when it powers up.
  Removing a module is noticed immediately. After attaching one, unplug the Base's USB cable and
  plug it back in.
- **MasterHUB [hw]:** the vendor app and its elevated service `MHDaemon` drive the same modules.
  Close both, or the two programs fight over screens and lights.

## 2. Report framing (all modules)

| report | ID | size incl. ID | direction | layout |
| --- | --- | --- | --- | --- |
| input | `0x01` | 64 B | device → host | `01 <tag> <len u16 LE> <payload…>` |
| output | `0x02` | 320 B (screen) or 64 B (M0) | host → device | `02 <cmd> …`, zero-padded |
| feature | `0x03` | 32 B | host → device (set) | `03 <cmd> …`, zero-padded |
| "get" features | module-specific | 32 B | device → host (get) | `<id> <len> <data…>` |

- **Input [hw]:** `<tag>` is the module family's channel-0 tag plus the channel (0–4). The
  payload is `<event id> <len> <data…>` **[up]**, which holds for every event below **[hw]**.
  - Screen modules: tags `0x34–0x38` (15-key) and `0x44–0x48` (Dial). Only channel 0 is used for
    controls **[hw]**.
  - M0 modules: tags `0x2A–0x2E` **[up]**. Controls arrive on channel 0, the Base's heartbeat on
    channel 1 **[hw]**.
- **"Get" features [hw]:** each module's descriptor declares extra feature report IDs. Reading
  them (GET_FEATURE) returns information: firmware, serial number, saved settings. Sections 3.6
  and 4.4 list them.

## 3. Screen modules: 15xKey (M4315) and Dial (M131E)

Both screen modules use the same command set, with the Dial's numbers shifted by `0x10`
**[hw]**.

### 3.1 Software mode, first

The module ignores everything else until software mode is on **[hw]**:

| module | feature report | source |
| --- | --- | --- |
| 15-key | `03 3a 01` software mode on | [hw] |
| Dial | `03 49 01` software mode on | [hw] |
| both | the same with `00`: back to hardware mode | [up] |

### 3.2 Backlight and solid colours

| module | bytes | meaning | source |
| --- | --- | --- | --- |
| 15-key | feature `03 34 <0-100>` | backlight brightness | [hw] |
| Dial | feature `03 44 <0-100>` | backlight brightness (the DLL rejects > 100); reading back `0x4c` returns the value set | [hw] |
| 15-key | feature `03 36 R G B` | fill the whole screen with one colour | [up] |
| 15-key | feature `03 37 <key> R G B` | fill one key with one colour | [up] |
| Dial | feature `03 46 R G B` | fill the screen with one colour (`03 46 ff 00 00` = red) | [hw] |

Feature `0x34` on the 15-key module is **brightness**. The same number as an *output* command is
firmware update (section 7).

### 3.3 Pictures: chunked JPEG upload

A picture goes out as a sequence of output reports **[hw]**:

```
byte:  0    1      2–3            4–5          6        7       8 … 319
       02   <cmd>  <chunk u16 LE> <len u16 LE> <index>  <last>  <len bytes of data>, zero-padded
```

| field | 15-key | Dial |
| --- | --- | --- |
| `cmd` | `0x36` | `0x45` |
| `index` | key 0–14 (row-major: index = row × 5 + column, top-left = 0) | `0x00` |
| picture | 72×72 | 240×240 |

- **Chunks:** `chunk` counts from 0. Each carries up to 312 bytes (report length − 8). `len` is
  this chunk's byte count. `last` is `1` on the final chunk and `0` before it.
- **Data:** a **baseline JPEG with 4:4:4 chroma subsampling** (no progressive, no 4:2:0) is
  displayed as-is **[hw]**. hubdeck uses quality 90 for keys and 85 for the Dial.
  - The vendor DLL also takes BMP: data starting with `BM` keeps the 54-byte header, drops row
    padding and flips the rows to top-down 24-bit **[dll]**. Whether the firmware accepts it is
    unconfirmed; JPEG is enough.
- **Example:** a 72×72 key JPEG of 2,825 bytes is 10 reports: chunks 0–8 with 312 bytes each, and
  chunk 9 with 17 bytes and `last = 1`.
- A 240×240 Dial picture at quality 85 is about 5–19 KB, or 16–61 reports **[hw]**.
- Pictures show upright as sent. For a module mounted rotated, rotate the picture before
  encoding.
- `02 35 …` (the 15-key's full 480×272 screen, [up], work in progress) and the Dial's
  `02 47 …` (store a picture in flash) aren't used.

### 3.4 Speed [hw]

- **Dial:** a 240×240 frame of ~5 KB writes in about 5 ms. Animation ran smoothly at 10, 25 and
  ~70 fps, the last as fast as hidapi could write, so encoding is the limit, not the module.
- **15-key:** all 15 keys with different 2.3–3.9 KB pictures take about **36 ms**, so about **28
  full frames per second** (around 1.1 MB/s). Only send the keys whose picture changed.

### 3.5 Input [hw]

| module | report | meaning |
| --- | --- | --- |
| 15-key | `01 34 <len> 00 \| 02 0f k1 … k15` | state of all 15 keys, `01` = held, `00` = up |
| Dial | `01 44 03 00 \| 03 01 00` | turned one detent **clockwise** |
| Dial | `01 44 03 00 \| 03 01 01` | turned one detent counter-clockwise |
| Dial | `01 44 03 00 \| 04 01 01` / `… 00` | pressed / released |

The Dial's direction byte is the **opposite of the knobs'** (section 4.2): `00` is clockwise here.

### 3.6 "Get" features

**15-key (`0x37–0x3e`)** [up] unless marked:

| id | meaning |
| --- | --- |
| `0x37` / `0x38` / `0x39` | firmware info (MasterHUB reads `0x38`) |
| `0x3a` | serial number, e.g. `MHKD15AA43…` [hw] |
| `0x3b` | device info, `3b 0b 14 03 05 0f e0 01 10 01 01 48 48` [hw] |
| `0x3c` | backlight, `3c 01 64` |
| `0x3d` | animation info |
| `0x3e` | config info |

**Dial (`0x47–0x4e`)** [hw], read from a module with firmware 01.00.04.00:

| id | reply | meaning |
| --- | --- | --- |
| `0x47` | `0f df 07 5a 29 "01.00.00.00"` | firmware info (bootloader?) |
| `0x48` / `0x49` | `0f 26 a0 d8 0a "01.00.04.00"` | firmware info (MasterHUB reads `0x48`) |
| `0x4a` | `14 "MHDD01AA22…"` | serial number |
| `0x4b` | `0b 14 00 00 00 f0 00 f0 00 01 f0 f0` | device info |
| `0x4c` | `01 64` | backlight = 100 |
| `0x4d` | `11 c3 07 4c 0d 95 7c 03 00 …` | animation info (layout unknown) |
| `0x4e` | `10 92 75 5b 0f 74 00 00 00 …` | config info (layout unknown) |

**Device info decoded [hw]**, from the two modules lined up byte by byte:

| byte | 15-key | Dial | meaning |
| --- | --- | --- | --- |
| 1 | `14` | `14` | ? |
| 2–3 | `03 05` | `00 00` | button rows, columns |
| 4 | `0f` | `00` | button count |
| 5–6 | `e0 01` = 480 | `f0 00` = 240 | screen width (u16 LE) |
| 7–8 | `10 01` = 272 | `f0 00` = 240 | screen height (u16 LE) |
| 9 | `01` | `01` | ? |
| 10–11 | `48 48` = 72, 72 | `f0 f0` = 240, 240 | button picture width, height |

### 3.7 The 15 keys as one display [hw]

- **Bezel:** about **22 px** between neighbouring key screens, at the screens' 72 px, the same
  between columns and rows.
  - It was measured with test pictures cut as if the bezel were 0–40 px. At 22 px, steep and
    shallow lines and circles around the points where four keys meet continued straight across
    the key edges.
  - So one picture over the module is drawn on 5 × 72 + 4 × 22 = **448 × 260** px (260 × 448
    mounted sideways), and the strips behind the bezels are left out.
- **Text:** text across several keys is hard to read, because the bezels hide about a sixth of a
  character's height. Showing every pixel instead (no bezel strips left out) reads worse. One
  character per key reads best.

### 3.8 Quirks [hw]

- The 15-key module sometimes drops off USB by itself, and image writes stop being accepted.
  Reopening the device recovers it.
- The Dial's inputs arrived while MasterHUB was running, so whether input needs software mode
  without MasterHUB is unknown. hubdeck always turns it on.

## 4. M0 family: Base, Knobs, Rollers, Faders

All four share one vendor SDK, `CoolTEK_SDK_CM_M0_V02.05.00.dll`, and the same commands.
Commands are output reports `02 <cmd> …`, 64 bytes including the ID.

### 4.1 LEDs

| cmd | bytes | meaning | source |
| --- | --- | --- | --- |
| `0x2c` | `02 2c <module> <idx> <mode> <save>` | LED mode | [hw] |
| `0x2d` | `02 2d <module> <idx> R G B <save>` | LED colour, applied immediately | [hw] |
| `0x2e` | `02 2e <module> <idx> <0-100>` | LED brightness: 5 is almost dark, 100 full | [hw] |
| `0x30` | `02 30 <0-100>` | LED effect speed (no visible effect reported) | [up] |
| `0x32` / `0x33` | | request config data; the reply arrives on input channels 2 and 3 | [up] |

- **`<module>`** (the LED target): `0x00` Base, `0x01` Fader, `0x02` Knob **[hw]**. The Roller has
  no LEDs.
- **`<idx>`:** the zone, or `0xff` for every zone of that module **[hw]**. Base: one zone (0).
  Knobs: 0–2, the same index as their input (section 4.2). Faders: 0–4, left to right.
- **`<mode>`:** 0 off, 1 static, 2 breathing, 3 strobe, 4 spring, 5 cycling, 6 random, 7 wave,
  8 water, 9 rainbow **[up]**. Only 1–4 use the colour. Static and breathing are **[hw]**.
- **`<save>`:** `1` keeps the setting after a power cycle, `0` doesn't. **Use `0` for anything
  live**, to spare the flash. hubdeck always does.
- **Saved, not live [hw]:** with `save = 0`, the "get" features `0x32` (brightness) and `0x34`
  (mode) keep returning the *saved* values, not what the LEDs show. Track the live state yourself.
- **Update rate [hw]:** colour writes at 20, 50 and 250 per second all looked smooth. hidapi
  manages about 250 writes per second, so software animations aren't limited by the module.
- **Colour accuracy [hw]:** the LEDs need gamma correction, about 2.2. Without it, green shows
  turquoise and red shows pink.

### 4.2 Input [hw]

All on channel 0 (tag `0x2a`) unless noted. Byte values are per control, in module index order.

| module | report | meaning |
| --- | --- | --- |
| Knobs | `01 2a 05 00 \| 00 03 d0 d1 d2` | turned one detent: `01` = clockwise, `00` = counter-clockwise, `ff` = this knob didn't move. One report per detent. |
| Knobs | `01 2a 05 00 \| 03 03 p0 p1 p2` | press state: `01` held, `00` released. One report for down and one for up. |
| Rollers | `01 2a 04 00 \| 01 02 r0 r1` | scrolled one notch: `00` / `01` are the two directions, `ff` = no change. Upstream calls `00` "up" [up]. Rollers can't be pressed. |
| Faders | `01 2a 07 00 \| 02 05 s0 s1 s2 s3 s4` | absolute positions, 0 (bottom) to 100 (top), always all five. Sent about every 20 ms while a fader moves (~50/s). |
| Base | `01 2b 01 00 01` (channel 1) | heartbeat, about every 0.5 s. Upstream documented its payload as `00` [up]; ours sends `01`. |

- **Knob positions:** index 0 = bottom-left, 1 = bottom-right, 2 = top, with the module mounted
  with one knob on top. **The input index is the LED index** (checked by turning each knob and
  watching which LED belonged to it).
- **Roller positions:** index 0 = right, 1 = left, mounted horizontally.
- **Fader positions:** index 0–4 = left to right, matching the LED index.
- One knob report carries either a rotation or a press, never both. Whether turning while
  pressed changes anything is unknown.

### 4.3 Reading a fader's position

The first fader report after connecting gives where each fader sits; there's also "get" feature
`0x33` (`05 00 00 00 00 00` with all five down) **[hw]**. Faders aren't motorised, so software
that maps them to a value it doesn't control should only follow a fader once it reaches that value
("soft takeover").

### 4.4 "Get" features

[up] unless marked:

| id | meaning | modules |
| --- | --- | --- |
| `0x2d` / `0x2e` / `0x2f` | firmware info, `2e 0f <4 bytes> <ASCII version>` | all |
| `0x30` | serial number | all |
| `0x31` | device info (empty on all ours) [hw] | all |
| `0x32` | LED brightness per zone, e.g. `03 64 64 64` on the knobs [hw] | Base, Knobs, Faders |
| `0x33` | fader positions [hw] | Faders |
| `0x34` | LED mode per zone, e.g. `01 09` = rainbow on the Base [hw] | Base, Knobs, Faders |
| `0x35` | device direction (no response; `00` on the Base [hw]) | Base |
| `0x36` | config info (layout unknown) | all |

Firmware and serials seen [hw]:

| module | firmware | serial |
| --- | --- | --- |
| Base | 02.00.04.00 | `MHBS01AA46…` |
| Knobs | 01.00.05.00 | `MHKB03AA22…` |
| Rollers | 01.00.03.01 | `MHRR02AA41…` |
| Faders | 01.00.05.00 | `MHFD05AA42…` |

## 5. Getting started: a minimal session

1. Enumerate HID devices with vendor `0x2516`, and pick the module by product ID (section 1).
2. **Screen module:**
   1. Turn software mode on (3.1).
   2. Optionally set the backlight (3.2).
   3. Send a picture per key, or one for the Dial (3.3).
   4. Read input reports (3.5).
3. **M0 module:**
   1. Set mode, colour and brightness with `save = 0` (4.1).
   2. Read input reports (4.2).
4. On exit there's nothing that must be restored. `03 3a 00` / `03 49 00` switch a screen module
   back to hardware mode [up].

## 6. How this was found

1. **HID descriptors:** the report IDs and sizes each module declares (`hubdeck probe info`).
2. **Passive tests:** GET_FEATURE reads, and logging input while turning, pressing and sliding
   every control.
3. **Static analysis of the vendor SDK DLLs** shipped with MasterHUB: `CoolTEK_SDK_CM_M0`,
   `CoolTEK_SDK_CM_M131E` and `CoolTEK_SDK_CM_M4315` in
   `C:\Program Files\Cooler Master\MasterHUB\resources\build\Release\`.
   - Each exported `*_CM_<module>` function wraps a generic `*_CM` function that fills a buffer and
     calls the DLL's bundled hidapi.
4. **USB captures** of MasterHUB with USBPcap and Wireshark.
5. **Controlled writes,** one command at a time, each taken from step 3 or 4, with MasterHUB
   closed.
6. **Visual measurements** for the 15-key display: test patterns on the keys (section 3.7).

### Credits

- [projectzero7/cooltek-sdk-probe](https://github.com/projectzero7/cooltek-sdk-probe) (MIT): the
  Base, Knob, Roller, Fader and 15-key protocol, which hubdeck started from.
- [projectzero7/cooltek-sdk](https://github.com/projectzero7/cooltek-sdk): the Dial's software-mode
  and serial-number commands.

## 7. Commands that must never be sent

They rewrite firmware, serial numbers or factory data. hubdeck refuses them, with the list
hard-coded in `src/hubdeck/device/hid.py`. hubdeck also refuses anything not on its list of
verified-safe commands, unless explicitly told otherwise.

| module | report | command | does |
| --- | --- | --- | --- |
| M0 family | output | `0x2a` | firmware update |
| M0 family | output | `0x2b` | set serial number |
| M0 family | output | `0x31` | save factory data |
| M0 family | output | `0x49` | firmware recovery |
| M0 family | output | `0x4a` | factory reset |
| 15-key | feature | `0x35` | set serial number |
| 15-key | feature | `0x39` | firmware recovery |
| 15-key | feature | `0x3c` | save factory data |
| 15-key | feature | `0x3d` | factory reset |
| 15-key | feature | `0x3e` | animation recovery |
| 15-key | output | `0x34` | firmware update |
| 15-key | output | `0x38` | save screen picture to flash |
| Dial | feature | `0x45` | set serial number |
| Dial | feature | `0x48` | firmware recovery |
| Dial | feature | `0x4b` | save factory data |
| Dial | feature | `0x4c` | factory reset |
| Dial | feature | `0x4d` | animation recovery |
| Dial | output | `0x44` | firmware update |
| Dial | output | `0x47` | save screen picture to flash |

Commands verified as safe:

| module | feature | output |
| --- | --- | --- |
| 15-key | `0x34`, `0x36`, `0x37`, `0x3a` | `0x36` |
| Dial | `0x44`, `0x46`, `0x49` | `0x45`, `0x48`, `0x49` |
| M0 family | none | `0x2c`, `0x2d`, `0x2e`, `0x30`, `0x32`, `0x33` |

## 8. Still unknown

- **15-key:** the full-screen picture command `02 35` (480×272?), and the layout of feature
  `0x3d` (animation info) and `0x3e` (config info).
- **Dial:** whether BMP pictures are accepted, and whether input needs software mode without
  MasterHUB.
- **Knobs:** how many physical LEDs each knob has (the protocol has one colour per knob), and
  whether turning while pressed changes the events.
- **Rollers:** which physical direction `00` is. Upstream says "up". Also whether there are LEDs.
- **2xRoller on macOS:** it sent no input reports at all (raw hidapi, after replugging). The
  cause is unknown.
- **Base:** how many LEDs it has and where, and whether it reports which modules are attached
  (its config dump contained a Dial's serial number).
- **Device info byte 1** (`0x14`) and **byte 9** (`0x01`).
