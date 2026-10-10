# Context for Claude Code

This is a prototype bridge that shows Discord mute and deafen state on the Cooler Master MasterHub 15-key screen module (M4315, VID 0x2516, PID 0x01FE). It runs on Windows with Python, `hidapi` and Pillow. It was written without access to the hardware, so the first job is to verify it on the real device.

The protocol comes from https://github.com/projectzero7/cooltek-sdk-probe. Its docs are in `docs/MasterHUB/modules/m4315/`.

- **Feature reports** are 32 bytes, report ID 0x03.
  - `03 3a 01` turns software mode on. The module ignores everything else until this is sent.
  - `03 34 <0-100>` sets backlight brightness.
  - `03 37 <idx> r g b` fills a key with a solid colour. It's a fallback if images fail.
- **Key image** is sent as output reports of 320 bytes:
  - Header: `02 36 <chunk u16 LE> <len u16 LE> <key 0-14> <last flag>`, then up to 312 bytes of JPEG data.
  - The image is a 72x72 baseline JPEG with 4:4:4 subsampling. That's what the vendor sample uses.
- **Key input** arrives as input reports: `01 34 <len u16 LE> 02 0f k1..k15`, with each k being 0 or 1.

## Verification order

1. Run `python masterhub_discord.py --list`. Windows may show several HID paths, one per top-level collection. The script opens all of them and uses whichever accepts each report type.
2. Run `python masterhub_discord.py --test` and check that the numbers show, that they're upright, and that key presses print.
3. Run the bridge with `config.json`.

## Likely failure points

- **HID writes fail with a length error.** Windows requires the exact report length from the device descriptor. Check the descriptor's output and feature report sizes with `hid.enumerate` or USBTreeView, then adjust `OUT_REPORT_LEN` or `FEATURE_LEN`.
- **Images show garbled or not at all.** Try a different JPEG size: 72, 80, 96 or 100. Also try removing JFIF and EXIF extras. If it still fails, capture what MasterCanvas actually sends with USBPcap and Wireshark.
- **MasterCanvas fights over the keys.** Lower `reassert_seconds` or close MasterCanvas.
- **Discord `AUTHORIZE` or the token exchange fails.** Make sure the redirect `http://localhost` is registered in the Discord app. Voice RPC scopes only work for the app owner and whitelisted testers.
