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
   C (DONE 2026-10-25) self-built AB, ABC and 你好 (--frame NIHAO) sent via
     `replay_frame.py`; device acked each, screen showed them (user-confirmed).
     Colour: `--frame COLOR_330200` (03 33 02 00) sent; device echoed it, colour
     turned the TEXT colour red (user-confirmed). The leading 03 is still
     unexplained and is never varied.
   Captures 10-17 (display modes) analysed: `ef` 9-byte form `sel RGB 00 02
     major sub ff`; major 00 static / 01 scroll; sub meaning depends on major.
     NOT in the CLI yet and nothing sent. Before the long-text tests, set the
     mode in TempoHub (then quit it) and record it with each result.
   Live length tests DONE 2026-10-25 (static centre, then scroll left to
     right; 5/12/24/55 bytes): all acknowledged; static cut the 55 B text
     after "V", scroll showed it all; colour 336699 took effect. See "Live
     length tests" in docs/protocol.md. Still no timed-refresh code.
   Power-cycle test DONE 2026-10-25: text PWRTEST1 stayed after the speaker was
     unplugged >= 10 s (user report). Flash wear per write is still unknown:
     any Display Hook must send only on change and rate-limit (see Unknowns).
   Scene uploads analysed (captures 20-25, read-only): cmd 0x17 uploads raw
     1-bit 256x32 images (page layout, LSB on top) in 50-byte chunks with
     per-chunk acks; clock/spectrum scenes upload nothing; no checksum or
     encryption seen beyond the frame checksum. NOT in the CLI; nothing sent.
     See "Scene uploads" in docs/protocol.md and tools/analyze_upload.py.
   Upload builder stage 1 DONE (dry run only): pixelbar/upload.py rebuilds the
     whole OUT sequence of captures 20, 21, 23 byte-exact (tests/test_upload.py,
     hex-text fixture). It has no device path. Real uploads (stage 2: replay
     capture 20 unmodified) need the checks listed in docs/protocol.md and the
     user's explicit approval; the unknown header/ef fields are never varied.
   Upload stage 2 DONE 2026-10-25: tools/replay_upload.py replayed capture 20
     once; 25/25 frames, all 24 replies matched, screen showed 兼职成年人
     (user-confirmed). Only capture 20 can be replayed; no unknown field was
     changed. Stage 3 (changing pixels) is NOT started: see "Open questions
     before stage 3" in docs/protocol.md; needs the user's approval first.
   Upload stage 3 (changing pixels) STARTED 2026-10-25: upload 1 of 5 done with
     tools/upload_pixel.py (pixel x=128, y=4 on in capture 20's scene): all
     replies matched; the screen showed the text plus a dot in the upper
     middle (user-confirmed). The scene now shows the dotted version until
     TempoHub re-uploads it (restore not tried). Budget file
     ~/.pixelbar/stage3_uploads.txt = 1. Each further upload needs the user's OK.
     Restored through TempoHub (click another scene, then this one): the dot
     disappeared, text as before (user-confirmed).
     Upload 2 of 5 DONE 2026-10-25 (tools/send_png.py with the previewed test
     image, bound by --expect-sha): all replies matched; the screen showed the
     image as in the picture (frame, F top-left, TEST 123 centred, block
     bottom-right; user-confirmed). So the layout 256x32, vertical bytes, LSB on
     top is confirmed on the screen. Budget file = 2. The image was drawn in the
     same greyish blue (336699) as Hello; restored through TempoHub afterwards
     (screen back to 兼职成年人): both user-confirmed.
     Upload 3 of 5 DONE 2026-10-25 (same test image as upload 2, same tool and
     fields): all replies matched; after the speaker was powered off for about
     one minute (user's estimate) the test image was still shown (user-confirmed).
     So uploaded images survive a power-off; flash wear per upload is still
     unknown. Budget file = 3. See "Assessment: write risk of a host-driven
     slideshow" in docs/protocol.md (assumptions marked). Restored through
     TempoHub afterwards: the screen recovered (user-confirmed).
     Captures 30-33 (animation scenes) analysed read-only: 1 / 2 / 2 / 7 images,
     up to 7448 bytes; XX XX is not tied to the image count (no evidence what
     it is); T == ef p6 in all 8 uploads; the device sets the upload pace
     (~46 ms per 50-byte chunk). See "Animation scenes" in docs/protocol.md.
     Upload attempt 4 (silhouette B, text colour white) STOPPED at step 0 on
     2026-10-25: the speaker's reply to the ef scene-select frame carried its
     current colour (ff ff ff) instead of the 33 66 99 we sent, so the strict
     "exact echo" check stopped the run after 1 of 25 frames. Budget 4 of 5
     used. Not retried. Then, with the user's approval, the reply check was
     changed: the 3 colour bytes of the scene-select reply may be the colour in
     force (everything else and its checksum must still match; all other steps
     exact). The next upload needs the user's go-ahead; they will raise
     limits.json "total" themselves.
     Upload 5 DONE 2026-10-25 (first own picture: a silhouette, contain +
     threshold + invert, hash c6cdc4852165e993, text colour white): all 24
     replies matched (the ef reply reported the colour in force); the screen
     showed it right, in pure white, nothing left over (user-confirmed). So the
     ef colour bytes are not applied; the picture takes the text colour.
     limits.json is {"total": 20, "per_day": 10} (written at the user's request);
     4->5 uploads used. The silhouette replaced the scene 兼职成年人 until it is
     clicked again in TempoHub (restore not reported yet).
     Multi-picture uploads: `python -m pixelbar animation --frames N` (1-20,
     generated test animation, dry run by default; plan checks in
     upload_replay._multi_image_checks; exchange time limit max(30 s, 5 s +
     0.12 s per chunk)). The user chose the staged live test 2 -> 7 -> 20
     pictures. Step 1 (2 pictures, upload 6 of 20) DONE 2026-10-25: all 45 replies
     matched; user: 「两张图轮流显示，顺序 1 到 2，方块在移动」 (speed/loop not
     reported). Step 2 (7 pictures, upload 7 of 20) DONE: all 151 replies
     matched; user: 「1.7張圖2對3大概1秒1循環，會循環4沒有」 (7 pictures, order
     right, loops, nothing odd; "1秒1" unclear). Step 3 (20 pictures, upload 8 of 20) DONE: all 428 replies
     matched, 22.25 s; user: 「1.20張2會對3大概4秒循環一次4沒有」 (20 pictures,
     order right, about 4 s per loop, nothing odd). Staged test finished (each step had a
     dry run and the user's go-ahead). "Frame" = one 64-byte message, "picture" = one 256x32
     image (see docs/protocol.md).
     Own pictures (developed; real uploads: attempt 4 stopped, upload 5 ok): `python -m pixelbar image PIC`
     (dry run by default) and `prepare PIC OUT.png`, Pillow optional
     (pixelbar/prepare.py only). Limits that only ~/.pixelbar/limits.json can
     raise: 5 uploads in total (3 used), 10 per day (3 used today), no
     repeat of the last completed bitmap; `image --send` also needs
     `--expect-sha` = the hash of the previewed bitmap. The first real upload of an own
     picture needs the user's separate approval. See "Uploading your own
     pictures" in docs/protocol.md (incl. what is not verified).
     Prepared (nothing sent): tools/png_upload.py --dry-run converts a 256x32
     PNG (outside the repo) to the bitmap and shows the whole upload and its
     differences from capture 20; there is no send path for converted images.
   Characters: only send text from the tested allow-list (see "Design
     principles for the sending CLI" in docs/protocol.md).
   D (step 1 DONE: text "Hello" displayed, user-confirmed; steps 2-3 pending) free input: `python -m pixelbar
     text "..." [--color RRGGBB] | color RRGGBB`; default is a dry run, --send
     writes after asking "TempoHub closed?" (or --yes-i-closed-tempohub),
     stops at the first unexpected reply; text limited to --max-bytes (default
     6 = longest verified), more than one frame (55 B) is a hard error.
     No timed-refresh code on purpose: see "Unknowns" in docs/protocol.md
     (flash writes, meaning of 03, max length). Each live step needs the user's OK, in this order:
     1 text "Hello" (5 B, new content) 2 uncaptured colour e.g. 336699
     3 text up to 12 B, then 24 B, then 55 B (hard limit), stopping on any
     odd reply or screen. Compare each reply with the expected one printed.
Update this status list as steps finish.
