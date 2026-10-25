# Project context (read first)

Goal: control the pixel screen of the Edifier(漫步者) 花再 Halo PixelBar speaker
from Windows with Python: custom text first, custom images later.
Owner has either the basic edition (2025) or the enhanced edition (2026, 419 RMB,
real-time lyrics, Bluetooth 6.0 + USB). Edition: ENHANCED, per the user
(2026-10-25; they state it has Bluetooth 6.0 and real-time lyrics; not verified
against the box label or model number). Image upload support unknown.

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
   DONE: USB HID 2d99:a106 (Jieli), vendor usage pages 0xFF13/0xFF14 on iface 4.
2. User captures USB traffic (steps in README.md) into captures/.
   DONE for captures 01-06 (connect, text A/AB/ABC, colour, replug).
   Raw .pcap files stay outside the repo (they contain keyboard traffic).
   Captures 07-09 added: text is UTF-8 with a BYTE count, colour is R G B.
   No bitmap/pixel command seen yet; an image upload has not been captured.
3. Analyse captures (tshark/Python), diff per action, fill docs/protocol.md.
   Mostly DONE: frame = Report ID 2e/2f + aa/bb ec cmd len payload checksum;
   e8 = set text (ASCII confirmed), ef = colour (guessed). See docs/protocol.md.
4. Build pixelbar/ module + CLI: `python -m pixelbar text "你好" --color ff0000`,
   with --dry-run first. Images only after text works.
   Staged, each stage needs the user's OK before the next:
   A (DONE) pixelbar/frames.py + `--dry-run`, byte-exact vs captures
     (python -m unittest discover -s tests -t .). No device access exists in code.
   B (DONE 2026-10-25) tools/replay_frame.py sent only the captured "A" frame,
     unmodified; device replied 2f cc ec e8 00 01 01 a2 and the screen showed A
     (user-confirmed). Needs --send --yes-i-closed-tempohub, TempoHub closed.
   C (not started) after B succeeds: send self-built AB, ABC.
Update this status list as steps finish.
