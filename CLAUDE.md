# Project context (read first)

Goal: control the pixel screen of the Edifier(漫步者) 花再 Halo PixelBar speaker
from Windows with Python: custom text first, custom images later.
Owner has either the basic edition (2025) or the enhanced edition (2026, 419 RMB,
real-time lyrics, Bluetooth 6.0 + USB). Edition: ENHANCED (user states it has Bluetooth 6.0 and real-time lyrics). Image upload support unknown.

Known facts (from public web reports, not verified):
- Official PC software is "EDIFIER TempoHub"; it controls the speaker over USB
  when the speaker is in sound-card (USB) input mode.
- Official screen modes: text DIY, lyrics, themes, cross-screen messages.
- No public protocol, SDK or open-source project was found.
- Screen resolution, transport (HID/serial/other) and encryption are UNKNOWN.

Rules:
- Never send data to the device until the protocol is documented in
  docs/protocol.md and a --dry-run matches the captured bytes.
- Mark each protocol finding as confirmed (seen in captures) or guessed.
- Keep captures/ files out of public pushes unless the user confirms.
- The user does the TempoHub/Wireshark actions; you analyse and write code.
- If traffic is encrypted/signed, say so plainly instead of guessing.

Workflow and status:
1. Identify device: run tools/list_devices.py, record VID:PID in docs/protocol.md.
2. User captures USB traffic (steps in README.md) into captures/.
3. Analyse captures (tshark/Python), diff per action, fill docs/protocol.md.
4. Build pixelbar/ module + CLI: `python -m pixelbar text "你好" --color ff0000`,
   with --dry-run first. Images only after text works.
Update this status list as steps finish.
