# PixelBar protocol notes

Fill in from captures. Questions to answer:

- Transport: USB HID, USB serial, BLE GATT, or classic Bluetooth?
- Screen resolution, colour depth
- Text packet: encoding, font, colour, scroll fields
- Image packet: chunking, pixel format, checksum
- Differences between basic and enhanced editions

## Device identification (2026-10-25)

Source: `python tools/list_devices.py` (read-only enumeration, nothing sent to
the device). Speaker connected by USB, in sound-card input mode. Python 3.12.10,
hidapi 0.15.0, pyserial 3.5, Windows 11.

Raw output, speaker rows only:

    VID:PID=2d99:a106 mfr='Jieli Technology' product='<garbled> Halo PixelBar' usage_page=0xff13 usage=0x1 iface=4
    VID:PID=2d99:a106 mfr='Jieli Technology' product='<garbled> Halo PixelBar' usage_page=0xff14 usage=0x1 iface=4
    VID:PID=2d99:a106 mfr='Jieli Technology' product='<garbled> Halo PixelBar' usage_page=0xc    usage=0x1 iface=3

(The product string starts with the Chinese brand name; it is garbled only
because of the console code page.)

Findings:

| Finding | Status |
|---|---|
| USB VID:PID = `2d99:a106`, manufacturer string "Jieli Technology" (the SoC vendor) | confirmed (enumeration) |
| Transport on USB is HID, not serial. No serial port belongs to the speaker; the only port is COM1 (VID/PID None), unrelated | confirmed (enumeration) |
| Interface 3: Consumer Control (usage page 0x0C) - almost certainly volume/media keys | confirmed page, purpose guessed |
| Interface 4: two vendor-defined top-level collections, usage pages `0xFF13` and `0xFF14`, usage 0x01 | confirmed (enumeration) |
| The screen is probably driven through the vendor collections on interface 4 (one may be the command channel, the other a data/OTA channel) | guessed - needs USB capture |
| Report IDs, report sizes, packet format, encryption | unknown |

Not seen: the speaker also exposes a USB audio interface (sound-card mode),
which is not listed by HID enumeration.

Caveats:

- Only the HID/serial view was checked. Edition (basic 2025 vs enhanced 2026)
  is not recorded yet - ask the user.
- Other devices on this PC (Microsoft keyboard, Logitech mouse, ASUS AURA,
  "Receiver Update" 1a34:f517, DJP Virtual Multitouch 00ff:bacc) are not related.

## Capture 01: TempoHub opened and connected (2026-10-25)

Files (not in the repo, user's `C:\pixelbar-raw`): `usb-01-open-app.pcap` (full
USBPcap capture, 88 s) and `usb-01-open-app-dev9.pcap` (pre-filtered to device
address 9). Parsed with tshark 4.6.9 + Python; nothing was sent to the device.

IMPORTANT about the pre-filtered file: it contains only host->device ("submit")
URBs. The device's replies are the IN *completion* URBs (IRP direction
PDO->FDO), which the dev9 filter dropped. In the dev9 file every IN URB is
27 bytes with no data - that is NOT "the device sent nothing". Use the full
`.pcap` and read the data with `usbhid.data` (Wireshark dissects IN as HID, so
`usb.capdata` is empty for IN).

### Transport

| Finding | Status |
|---|---|
| Commands go over USB HID interrupt endpoint 0x04 (OUT) and answers come back on 0x84 (IN), device address 9. Each transfer is exactly 64 bytes, zero padded | confirmed |
| Request/response: every OUT frame is answered by one IN frame with the same command byte, ~2 ms later | confirmed (26 OUT, 28 IN) |
| Endpoint 0x84/0x04 is on the interface-4 vendor HID (usage pages 0xFF13/0xFF14) | guessed (HID class is shown by Wireshark, interface number was not checked) |
| Whether byte 0 (`2e`/`2f`) is a HID Report ID or part of the protocol | RESOLVED by capture 06: it is the HID Report ID (see "Captures 02-06") |
| Endpoint 0x03 isochronous = audio; other control/interrupt traffic is enumeration/descriptors | confirmed, ignored |
| 28 of the 54 dev9 interrupt URBs are IN; 26 OUT; no encryption seen: all fields are readable plain bytes and ASCII/UTF-8 strings appear in clear | confirmed |

### Frame format (64-byte report, rest zero padding)

    off  0     1   2   3     4-5        6..6+len-1   6+len
         magic aa  ec  cmd   len (BE)   payload      checksum

| Field | Value | Status |
|---|---|---|
| bytes 0-2, host->device | `2e aa ec` (26/26 OUT frames) | confirmed |
| bytes 0-2, device->host | `2f bb ec` (28/28 IN frames) | confirmed |
| byte 3 = command id; the reply repeats the request's command id | | confirmed |
| bytes 4-5 = payload length, big endian (all values seen < 256) | | confirmed |
| payload, then 1 checksum byte, then zeros to 64 bytes | | confirmed |
| checksum = (sum of all bytes from offset 1 through end of payload) mod 256, i.e. excluding byte 0. Holds for all 17 distinct OUT frames and all 18 distinct IN frames, 0 mismatches. (Equivalent: cmd + len bytes + payload bytes + 0x96 for OUT, + 0xA7 for IN) | confirmed |
| Not a CRC, not encrypted, no sequence number seen (identical requests are byte-identical) | confirmed for this capture |
| Command ids look scrambled rather than sequential (ee, 66, d8, c9, ...); their meaning is only inferred below | guessed |

### Commands seen (OUT payload -> IN payload)

| cmd | OUT payload | IN reply payload | Reading | Status |
|---|---|---|---|---|
| `d8` | none | `0000010501010100010001010a0e0a0031a000830030000000a008` (27 B) | capability/feature table | guessed |
| `65` | none | `02000040` | unknown (version/flags?) | unknown |
| `c9` | none | UTF-8 `e88ab1 e5868d 20 48616c6f20506978656c426172` | device name "花再 Halo PixelBar" | confirmed (decoded text) |
| `c8` | none | `c82478bbc66f` (6 B) | looks like a Bluetooth/MAC address | guessed |
| `c6` | none | `010306` | version number? | guessed |
| `66` | none | `100b` | unknown | unknown |
| `d5`, `68` | none | `00` | unknown flags | unknown |
| `6a` | none | 44 B starting `13040171ff563c0a02...` | structured config table (repeating `3c 0a`/`3c ff` groups) | unknown |
| `ee` | none | `002592ff00010000ff010000` (12 B), identical all 5 times | status poll, repeats about every 0.5 s (49.9, 50.4 s) | guessed |
| `87` | `0e0201` | `020201` | query/set with 3-byte arg | unknown |
| `e7` | `00` | `001032056c73707373` (9 B, ends with ASCII "lspss") | version/info query | unknown |
| `84` | none | `0501` | unknown | unknown |
| `77` | `07ea 0a 19 0f 19 0d 00 01` (9 B) | `01` | clock sync: 0x07ea=2026, 10, 25, 15, 25, 13 = 2026-10-25 15:25:13; last two bytes unknown (weekday/24h flag?) | year..second confirmed by matching the real capture time; last 2 bytes guessed |
| `86` | none | `0e0101` | unknown | unknown |
| `d7` | none | `0101` | unknown | unknown |
| `f0` | `1a` | `1aff01` | query with 1-byte arg `1a`, echoes it | guessed |

Also seen: two device-initiated IN frames with no matching request (cmd `66`
payload `100b` at 14.8 s, cmd `77` payload `02` at 43.1 s) - probably unsolicited
notifications (guessed).

Sequence: after the app opens there is a burst of query commands
(`66 d5 68 6a ee 87 c9 c8 c6 ee e7 e7 ee ee 84 77 86 d7`), then only the `ee`
poll about every 0.5 s and a one-off `f0`. Two such rounds are present (about
48 s and 64 s into the capture).

### What this capture does NOT contain

- No text, colour or image data. This is the connection handshake only (device
  info, capability queries, clock sync, status polling). Nothing here changes
  screen content. Stated as confirmed for this capture.
- Text encoding: the only text seen is the device name, which is UTF-8 (CJK
  `花再` as 3-byte sequences). Whether screen text also uses UTF-8 is a guess.
- Screen resolution, colour format, image chunking: unknown.

### Open questions after capture 01 (answered below in "Captures 02-06")

1. Text / colour actions: see below.
2. HID report descriptor: see below.
3. Edition: the user believes it is the enhanced edition (2026); not verified
   against the box label or model number. Findings above apply to that unit.

Tooling: `python tools/analyze_capture.py <full .pcap> [--dev N] [--timeline]`
reproduces the frame, checksum and command tables in this section;
`--descriptors` decodes HID report descriptors from a replug capture.

## Captures 02-06 (2026-10-25)

Files (kept outside the repo, they contain unrelated keyboard traffic):
`usb-02-text-A`, `-03-text-AB`, `-04-text-ABC`, `-05-color`, `-06-replug`.
Speaker was device address 9 in 01-05; after the replug in 06 it re-enumerated
as address 11 (VID 0x2D99 used to find it). Edition per the user: enhanced
(not verified against the label). Nothing was sent to the device.

### HID report descriptor (capture 06) - confirmed

Read from GET_DESCRIPTOR(HID report) replies during enumeration.

| Interface | Collection | Reports |
|---|---|---|
| 3 | Consumer Control (usage page 0x0C) | no Report ID, 1 input byte, 8 bits: usages 0xE9 0xEA 0xE2 0xCD 0xB5 0xB6 0xB3 0xB7 (volume up/down, mute, play/pause, next, previous, ...) |
| 4 | vendor, usage page **0xFF13** | Report ID **0x06** OUTPUT 61 bytes, Report ID **0x07** INPUT 61 bytes (not seen used in any capture) |
| 4 | vendor, usage page **0xFF14** | Report ID **0x2E** OUTPUT 63 bytes, Report ID **0x2F** INPUT 63 bytes (this is the channel all traffic uses) |

So byte 0 of the 64-byte transfers (`2e` host->device, `2f` device->host) is the
HID **Report ID**, not part of the protocol header (confirmed). The protocol
frame is the following 63 bytes:

    Report ID | tag  ec | cmd | len (2, BE) | payload | checksum | zero padding to 64
    2e / 2f     aa/bb

- tag byte: `aa` host->device, `bb` device->host, and `cc` device->host for
  the reply to `e8` (set text) only; second byte always `ec` (confirmed values,
  meaning guessed). CORRECTION: the first version of this document and of
  `analyze_capture.py` knew only `bb`, so the four `cc` replies were dropped
  and "no reply to e8" was wrongly reported. Fixed after stage B.
- checksum = sum of the tag byte through the last payload byte, mod 256; the
  Report ID is not included (confirmed, same data as before, restated).
- With hidapi the write buffer is the full 64 bytes including the Report ID in
  byte 0; the 0xFF14 collection on interface 4 is the one to open (guessed
  mapping of usage page to hidapi device path; to be checked at send time).
- Self-check: all 127 frames in captures 01-09 (both directions, addresses 9
  and 11) were rebuilt from (Report ID, tag, cmd, payload) and are byte-exact
  identical, including the zero padding (confirmed; the count was 123/110
  before the four `cc` replies were found).

### Text (captures 02, 03, 04) - confirmed for ASCII

New command `e8` = set screen text. It is the only new OUT command in these
three captures besides the already known `e7`, `77`, `6a`, `ee`.

| Capture | Typed | `e8` payload | Reading |
|---|---|---|---|
| 02 | `A` | `00 01 41` | `00`, count 1, `41` |
| 03 | `AB` | `00 02 41 42` | `00`, count 2, `41 42` |
| 04 | `ABC` | `00 03 41 42 43` | `00`, count 3, `41 42 43` |

- Payload = `00` + count byte + the text bytes. Confirmed for ASCII.
- Encoding: single bytes `41 42 43`, so NOT UTF-16 (confirmed). RESOLVED in
  capture 07: the encoding is UTF-8 and the count is a BYTE count (below).
- Maximum text length: unknown.
- The device answers every `e8` about 2 ms later with `2f cc ec e8 00 01 01 a2`
  (cmd `e8`, 1 byte payload `01`, checksum `a2`), identical for A, AB, ABC and
  你好 (confirmed, 4 of 4); `01` is probably "accepted" (guessed). The app also
  reads the text back with `e7` (below). The first `00` byte of the `e8`
  payload is unknown (index/slot? guessed).
- Read back, cmd `e7` with payload `00`: reply `00 10 32 <count> <text>` (e.g.
  `00 10 32 03 41 42 43`). The app sends it twice after each `e8`. The bytes
  `10 32` are unknown (maybe limits, guessed). In capture 01 the same reply was
  `... 05 6c 73 70 73 73` = the text that was on screen before, "lspss"
  (confirmed decode).
- The status poll `ee` (12-byte reply) changed when text was set: bytes 5 and 7
  went `01..00` -> `02..01` (`002592ff 0001 0000 ff010000` ->
  `002592ff 0002 0001 ff010000`). It did not change between A, AB and ABC, so
  it is not the length. Meaning unknown (display mode? guessed).
- No other command was needed to make the text appear in the captures (no
  separate "apply" frame). That the screen actually changed was not observed
  by the capture itself, only by the user (not verified).
- Time sync `77` was resent in capture 04 (`07ea 0a 19 0f 32 12 00 01` =
  2026-10-25 15:50:18) and is answered with `01`.

### Colour (capture 05) - first sample; confirmed by captures 08/09 below

Only frame pair in the file:

    OUT  cmd ef  payload 03 27 99 ff
    IN   cmd ef  payload 03 27 99 ff 00 00 ff ff ff

- Evidence for `27 99 ff` = RGB: the `ee` status reply before the change had
  bytes 1-3 = `25 92 ff`; in capture 06 (after the colour change, after replug)
  they are `27 99 ff` (confirmed: same three bytes the app sent). So `ee`
  bytes 1-3 mirror the current colour. (`25 92 ff` and `27 99 ff` are both
  light blue.)
- Guess: payload = `03` + R G B. The meaning of `03` is unknown (zone,
  effect or colour slot).
- The IN reply echoes the 4 bytes then `00 00 ff ff ff`; meaning unknown
  (possibly another colour = white).
- At this point it was only a guess because the picked colour was not
  recorded; captures 08 and 09 (known hex values) settled the RGB order.

### Replug (capture 06)

After the replug the app repeats the same startup queries as capture 01
(`d8 c9 c8 c6 65 66 d5 68 6a ee 87`), all with identical replies, so the device
info is stable (confirmed). `ee` reply: `002799ff00020001ff010000`.

## Captures 07-09 (2026-10-25)

`usb-07-text-nihao` (text `你好`; the user confirmed by photo that the screen
showed it correctly, so the device has its own Chinese font), `usb-08-color-red`
(TempoHub hex `#330200`), `usb-09-color-green` (TempoHub hex `#003407`). Speaker
was device address 11 (VID 0x2D99). All frames: checksum OK. Nothing was sent
to the device.

### Text encoding and count - confirmed

    OUT e8  payload 00 06 e4 bd a0 e5 a5 bd

| Text | Chars | UTF-8 bytes | count byte | evidence |
|---|---|---|---|---|
| `A`, `AB`, `ABC` | 1, 2, 3 | 1, 2, 3 | 1, 2, 3 | captures 02-04 |
| `你好` | 2 | 6 | **6** | capture 07 |

- `e4 bd a0` = 你 and `e5 a5 bd` = 好 in UTF-8 (not GBK `c4 e3 ba c3`, not
  UTF-16 `4f 60 59 7d`): encoding is **UTF-8** (confirmed).
- The count byte is the **byte length** of the UTF-8 text, not the number of
  characters (2 characters gave 6) (confirmed). The frame length field is
  therefore `2 + byte_count`.
- The `e7` read-back repeats it: `00 10 32 06 e4bda0e5a5bd`.
- The leading `00` of the `e8` payload was `00` in all 4 samples (A, AB, ABC,
  你好). No sample ever differed, so its meaning is **unknown** (slot / text
  index / flag - only guesses, none supported).
- Maximum length: still unknown (longest sample 6 bytes).

### Colour `ef` - confirmed RGB order, meaning of `03` unknown

| Capture | TempoHub hex | `ef` OUT payload | reading |
|---|---|---|---|
| 05 | not recorded | `03 27 99 ff` | |
| 08 | `#330200` | `03 33 02 00` | `33 02 00` = R G B exactly as the hex (confirmed) |
| 09 | `#003407` | `03 00 33 07` | R=00, G=**33**, B=07 |

- Order is R, G, B, the same as the hex string (confirmed with `#330200`,
  which cannot be read any other way).
- DISCREPANCY: the user's hex for the green was `#003407` but the bytes sent
  are `00 33 07` (green 0x33, not 0x34). One count lower; cause unknown. Could
  be TempoHub rounding when it converts from its colour picker, or a misread
  hex. The device receives `33`. Not resolved - note for the sender: use the
  exact bytes.
- The values are dark (max 0x33 of 0xFF), but they are sent as-is; the app
  does not seem to scale them. (Capture 05 used a bright blue, `ff` blue.)
- The IN reply is the same 4 bytes echoed, then `00 00 ff ff ff`, in all 4
  samples (05, 08, 09 and the first one) - constant, meaning unknown.
- The leading `03` was `03` in all 3 colour captures. No variation, so its
  meaning is **unknown** (guesses such as text colour / zone are not
  supported by any data). Only changing the screen element in TempoHub (e.g.
  background colour, if there is one) while capturing would show whether `03`
  changes.

### Status reply `ee` (12 bytes) - what changes

Reply layout `00 | C1 C2 C3 | 00 | S5 | 00 | S7 | ff 01 00 00` (offsets 1-3 and 5, 7 vary):

| Where seen | bytes 1-3 | byte 5 | byte 7 |
|---|---|---|---|
| 01, 02 before the first `e8` | `25 92 ff` | `01` | `00` |
| 02 after `e8 "A"`, 03 `AB`, 04 `ABC` | `25 92 ff` | `02` | `01` |
| 06 (replug), 07 `你好` | `27 99 ff` | `02` | `01` |

- Bytes 1-3 changed once, between capture 04 and 06, i.e. across capture 05
  where the colour was changed to `27 99 ff`. They then equal the `ef`
  payload colour bytes (confirmed for that one change). In 08/09 no `ee` was
  captured after the colour change, so the mirror is evidenced by one sample
  only (strong guess, not confirmed).
- Bytes 5 and 7 changed from `01/00` to `02/01` when the first `e8` was sent
  (capture 02) and stayed at `02/01` for A, AB, ABC and 你好. They do not
  depend on the text content or length (confirmed). Meaning unknown (guess:
  a mode flag, "custom text active").
- The device pushes an `ee` status reply by itself right after an `e8`,
  without an `ee` request (seen in 02 and 07) (confirmed).
- Time sync `77` is repeated by the app in captures 04 and 08; the device
  sends an unsolicited `77` with payload `02` just before it (08) (confirmed;
  meaning of `02` unknown).

### Is there any bitmap / pixel command? - not so far

Across all 9 captures (127 frames) there are 19 distinct command bytes:
`65 66 68 6a 77 84 86 87 c6 c8 c9 d5 d7 d8 e7 e8 ee ef f0`. The longest
payload is 44 bytes (`6a` reply) and no transfer spans several frames. No
command carries what looks like pixel data: `6a` is a fixed config-like table
(identical in every capture), `d8`/`c9`/`c8`/`c6`/`65`... are device-info
queries, `e8`/`e7` are text, `ef` is colour. No image upload was captured, so
an image command is simply not seen yet (confirmed for these captures). An
image would need many frames or the other vendor HID channel (usage page
0xFF13, Report IDs 0x06/0x07, unused so far) - that is a guess.

(Superseded by "Bitmap / bulk-data evidence check" further down, which covers
all 17 captures and 157 frames; its conclusion is the same: no evidence.)

### Can the text module be written?

Yes, dry-run only first (project rule: nothing is sent until a `--dry-run`
frame equals the captured bytes). The frame builder reproduces all 127
captured frames byte-exact, including padding. The text frame is fully
described for UTF-8 text with the leading `00`, the colour frame for RGB
with the leading `03`. Open before real sending: meaning of the `00` and `03`
bytes (use the captured values), the maximum text length, and whether anything
else is required for the text to be shown in a given colour (not seen:
`e8` and `ef` were captured separately). The hidapi path for 0xFF14 is solved
(see stage B below).

## Stage B: replay of the captured "A" frame (2026-10-25)

`python tools/replay_frame.py --send --yes-i-closed-tempohub` wrote exactly
one frame, the unmodified `e8` "A" frame from capture 02 (64 bytes, verified
identical to the capture and to the frame builder before sending), with
TempoHub fully closed. No other command was sent.

- Device path: found with hidapi by `usage_page == 0xFF14` (VID 2D99, PID
  A106): `...MI_04&Col02...`, interface 4, second collection. No hardcoded
  path (confirmed).
- `write()` accepted all 64 bytes (confirmed).
- One read (2 s limit) returned the device reply at once:
  `2f cc ec e8 00 01 01 a2` + zero padding. This is byte-for-byte the reply
  TempoHub received for the same frame in capture 02 (confirmed).
- Screen result: the user watched the speaker and reported that the screen
  changed to `A` (user observation, 2026-10-25). So a single unmodified
  host->device `e8` frame is enough to set the text; no handshake, clock sync
  or status poll was needed first (confirmed for this case: TempoHub was
  closed and nothing else was sent in this session before the write).

## Stage C: self-built "AB" frame (2026-10-25)

`python tools/replay_frame.py --frame AB --send --yes-i-closed-tempohub`
wrote one frame built by `pixelbar.frames.text_frame("AB")`:

    2e aa ec e8 00 04 00 02 41 42 07  (+ zero padding to 64 bytes)

- Before sending it was verified byte-identical to the frame TempoHub sent in
  capture 03 (confirmed). TempoHub was closed; nothing else was sent.
- `write()` accepted 64 bytes; one read returned `2f cc ec e8 00 01 01 a2`,
  the same reply as for "A" and as in the captures (confirmed).
- Screen: the user reported it changed from `A` to `AB` (user observation).
  So a frame built by our own code, not just a replayed one, is accepted and
  displayed (confirmed for this frame).
- Then `--frame ABC` (built by our code, verified identical to capture 04):
  frame `2e aa ec e8 00 05 00 03 41 42 43 4c`, same reply
  `2f cc ec e8 00 01 01 a2`; the user reported the screen changed from `AB`
  to `ABC` (user observation). Confirmed for this frame.
- Then `--frame NIHAO` (`你好`, the first non-ASCII frame, built by our code,
  verified identical to capture 07 and with count byte 6 == UTF-8 byte
  length 6): frame `2e aa ec e8 00 08 00 06 e4 bd a0 e5 a5 bd 14`, same reply
  `2f cc ec e8 00 01 01 a2`; the user reported the screen changed from `ABC`
  to `你好` (user observation). Confirmed: UTF-8 text with a byte count is
  accepted and rendered by the device's own font.
- Then `--frame COLOR_330200` (cmd `ef`, built by our code, verified
  identical to capture 08): frame `2e aa ec ef 00 04 03 33 02 00 c1`. Reply
  `2f bb ec ef 00 09 03 33 02 00 00 00 ff ff ff d4` (tag `bb`, payload
  `03 33 02 00 00 00 ff ff ff`), identical to the reply in capture 08
  (confirmed). The user reported that the TEXT colour turned red (user
  observation, clarified afterwards). So `ef` with `03` + R G B is accepted
  and sets the text colour, and `33 02 00` (R=0x33, G=0x02, B=0x00) renders
  as red, which confirms the R G B order on the device (confirmed for this
  value).
- Still unexplained: the leading `03` (never varied; no other value was
  sent). The reply tag for `ef` is `bb`, for `e8` it is `cc`.
- Not yet sent from our code: any colour other than the two captured ones
  (`33 02 00`, `00 33 07`) through `replay_frame.py` (its allow-list is fixed).

## Stage D: free input via `python -m pixelbar` (2026-10-25)

`python -m pixelbar text "Hello" --send --yes-i-closed-tempohub` (TempoHub
closed, user watching) sent one frame that was NOT in any capture:

    2e aa ec e8 00 07 00 05 48 65 6c 6c 6f 7e  (+ zero padding to 64 bytes)

- Reply `2f cc ec e8 00 01 01 a2`, identical to every earlier `e8` reply
  (confirmed).
- The user reported that the screen changed to `Hello` (user observation;
  whether the colour stayed red was not stated). So a frame built purely from
  the documented format, with content never captured, is accepted (confirmed
  for 5 ASCII bytes).
- Text length verified on the device so far: up to 6 UTF-8 bytes (`你好`), 5 for
  `Hello`. Longer text is unverified; the CLI limits text to `--max-bytes`
  (default 6) and the hard limit is 55 bytes (one frame).

## Captures 10-17: display modes (2026-10-25)

Captured by the user in TempoHub's text DIY screen (device address 11). Modes
are two levels: a main mode, scroll or static, and a sub-option inside it
(scroll: left-to-right, right-to-left; static: left, centre, right, justified).
Per the user, clicking is enough: no apply/save button. Baseline: static
centre, text `Hello`, text colour `#330200`. The labels below are the user's
description of what they clicked; the bytes are from the captures. Nothing was
sent to the device by us. All 30 frames rebuild byte-exact from
(Report ID, tag, cmd, payload) (confirmed).

### No new command, but `ef` has a 9-byte form (confirmed)

Only `ef`, `e7`, `ee`, `77`, `66` appear. The main/sub choice is sent with
`ef`, in a 9-byte payload (the colour change seen before used a 4-byte one):

    sel | R G B | 00 | 02 | major | sub | ff
    p0    p1-p3   p4   p5   p6      p7    p8

| Capture | User action | `ef` OUT payload | `ef` IN reply |
|---|---|---|---|
| 10 | static centre -> scroll (default sub) | `02 33 02 00 00 02 01 ff ff` | `02 33 02 00 00 02 01 00 ff` |
| 11 | scroll -> right-to-left | `01 33 02 00 00 02 01 01 ff` | same as sent |
| 12 | scroll -> left-to-right | `01 33 02 00 00 02 01 00 ff` | same as sent |
| 13 | scroll -> static (default sub) | `02 33 02 00 00 02 00 ff ff` | `02 33 02 00 00 02 00 01 ff` |
| 14 | static centre -> left | `01 33 02 00 00 02 00 00 ff` | same as sent |
| 15 | static left -> right | `01 33 02 00 00 02 00 02 ff` | same as sent |
| 16 | static right -> justified | `01 33 02 00 00 02 00 03 ff` | same as sent |
| 17 | static justified -> centre | `01 33 02 00 00 02 00 01 ff` | same as sent |

### Field meanings

Byte values (each correlated with the user's action in the captures):

| Field | Value | Meaning | Status |
|---|---|---|---|
| `major` (p6) | `00` | static | confirmed (13, 14-17) |
| `major` (p6) | `01` | scroll | confirmed (10, 11, 12) |
| `sub` (p7), scroll | `00` | left-to-right | confirmed (12) |
| `sub` (p7), scroll | `01` | right-to-left | confirmed (11) |
| `sub` (p7), static | `00` | left | confirmed (14) |
| `sub` (p7), static | `01` | centre | confirmed (17; also default after 13) |
| `sub` (p7), static | `02` | right | confirmed (15) |
| `sub` (p7), static | `03` | justified | confirmed (16) |

- Main mode and sub-option are TWO FIELDS of ONE command (`ef`), not two
  commands and not one byte (confirmed): `major` at p6, `sub` at p7. The
  meaning of `sub` depends on `major`.
- `sel` (p0): `02` when the main mode was clicked (10, 13), `01` for every
  sub-option click (11, 12, 14, 15, 16, 17), and `03` in the earlier 4-byte
  colour frames. The correlation is confirmed (2/2, 6/6, 4/4). That `sel` says
  "which field is being set" is a guess.
- This explains the earlier unknown `03` of the colour command: it is the
  `sel` value for "colour" (correlation confirmed, interpretation guess).
- Changing the main mode resets the sub-option to a default (confirmed from the
  replies): the app sends `sub = ff` and the device answers `00` (left-to-right)
  after switching to scroll (10), `01` (centre) after switching to static (13).
  Whether `ff` means "default" or the device always resets is not known.
- Clicking the main mode sends one `ef` then one `e7` read-back (2 OUT, 2 IN
  frames); clicking a sub-option sends one `ef` only (confirmed).
- Capture 13 (to static) and capture 17 (back to centre) are NOT the same frame:
  `02 33 02 00 00 02 00 ff ff` versus `01 33 02 00 00 02 00 01 ff`. They end in
  the same state (replies differ only in `sel`) (confirmed).
- p4 `00`, p5 `02` and p8 `ff` never changed in these captures. p5 `02` equals
  `ee` byte 5 below; guess: a "screen mode" id with `02` = custom text.
- The 9-byte frames carry the current colour (`33 02 00` here). Whether the
  device applies the RGB from a `sel = 01/02` frame (i.e. would a different
  RGB change the colour) is NOT known: it was never varied.

### Status reply `ee` and read-back `e7` (confirmed correlations)

- `ee` reply layout: `00 | R G B | 00 | 02 | major | sub | ff 01 00 00`, the
  same fields as the `ef` payload with `p0 = 00`. The `sub` byte (index 7)
  tracked the state before each capture: centre `01` before 14, left `00`
  before 15, right `02` before 16 (3/3). `major` (index 6) was `00` in all of
  them (all static). A scroll state was not read with `ee`.
- This answers the earlier question about `ee` bytes 5 and 7: byte 7 is the
  sub-option, byte 6 the main mode, byte 5 is `02` in all of these (earlier,
  before the first `e8` of capture 02, bytes 5 and 7 were `01` and `00`, after
  it `02` and `01`: i.e. after sending text, the status read static/centre
  with p5 `02`; what byte 5 `01` meant is unknown).
- `e7` read-back reply `00 XX 32 <count> <text>`: `XX` was `20` in scroll mode
  (10) and `10` in static mode (13, and every earlier capture) (confirmed, 2
  samples). The `32` stayed constant.
- Not in these captures: any `e8`. So nothing new is known about the leading
  `00` of the `e8` payload.
- Incidental: the app re-sent the clock (`77`) in 16, and the device pushed an
  unsolicited `66` with payload `10 08` (earlier `10 0b`); meaning unknown.

### Consequences (not tested)

- To set a mode from our code the matching `ef` frame must be sent; nothing
  suggests `e8` alone changes it, but that is not established either way (the
  `ee` status after the first text in capture 02 read static/centre).
- None of this was sent by us; `pixelbar` does not build these frames yet.

## Live length tests, 2026-10-25 (confirmed by this live test)

Sent from `python -m pixelbar` with TempoHub fully closed, the user watching
the speaker. One frame per step, nothing repeated. Only `e8` (text) and the
4-byte `ef` colour frame (`03 R G B`) were used; no 9-byte mode frame. The
display mode was set by the user in TempoHub beforehand: round 1 "static
centre", round 2 "scroll, left to right". Every `e8` got the same reply,
`2f cc ec e8 00 01 01 a2`; the colour frame got its colour echoed back (below).
Each answer is the user's own, copied as typed (traditional Chinese), in the
order of the questions asked; the English is a plain translation, not an
interpretation.

### Round 1: static centre

| Step | Text sent (UTF-8 bytes) | User's answer | Translation |
|---|---|---|---|
| 1 | `Hello` (5) after colour `03 33 66 99` | `1是 2藍色偏灰` | 1 yes (text normal); 2 bluish grey |
| 2 | `Hello World!` (12) | `1有 2沒有，完整顯示 3是 4沒有` | 1 yes; 2 no truncation, shown completely; 3 yes (colour still bluish grey); 4 nothing odd |
| 3 | `ABCDEFGHIJKLMNOPQRSTUVWX` (24) | `1是 2沒有 3縮小了 4是` | 1 yes (complete); 2 not truncated; 3 shrunk; 4 yes (colour unchanged) |
| 4 | `0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrs` (55, the whole frame, no padding) | `1沒有 2V後面沒有了 3是 4是 5沒有` | 1 no (not complete); 2 nothing after V; 3 yes (smaller than the last step); 4 yes (colour unchanged); 5 nothing odd |

- Colour: frame `2e aa ec ef 00 04 03 33 66 99 be`, reply
  `2f bb ec ef 00 09 03 33 66 99 00 00 ff ff ff d1`. The user saw a bluish
  grey, as expected for `#336699`, and it stayed so in every later step
  (confirmed: the 4-byte colour frame with an uncaptured colour takes effect).
- 55 bytes in static centre was cut off after `V`. By counting our own string,
  that is the first 32 characters (`0123456789` + `A`..`V`); this count is
  arithmetic, not an observation. Whether the cut is a byte limit, the
  display width, or the font floor was not tested.

### Round 2: scroll, left to right

| Step | Text sent (UTF-8 bytes) | User's answer | Translation |
|---|---|---|---|
| 2 | `Hello World!` (12) | `1是 2對 3比較慢 4沒有 5沒有` | 1 yes (scrolled out completely); 2 correct (left to right); 3 rather slow; 4 colour unchanged; 5 nothing odd |
| 3 | `ABCDEFGHIJKLMNOPQRSTUVWX` (24) | `1完整，沒有 2差不多 3縮小了 4沒有 5沒有` | 1 complete, no letter missing; 2 about the same (direction/speed as before); 3 shrunk; 4 colour unchanged; 5 nothing odd |
| 4 | the same 55 bytes | `1完整 2差不多 3差不多 4沒有 5沒有` | 1 complete; 2 about the same (font size vs 24 bytes); 3 about the same (direction/speed); 4 colour unchanged; 5 nothing odd |

### What these results establish

- A 55-byte text, the most one 64-byte report can carry (frame exactly 64
  bytes, no padding), is accepted and acknowledged by the device (confirmed:
  twice, round 1 and 2).
- In static centre: 12 bytes shown completely; 24 bytes shown completely with
  a smaller font; 55 bytes shown only up to `V`, with a still smaller font
  (user's words). In scroll left to right: 12, 24 and 55 bytes all scrolled
  out completely, left to right; the font shrank at 24 bytes and was about the
  same at 55 (user's words); speed "rather slow" for the 12-byte text,
  "about the same" afterwards.
- The text colour set with the 4-byte `ef` frame stayed through the following
  `e8` frames in both modes (user: no colour change in any later step).
- Our `e8` frames did not change the main mode or direction: in round 2 the
  speaker kept scrolling left to right after three `e8` frames (confirmed).
  For static centre the user was not asked about the alignment each step, so
  that mode is not claimed.

### Power-cycle test (confirmed by this live test)

Display mode at the time: "滚动，左往右" (scroll, left to right), told by the
user. TempoHub closed throughout. Only one frame was sent in this test.

1. Sent `e8` with the text `PWRTEST1`: `2e aa ec e8 00 0a 00 08` + `PWRTEST1`
   + checksum `fa`. Reply `2f cc ec e8 00 01 01 a2`.
2. Asked whether the screen showed `PWRTEST1`; the user answered `是` (yes).
3. The user then powered the speaker completely off for at least 10 seconds
   (their statement; the exact time was not measured), connected it again,
   did not open TempoHub, and reported: `开机了，斷電前一樣` ("powered on,
   same as before the power-off").
4. Reading of that answer: the screen after power-on was what it had been
   before power-off, i.e. `PWRTEST1` (the user did not repeat the text; the
   previous answer fixes what "before" was).

Result: the text stayed after the power cycle (confirmed). What it does NOT
show: how the text is stored (the speaker may keep it in non-volatile memory,
or may have a battery or capacitor that kept memory alive; the user said the
speaker was completely unpowered, but we do not know whether it has a battery),
whether every `e8` makes a flash write, or how many writes it survives.

### What they do NOT establish

- Why static mode cuts at `V`; whether it keeps 32 bytes or is limited by
  width or font; any other length between 24 and 55.
- Anything about text longer than one frame (not tried, no such transfer was
  captured).
- The scroll speed in numbers; whether each new `e8` restarts the scroll.
- Whether any of this writes flash (see Unknowns).

## HID report descriptor of interface 4, itemised (read-only analysis)

Source: the GET_DESCRIPTOR(HID report) reply for interface 4 in capture 06
(replug), 66 bytes, read offline; nothing was sent to the device.

Raw bytes (hex):

    0613ff0901a101150026ff00850609007508953d9102850709007508953d8102c0
    0614ff0901a101150026ff00852e09007508953f9102852f09007508953f8102c0

### Collection 1: usage page 0xFF13 (confirmed)

| Bytes | Item |
|---|---|
| `06 13 ff` | Usage Page 0xFF13 (vendor defined) |
| `09 01` | Usage 0x01 |
| `a1 01` | Collection (Application) |
| `15 00` `26 ff 00` | Logical Min 0, Logical Max 255 |
| `85 06` | Report ID 0x06 |
| `09 00` `75 08` `95 3d` `91 02` | Usage 0, Report Size 8 bits, Report Count 61, **Output** (Data, Variable, Absolute) |
| `85 07` | Report ID 0x07 |
| `09 00` `75 08` `95 3d` `81 02` | Usage 0, Report Size 8 bits, Report Count 61, **Input** (Data, Variable, Absolute) |
| `c0` | End Collection |

- Output report 0x06: 61 data bytes (62 bytes on the wire with the Report ID).
- Input report 0x07: 61 data bytes (62 bytes with the Report ID).
- **No Feature report** in this collection (no `b1` item) (confirmed).
- Meaning of the bytes inside these reports: unknown. The usage is 0 (undefined)
  and the data are plain bytes 0-255.

### Collection 2: usage page 0xFF14 (confirmed, same structure)

Report ID 0x2E Output, 63 data bytes (64 on the wire); Report ID 0x2F Input,
63 data bytes (64 on the wire); **no Feature report**. This is the channel
that carries every frame in all 17 captures.

### Endpoints of interface 4 (confirmed, from the configuration descriptor in
capture 06)

Interface 4: class 0x03 (HID), 2 endpoints, both interrupt, wMaxPacketSize 64,
bInterval 1: `0x84` IN and `0x04` OUT. Both collections (0xFF13 and 0xFF14) are
in the same interface, so reports of either collection would share these two
endpoints and be told apart only by the Report ID in the first byte. That the
two collections share the endpoints follows from the descriptor (one HID
interface, one IN and one OUT endpoint); it is not something a frame showed.

Other interfaces in the same descriptor: interface 0 (class 0x01, subclass
0x01) and interfaces 1 and 2 (class 0x01, subclass 0x02) are USB Audio
(control and two streaming interfaces; isochronous endpoints `0x03` OUT,
maximum packet 288, and `0x83` IN, maximum packet 96); interface 3 is the HID
consumer control (report descriptor 33 bytes, interrupt IN `0x82`, maximum
packet 16).

## Known command table (all 17 captures: 01-17)

Counts are frames TempoHub exchanged with the speaker in captures 01-17 (74
OUT, 83 IN, 157 in total; nothing we sent is counted). "Answered" = an IN frame
with the same command byte follows the OUT before the next OUT of that
command. "Unsolicited" = IN frames with no matching OUT. Lengths are the
payload length in bytes (not the 64-byte report). Direction is from the host:
OUT = host to speaker, IN = speaker to host.

| Cmd | OUT | IN | OUT len | IN len | Answered | Unsolicited IN | In captures | Known meaning | Status |
|---|---|---|---|---|---|---|---|---|---|
| `65` | 2 | 2 | 0 | 4 | 2/2 | 0 | 01, 06 | startup query, reply `02 00 00 40` | unknown |
| `66` | 2 | 4 | 0 | 2 | 2/2 | 2 | 01, 06, 16 | startup query; reply `10 0b` (and `10 08` in 16), also pushed by the speaker | unknown |
| `68` | 2 | 2 | 0 | 1 | 2/2 | 0 | 01, 06 | startup query, reply `00` | unknown |
| `6a` | 3 | 3 | 0 | 44 | 3/3 | 0 | 01, 02, 06 | startup query, same 44-byte table every time | unknown (looks like a config table) |
| `77` | 4 | 7 | 9 | 1 | 4/4 | 3 | 01, 04, 08, 16 | clock: bytes 0-6 are year (2 B, big endian), month, day, hour, minute, second; last 2 bytes `00 01`; answered with `01`; the speaker also sends `02` unprompted before the app's sync | date/time bytes confirmed; last 2 bytes and the `02` unknown |
| `84` | 1 | 1 | 0 | 2 | 1/1 | 0 | 01 | startup query, reply `05 01` | unknown |
| `86` | 1 | 1 | 0 | 3 | 1/1 | 0 | 01 | startup query, reply `0e 01 01` | unknown |
| `87` | 2 | 2 | 3 | 3 | 2/2 | 0 | 01, 06 | OUT `0e 02 01`, reply `02 02 01` | unknown |
| `c6` | 4 | 4 | 0 | 3 | 4/4 | 0 | 01, 06 | startup query, reply `01 03 06` | unknown (version? guess) |
| `c8` | 4 | 4 | 0 | 6 | 4/4 | 0 | 01, 06 | startup query, reply `c8 24 78 bb c6 6f`, looks like an address | unknown (address-like, guess) |
| `c9` | 4 | 4 | 0 | 20 | 4/4 | 0 | 01, 06 | device name, UTF-8: "花再 Halo PixelBar" | confirmed |
| `d5` | 2 | 2 | 0 | 1 | 2/2 | 0 | 01, 06 | startup query, reply `00` | unknown |
| `d7` | 1 | 1 | 0 | 2 | 1/1 | 0 | 01 | startup query, reply `01 01` | unknown |
| `d8` | 4 | 4 | 0 | 27 | 4/4 | 0 | 01, 06 | startup query, 27-byte table, same every time | unknown (capability table? guess) |
| `e7` | 12 | 12 | 1 | 5-10 | 12/12 | 0 | 01, 02, 03, 04, 07, 10, 13 | read the screen text back: OUT `00`; reply `00 XX 32 <count> <text>` with `XX` = `10` static / `20` scroll | confirmed (text, mode byte correlation) |
| `e8` | 4 | 4 | 3-8 | 1 | 4/4 | 0 | 02, 03, 04, 07 | set screen text: `00 <UTF-8 byte count> <UTF-8 text>`; reply (report tag `cc`) `01` | confirmed |
| `ee` | 10 | 14 | 0 | 12 | 10/10 | 4 | 01, 02, 03, 04, 06, 07, 14, 15, 16 | status: `00 R G B 00 02 major sub ff 01 00 00`; also pushed by the speaker after an `e8` | confirmed for colour, main mode and sub-option; byte 5 and the trailing `01 00 00` unknown |
| `ef` | 11 | 11 | 4 or 9 | 9 | 11/11 | 0 | 05, 08, 09, 10-17 | 4-byte `03 R G B` sets the text colour; 9-byte `sel R G B 00 02 major sub ff` sets main mode (`sel` 02) or sub-option (`sel` 01) | confirmed (see "Captures 10-17") |
| `f0` | 1 | 1 | 1 | 3 | 1/1 | 0 | 01 | OUT `1a`, reply `1a ff 01` | unknown |

The table covers captures 01-17. Command `17` (scene upload) first appears in
captures 20-23 and is described in "Scene uploads".

Report tags: OUT frames carry `aa`; IN frames carry `bb`, except the reply to
`e8`, which carries `cc` (four frames). All frames in the table checksum
correctly (sum of the tag byte through the last payload byte, mod 256).

## Bitmap / bulk-data evidence check (all 17 captures)

UPDATE (captures 20-25, see "Scene uploads"): this check covered captures
01-17 only. The later captures 20-23 DO contain a bitmap upload (command
`0x17`, 1-bit 256x32 images on the 0xFF14 channel). The conclusion below was
right for 01-17 and is superseded for the whole set.

Question: does any command, frame or channel in the existing captures look
able to carry a bitmap or other large data?

**In the existing captures there is no evidence of that.** What was checked,
and what each check found:

- Speaker frames on interface 4: 157 frames, every one is a 64-byte report with
  Report ID 0x2E or 0x2F, on endpoints `0x04` and `0x84`. The longest payload
  is 44 bytes (reply to `6a`, identical in each capture). No command is
  followed by a numbered or offset-bearing series of frames; no frame is a
  continuation of another. 19 distinct command bytes, listed above, all with
  short fixed or small payloads.
- Collection 0xFF13 (Report IDs 0x06 and 0x07, 61-byte reports): not a single
  frame with Report ID 0x06 or 0x07 in any capture. It is declared in the
  descriptor but unused in these captures. Nothing can be said about what it
  is for.
- Control pipe: no SET_REPORT or GET_REPORT (no HID report transfers), no
  Feature report requests. HID class requests seen: SET_IDLE (2) only. The
  rest are standard descriptor and configuration requests and USB Audio class
  requests.
- Isochronous endpoints `0x03` and `0x83` belong to the USB Audio interfaces
  (audio data), and were not examined further.
- No image upload was captured, so the captures contain no example of an
  image being sent. The absence of evidence is about these captures only; it
  does not mean the speaker cannot show images.

## Scene uploads: captures 20-25 (read-only analysis, 2026-10-25)

TempoHub's "个性场景" (personal scenes) screen. The user clicked one scene per
capture, waited for the progress bar to finish plus 5 seconds, and said the bar
runs 5-10 seconds. The placeholder for the bar duration in their message was
left unfilled, so the bar durations are not recorded; only the USB timing below
was measured. Speaker at USB address 13. Nothing was sent to the device by us;
the full analysis is reproducible with `tools/analyze_upload.py` (the PNG
images it can write are not in the repository).

### Per capture

| File | Scene (user's label) | Frames (OUT / IN) | Bytes on the wire | USB upload time | `17` upload | Data |
|---|---|---|---|---|---|---|
| 20 | work, "兼职成年人" (plain text) | 25 / 24 | 3136 | 1.42 s | 1064 B, 22 chunks | 1 image |
| 21 | study, "上岸" (cat + laptop) | 46 / 45 | 5824 | 2.63 s | 2128 B, 43 chunks | 2 images |
| 22 | same "上岸" again | 46 / 45 | 5824 | 2.60 s | 2128 B, 43 chunks | 2 images, identical to 21 |
| 23 | cat, left column 3rd (row of paws) | 25 / 24 | 3136 | 1.32 s | 1064 B, 22 chunks | 1 image |
| 24 | clock, left column 1st | 1 / 1 | 128 | none | no `17` frame | - |
| 25 | spectrum, 1st | 1 / 2 | 192 | none | no `17` frame | - |

Every frame is a 64-byte report on the 0xFF14 channel (Report ID 0x2E / 0x2F);
no frame with Report ID 0x06 or 0x07 (0xFF13) in any of the six (confirmed).
"Bytes on the wire" = 64 bytes x frames. Commands used: `ef` (scene select) and
`17` (upload) only. Upload time = start frame to the final acknowledgement.

### Command `17` upload protocol (confirmed unless noted)

| Step | Host to speaker (OUT) | Speaker to host (IN) |
|---|---|---|
| start | `17`, payload `00 00` + 4-byte big-endian total size (`00 00 04 28` = 1064, `00 00 08 50` = 2128) | `00 01 00 32`: ready; `00 32` = 50 = data bytes per chunk |
| data | `17`, payload `00 02` + 2-byte sequence number (0, 1, 2, ...) + up to 50 data bytes | `00 03` + the same sequence number, for every chunk except the last one |
| end | `17`, payload `00 04` | `00 05` |

- Checked in all four uploads: the declared total equals the reassembled
  length; sequence numbers are contiguous from 0; every chunk but the last is
  acknowledged with its own number; the end is acknowledged. All chunks are 50
  bytes except the last (14 bytes for 1064, 28 for 2128).
- Single-frame payload: at most 54 bytes (`00 02 SSSS` + 50 data bytes); the
  stop-and-wait is one chunk per acknowledgement, about 50 ms per chunk.
- The total size counts the 5-byte headers (see below): 1064 = 8 x 133,
  2128 = 16 x 133.
- Timing seen: the speaker answered the start frame after 189, 199, 207 and 105
  ms, and the end frame after 204, 201, 214 and 173 ms (20, 21, 22, 23). That
  the speaker is busy for about 0.1-0.2 s at start and end could mean
  preparation or storing; this was not tested (guess).
- The leading `00 00` of the start payload never changed (meaning unknown).
- Before each upload TempoHub sent one `ef` frame (below) and the speaker
  echoed it. The start frame follows about 20 ms later (20, 21, 22) or 0.7 s
  later (23).

### What is uploaded: raw 1-bit images

The reassembled data is split in records of 133 bytes: a 5-byte header and 128
bytes of image data.

| | 20 | 21 / 22 | 23 |
|---|---|---|---|
| records | 8 | 16 | 8 |
| header `T 00 NN XX XX` | `02 00 NN 00 64` | `03 00 NN 00 30` | `04 00 NN ff ff` |
| image data | 1024 B | 2048 B | 1024 B |

- `NN` counts the records 0, 1, 2, ... without gaps (confirmed, all four).
- `T` is the same in all records of an upload (`02`, `03`, `04`) and equals the
  seventh byte of the `ef` frame sent just before (below) (confirmed for 20,
  21, 22, 23). Its meaning is unknown.
- `XX XX` is the same in all records of an upload: `0064` (20), `0030` (21,
  22), `ffff` (23). Meaning unknown. It looks like a per-scene value, such as a
  frame delay (guess, no evidence); nothing suggests a checksum.
- The data are bitmaps: 1 bit per pixel, 256 columns x 32 rows, 4 pages of 256
  bytes, one byte = 8 vertical pixels, least significant bit at the top.
  A 256x32 1-bit image is exactly 1024 bytes; the 21/22 data are exactly two
  such images. Evidence (confirmed): searching all page widths, the width 256
  with the least significant bit on top gave the highest stroke continuity
  across page boundaries in all three uploads (0.63 for 20, 0.37 for 21, 0.52
  for 23, against 0.04-0.11 for shuffled bytes), and the images rendered with
  this layout are clearly legible: capture 20 shows the five characters
  "兼职成年人"; 21 shows "上岸" twice around a cat at a laptop, in two slightly
  different frames; 23 shows a row of five paws. Rendered PNGs are in
  `C:\pixelbar-raw\render\` (not in the repository; the script regenerates
  them). The speaker's physical screen size was not read from the device;
  256x32 is the size of the uploaded images.
- Other layouts were not needed: one byte per pixel (RGB332) would be 8192 bytes
  per 256x32 image and RGB565 16384, but each image is 1024 bytes.
- Whether the 2-image upload (21) is an animation that the speaker plays is a
  guess from the two slightly different frames; the capture does not show
  playback. The `XX XX` value `0030` might be its delay (guess).
- Not compressed (confirmed by the sizes): 20 (text) and 23 (paws) have
  different content (72 % and 47 % zero bytes, entropy 2.10 and 3.04 bits per
  byte) but the same size, 1064 bytes, and the 21/22 size is exactly twice that.
  Not encrypted: the images are legible as they are sent.
- Checksum: the only check seen is the frame-level checksum (sum of the tag
  byte through the last payload byte, mod 256; all frames in all six captures
  are valid). In the upload there is no field that varies per record and looks
  like a check value, the blob has no trailer, and the end frame carries none.
  A check inside the speaker's reply to the end frame is not visible (`00 05`).
  "No evidence of an extra checksum" is all that can be said.

### Comparisons asked for

- 21 and 22 (the same scene, clicked again after another scene): all 45 `17`
  frames of 64 bytes each are byte-identical, and so is the `ef` frame
  (confirmed). So TempoHub uploads the scene again every time it is clicked;
  the 22 capture holds no other scene's traffic.
- Data volume: plain text (20) 1064 B; picture with pattern (21) 2128 B because
  it has 2 images; paws (23) 1064 B; clock (24) and spectrum (25): nothing.
  The size depends on the number of 256x32 images, not on the content.

### Scene select `ef` frame (confirmed correlation, meaning guessed)

The 9-byte form seen in "Captures 10-17": `sel R G B 00 p5 p6 p7 ff`. In these
six captures:

| Capture | `ef` payload | `p5` | `p6` | `p7` |
|---|---|---|---|---|
| 20 | `01 33 66 99 00 01 02 21 ff` | `01` | `02` | `21` |
| 21, 22 | `01 33 66 99 00 01 03 09 ff` | `01` | `03` | `09` |
| 23 | `01 33 66 99 00 01 04 04 ff` | `01` | `04` | `04` |
| 24 | `01 33 66 99 00 01 00 00 ff` | `01` | `00` | `00` |
| 25 | `01 33 66 99 00 01 08 00 ff` | `01` | `08` | `00` |

- `p6` equals the record header `T` in the three scenes that were uploaded
  (confirmed). `p6` and `p7` differ per scene; the speaker echoes the frame.
- `p5` was `02` in all text-mode frames and is `01` here. Together with the
  earlier `ee` status (byte 5 was `01` before the first text and `02` after) it
  looks like a screen-mode number: `02` = text, `01` = scenes (guess, matches
  all frames seen).
- `p6` / `p7` as a category and item number are a guess; the mapping to the
  user's categories (work, study, cat, clock, spectrum) was not established.
- Clock and spectrum: no upload frame; only the `ef` frame (confirmed). In
  these captures there is no evidence that their pictures are sent by the
  host.

### Capture 25: spectrum while music played

- The capture lasts 27.7 s. The `ef` frame is at 4.11 s. USB audio packets with
  data to the speaker (endpoint 0x03) run from 7.58 s to 21.70 s, 1414 packets,
  about 14 s.
- During the whole audio period the host sent no frame on the HID channel, and
  there was no periodic traffic in either direction (confirmed: the capture
  holds 3 HID frames in total: the `ef` frame and its echo, and one `66` frame).
- That `66` frame (payload `10 08`) came from the speaker without a request, 9
  ms after the first audio packet (7.587 s). Its meaning is unknown; it may
  simply be a reaction to audio starting (guess).
- So the host does not stream spectrum data; the speaker gets the music over
  the USB audio interface only. That the speaker computes the spectrum from that
  audio itself is a guess (no other source is visible in the captures).

### Answers to the question "is this an image upload?"

- Yes for the scenes 20-23: the host sends 1-bit 256x32 bitmaps (1024 bytes
  per image) with `17`, in 50-byte chunks, each acknowledged (confirmed).
- No for the clock and spectrum scenes in these captures: only `ef`.
- Whether the scene stays on the speaker after the upload is not known (the
  user's guess that it does was not tested). Whether the upload writes flash is
  not known either; the data are about 1-2 KB per upload, and TempoHub repeats
  the whole upload every time a scene is clicked.

## Character display tests (live, via TempoHub) (2026-10-25)

Status of every line below: 已确认（本次实机测试，经 TempoHub 输入并目视）
(confirmed: typed into TempoHub and seen on the speaker's screen). Nothing was
sent by our code in these tests, and nothing was sent to the device while
writing this section. The results are the user's, with no inference added.

| Characters | Result |
|---|---|
| Arrows `←` `↑` `→` `↓` `↖` `↗` `↘` `↙` | all eight displayed |
| `°` | displayed normally |
| `℃` | displayed normally |
| `●` | displayed normally |
| `■` | displayed normally |
| `▲` | displayed normally |
| `♥` | rejected by the software (TempoHub) |
| `♪` | rejected by the software (TempoHub) |
| `★` | rejected by the software (TempoHub) |
| Box-drawing `─│┌┐└┘├┤┬┴┼═║╔╗╚╝` | displayed, but with gaps between characters: they do not join into continuous lines |
| `□` `○` `△` `▽` `▼` `◆` `◇` `◎` `☆` | not tested |

## Design principles for the sending CLI

- Only send text made of characters that were actually seen to display. The
  tested set so far: ASCII letters, digits, space and `!` (only some ASCII
  punctuation was tried), Chinese characters (`你好`, and the scene text seen in
  uploads), and the symbols in the table above that displayed (the arrows,
  `°`, `℃`, `●`, `■`, `▲`). Everything else must be rejected by our code
  BEFORE any frame is built or sent; do not rely on TempoHub or the speaker to
  block it. Keep the allow-list as an explicit table in code, with the test
  status of each entry, and extend it only after a live test. Box-drawing
  characters would display but not as joined lines, so they should not be used
  to draw.
- One frame per action; no loops; stop at the first unexpected reply; text
  length limits as in "Live length tests".

## Scene upload builder, stage 1 (dry run only) (2026-10-25)

`pixelbar/upload.py` builds the frames of an upload; it has no path that opens
or writes a device, imports neither hidapi nor `pixelbar.device`, and its
command line (`python -m pixelbar.upload`) refuses to run without `--dry-run`.
Nothing was sent to the device.

- Input: image data (1024 bytes per 256x32 image, vertical bytes, least
  significant bit on top; `pack_pixels` / `unpack_pixels` convert from and to
  rows of 0/1), the record header parameters `T` and `XX XX`, and the bytes of
  the `ef` frame (RGB and `p7`; `p6` is set equal to `T`, as in the captures).
- Output: the OUT frames in capture order: one `ef` frame, then the `17` start
  frame, the data frames (50 bytes each), the end frame. No `ef` frame follows
  the upload, because none was seen after it. `expected_replies` gives the
  replies TempoHub received (the `ef` echo, ready `00 01 00 32`, one `00 03
  seq` per chunk but the last, done `00 05`; all with tag `bb`).
- Verified (unit tests, test data are hex text in
  `tests/fixtures/captured_uploads.json`, no .pcap or image file is read): the
  builder rebuilt the complete OUT sequence of captures 20 (25 frames), 21 (46
  frames) and 23 (25 frames) byte for byte from the image data and the header
  parameters, and the expected replies equal the captured IN frames. That
  confirms the layout of the start, data and end frames, the record header
  layout, the size field and the chunking for these three; it does not
  confirm the meaning of the unknown fields below.

## Stage 2: replay of capture 20's upload (confirmed by this live test, 2026-10-25)

Run once, with TempoHub fully closed (process count 0; the tray also exited,
per the user) and the user watching the speaker. Nothing else was sent in
this stage.

Step a (existing CLI): text colour `336699`, then the text `Hello`
(`2e aa ec ef 00 04 03 33 66 99 be`, `2e aa ec e8 00 07 00 05 48 65 6c 6c 6f
7e`). Both were answered as expected (`2f bb ec ef 00 09 03 33 66 99 00 00 ff
ff ff d1` and `2f cc ec e8 00 01 01 a2`). The user's answers on the screen:
`1是2是` ("1 yes, 2 yes": it showed `Hello`, in the greyish blue).

Step b (`python tools/replay_upload.py --send --yes-i-closed-tempohub`): the 25
OUT frames of capture 20, byte for byte as TempoHub sent them (all unknown
fields at the captured values: record header `T` = `02`, `XX XX` = `0064`, `ef`
frame `01 33 66 99 00 01 02 21 ff`), the plan having been checked against the
capture fixture and the stage-1 builder before sending:

- ef scene select (1 frame), start (1), data seq 0-21 (22), end (1).
- Device replies: all 24 expected replies matched exactly: the `ef` echo, the
  ready reply `00 01 00 32` (chunk size 50, as expected), 21 chunk
  acknowledgements `00 03 <seq>` for seq 0-20, and the end acknowledgement
  `00 05`. The last data chunk (seq 21) got no reply, as in the capture, and
  nothing was waited for after it.
- No unsolicited `ee` or `66` frame arrived during the upload. No timeout, no
  mismatch, no retry, no frame sent twice. 25 of 25 frames written.
- Timing: 1.44 s from the first write to the end acknowledgement (capture 20:
  1.42 s). The speaker took about 0.2 s to answer the start frame and about
  0.2 s to answer the end frame, as in the captures.

What the user saw, as typed: `1兼职成年人2一樣3沒有`. Answers to my three
questions in order: (1) what the screen shows now: 兼职成年人; (2) whether font,
colour and position are the same as in capture 20 at the time: the same (一樣);
(3) anything else odd (flicker, leftover `Hello`, garbage, no reaction): none
(沒有).

Result: after replaying the upload sequence of capture 20, the screen displayed
兼职成年人 where it had displayed `Hello`. That is all this test shows. It does
not show whether the scene stays after a power cycle, whether it wrote flash,
what any unknown field does, or what happens with changed image data; and as
the `ef` frame's colour bytes equalled the colour already in force
(`33 66 99`), it does not show whether the speaker applies them.

Way back: clicking the scene in TempoHub uploads it again (capture 22 shows a
second upload of the same data); this was not exercised after this test.

### Open questions before stage 3 (changing pixels)

- Whether the speaker checks the image data: `XX XX` or something in the
  start/end exchange could depend on the content. Only unchanged data has been
  uploaded, so nothing is known. A first test would change one pixel only.
- Which scene a changed upload replaces: with `T`, `p6` and `p7` kept as in
  capture 20 it is the same scene (兼职成年人); that scene would then show our
  image until TempoHub uploads it again. Not tested.
- Whether TempoHub really restores it (click the scene, wait for the upload),
  and whether a stored scene survives a power cycle: neither was tested.
- Pixel geometry on the real screen: that the layout 256x32, vertical bytes,
  least significant bit on top, is the speaker's own is supported by the
  legible 兼职成年人; a test that lights a known pixel (for example a corner)
  would show whether all 256 columns and 32 rows appear, and whether any edge
  is cut.
- Whether the image is drawn in the text colour in force (the `ef` frame's RGB
  or the earlier `ef` colour setting) or in a fixed colour: not known.
- A limit on the number of uploads, because it may write flash (see Unknowns).

## Stage 3: one changed pixel (confirmed by this live test, 2026-10-25)

Tool: `python tools/upload_pixel.py --send --yes-i-closed-tempohub` (capture
20's scene, one pixel switched on, at most 5 uploads in total; this was upload
1 of 5). TempoHub fully closed (process count 0 before each send); before the
upload the screen showed `Hello` in the greyish blue, which the user confirmed
(`是，是，繼續`).

What was sent: capture 20's 25 frames with ONE pixel changed: pixel (x=128,
y=4) of the 256x32 image = image byte 128 (page 0, column 128), bit 4, value
`0x10`; the byte went from `00` to `10`. The dry run showed that exactly one
OUT frame differed from capture 20: step 4 (data seq 2), at two byte offsets,
48 (the image byte, `00` to `10`) and 60 (that frame's own checksum, `ee` to
`fe`, which follows from the first); the other 24 frames were byte-identical.
All unknown fields kept the captured values (`T` = `02`, `XX XX` = `0064`, `ef`
frame `01 33 66 99 00 01 02 21 ff`). The expected replies were those of capture
20.

Device side (confirmed): 25/25 frames written; all 24 expected replies matched
exactly (the `ef` echo, ready `00 01 00 32`, acknowledgements `00 03 <seq>` for
seq 0-20, end `00 05`); no unsolicited frame, no timeout, no retry; 1.47 s. The
frame with the changed byte (seq 2) was acknowledged like in capture 20
(`00 03 00 02`).

What the user saw, as typed: `1是2有，對，中間偏上，跟文字一樣3沒有4沒有`. In the
order of my four questions: (1) is 兼职成年人 shown: yes; (2) is there a dot
above the text, and is the position right: yes, correct, in the upper middle,
"same as the text" (the user's words, in answer to my question about its size
and brightness); (3) did the text itself change: no; (4) anything else odd:
no.

Result: after uploading capture 20's scene with one bit of the image data
changed, the screen showed 兼职成年人 with an additional lit dot in the upper
middle above the text. Confirmed by this test:

- The speaker accepted image data that differs from the captured data, with
  the record header `XX XX` and all other unknown fields unchanged, and
  displayed it. It did not refuse the upload and no field had to be
  recalculated for this change. (This does not prove that the speaker never
  validates the data; it shows that this change was not refused.)
- Image byte 128, bit 4 produced a dot in the middle (column 128 of 256) and
  near the top, as the layout "256 columns, vertical bytes, least significant
  bit on top" predicts for (128, 4). The user could only say "upper middle", so
  the exact row and column were not read from the screen, and "bit 4 means row
  4 from the top" (rather than row 3 with the most significant bit on top) is
  not separated from the alternative by this one observation.

Not shown: that all 256 columns and 32 rows are visible, that the edges are not
cut, where exactly the dot sits, whether the change survives a power cycle,
and whether the upload writes flash.

### Restoring the scene through TempoHub (confirmed by this live test)

Afterwards the user restored the scene in TempoHub: clicked another scene
first, then clicked 「兼职成年人」 again, waited for the progress bar to finish.
The user's words: `亮点消失了，文字和原来一样` ("the dot disappeared, the text is
the same as before"). Result: clicking the scene in TempoHub removed our extra
pixel and the text looked as before. The USB traffic of this click was not
captured, so what TempoHub sent is not known from this test; it is consistent
with capture 22, where a second click on a scene uploaded the same data
again.

## PNG to bitmap converter and dry run (stage 3 preparation, nothing sent)

`pixelbar/image.py` and `tools/png_upload.py --dry-run` (no device path; the
tool refuses to run without `--dry-run`):

- Input: a PNG of exactly 256x32 pixels, white pixels on a black background; a
  pixel is lit when its brightness is at or above `--threshold` (default 128;
  transparent pixels count as black). Any other size is an error; nothing is
  scaled or cropped. Interlaced or damaged PNG files are errors. No
  third-party package is used.
- Output: 1024 bytes in the confirmed layout: 4 pages of 256 bytes, one byte =
  the 8 pixels of one column, least significant bit on top (`--bit-order
  lsb-top`, the default). `msb-top` exists as an option for testing the
  alternative; the tool prints a warning when it is used.
- The tool prints a `#` / `.` picture of the 256x32 image (with a column ruler
  and row numbers), then the upload sequence it would produce: capture 20's 25
  frames in which only the image data is replaced; every unknown field keeps
  capture 20's value (`T` = `02`, `XX XX` = `0064`, `ef` frame `01 33 66 99 00
  01 02 21 ff`). It lists the frames that differ from capture 20 and checks
  that: the `ef`, start and end frames are byte-identical, the record headers
  are identical, and every differing frame is a data frame whose differing
  bytes are image bytes and, where it changes, its own checksum. It also shows
  the upload budget (1 of 5 used before the next upload, which would be the
  2nd).
- Demonstrated on a temporary test pattern made for this purpose (a one-pixel
  frame, a letter F at the top left, "TEST 123" in the middle; kept outside the
  repository): 21 of the 25 frames differ, all of them data frames; `ef`,
  start, end and one data frame (seq 11, which has no lit pixel in either
  image) are identical to capture 20; all checks passed. In one frame (seq 20)
  the 50 image bytes differ but the frame's checksum is unchanged, because the
  changes add up to a multiple of 256.
- Tests build their PNG data in memory; the repository contains no PNG file.

### Planned test image: what each observation would tell (a plan, not a result)

The pattern is the one written by `tools/make_test_image.py OUT.png` (added by
another session; standard library only; keep OUT.png outside the repository): a
1-pixel white frame on the outer ring (columns 0 and 255, rows 0 and 31), a
letter F at the top left (columns 6-15, rows 4-17, twice the size of a 5x7
font), `TEST 123` in the centre (columns 81-174, rows 9-22), and a 3x3 block
near the bottom right (columns 249-251, rows 26-28). None of this has been
uploaded yet. (The temporary pattern used for the dry-run demonstration above
was a similar one made for that purpose; it is not this file's output.)

| Observation | What it can decide |
|---|---|
| All four sides of the frame visible and unbroken | the whole 256x32 area is displayed; a missing side means at least that outer column or row is not visible (cut or hidden); a gap in a line points to a seam or a missing column or row in the panel mapping |
| The F at the top left, upright and readable | no mirroring, flipping or rotation: columns run left to right, rows top to bottom, and the origin (0, 0) is the top left. The F spans rows 3-16, across the 8-row page boundary, so a wrong bit order within a byte (msb-top) would show the F cut into bands with the halves swapped; this separates `lsb-top` from `msb-top`, which one pixel could not |
| F mirrored, upside down, or at another corner | mirrored: the column order is reversed; upside down or banded: bit order or page order is wrong; at another corner or rotated: the mapping of pages and columns is transposed |
| The 5 dark columns (1-5) left of the F and the 3 dark rows (1-3) above it, between the F and the frame | the first columns and rows are displayed at the right positions (no offset of the image against the panel) |
| The 3x3 block near the bottom right (columns 249-251, rows 26-28), 3 dark columns (252-254) and 2 dark rows (29-30) from the frame | the bottom-right area is displayed (the last columns and rows); with the F at the top left it also separates a left-right mirror from a top-bottom flip: a mirror moves the block to the bottom left, a flip to the top right |
| `TEST 123`: strokes crossing the page boundary at row 16 are continuous | the page order and the bit order are right in the middle of the image too; the digits `1`, `2`, `3` and the `S` cannot be read the same when mirrored |
| `TEST 123` equally far from the left and right frame (81 columns each side) and from the top and bottom (9 rows each) | column 128 and row 16 are the centre of what is shown (to the precision of the eye) |
| Strokes two pixels thick stay two pixels thick | no scaling or blurring; pixels are square and uniform |
| All lit pixels (frame, F, text) the same colour and brightness | the image is drawn in one colour; compared with the colour set for the text before (`33 66 99`) it shows whether the image takes the text colour or a fixed one |
| Everything else dark, no leftover of the previous display (`Hello`) | the upload replaces the whole picture; a leftover would mean the screen is not cleared |
| Still image, no flicker or movement | a single-image scene with `XX XX` = `0064` is static; any movement would be new information about what `XX XX` or the scene mode does |

The eye limits these readings; a photo of the screen would make the
comparison with the preview easier.

## Stage 3, upload 2: the orientation test image (confirmed by this live test, 2026-10-25)

Tool: `python tools/send_png.py C:\pixelbar-raw\test-256x32.png --expect-sha
037720be605a --send --yes-i-closed-tempohub`; the PNG is the output of
`tools/make_test_image.py` (kept outside the repository). TempoHub fully closed
(process count 0 before each send). Upload 2 of 5. Before it, `Hello` in
colour `336699` was shown with the existing CLI (replies as expected) and the
user confirmed: `屏幕是 Hello，偏灰蓝` ("the screen is Hello, greyish blue").

What was sent: capture 20's 25 frames with only the image data replaced by the
1024-byte bitmap of the test image (lit pixels: the 1-pixel outer frame, an F
at columns 6-15 / rows 4-17, `TEST 123` at columns 81-174 / rows 9-22, a 3x3
block at columns 249-251 / rows 26-28; bitmap SHA-256 starts `037720be605a1495`,
the value shown by the preview and bound with `--expect-sha`). 21 of the 25
frames differed from capture 20, all of them data frames, differing only in
image bytes and their own checksum; `ef`, start and end frames and all unknown
fields (`T` = `02`, `XX XX` = `0064`, `ef` frame `01 33 66 99 00 01 02 21 ff`)
were the captured ones.

Device side (confirmed): 25/25 frames written; all 24 expected replies matched
exactly; no unsolicited frame, no timeout, no retry; 1.53 s.

What the user saw, as typed (answers in the order of my eight questions):
`1都可以2是，正的3右下角4清楚，居中，跟圖片一模一樣5看的到6都一樣7是，沒有8是靜止，正常`.

| Question | The user's words | Plain translation |
|---|---|---|
| 1. Are all four sides of the white frame visible, any side hidden? | 都可以 | all fine |
| 2. Is the F at the top left, and upright, mirrored or flipped? | 是，正的 | yes, upright |
| 3. Where is the small block? | 右下角 | the bottom right corner |
| 4. Is `TEST 123` clear, centred, strokes unbroken? | 清楚，居中，跟圖片一模一樣 | clear, centred, exactly like the picture |
| 5. Are the dark gaps between the F and the frame, and the block and the frame, visible? | 看的到 | visible |
| 6. Are frame, F, text and block the same colour and brightness (and which colour)? | 都一樣 | all the same |
| 7. Is everything else dark, any `Hello` left over? | 是，沒有 | yes (dark), none left |
| 8. Is the picture still, any flicker or movement? | 是靜止，正常 | still, normal |

Result: after uploading the test image, the screen showed the image as in the
picture: the frame, the F at the top left, `TEST 123` in the centre and the
block at the bottom right. Confirmed by this test (to the precision of the
eye):

- The layout of the bitmap is right: 256 columns left to right, 32 rows top to
  bottom, vertical bytes, least significant bit on top. The F spans rows 4-17,
  across the page boundary at row 8, and is upright; with the most significant
  bit on top it would be cut into bands with the halves swapped. This also
  settles the point left open after upload 1 (one dot could not separate
  `lsb-top` from `msb-top`). No mirror, flip or rotation: the F is at the top
  left and the block at the bottom right.
- The whole 256x32 area is shown, including the outer ring (all four sides of
  the frame), the block near the bottom-right corner and the gaps between
  those and the frame, so no outer column or row is cut off and the image is
  not offset.
- The text is centred; the screen was fully replaced (no `Hello` left over) and
  the image was still.

Colour (confirmed by this live test, told by the user afterwards): the colour
shown by the test image was the same greyish blue as `Hello`. Both had the
colour `336699` set with the `ef` colour frame. So the image was drawn in that
colour and not in some other fixed colour. This test cannot say whether the
image takes the text colour setting or the RGB bytes inside the scene `ef`
frame, because both were `33 66 99`.

Restore (confirmed by this live test, told by the user afterwards): the user
restored the scene in TempoHub by clicking another scene first, then clicking
「兼职成年人」 again; after the progress bar finished the screen was back to
「兼职成年人」. The USB traffic of this click was not captured.

Not settled: whether the scene stays after a power cycle, and whether the
upload writes flash. The `ef` frame's colour bytes equalled the colour already
in force, so this does not show whether the speaker applies them. Upload budget
used: 2 of 5.

## Animation scenes: captures 30-33 (read-only analysis, 2026-10-25)

Four more scenes uploaded by TempoHub (the user clicked another scene first,
then the target, waited for the progress bar and 5 more seconds). Each file
holds exactly one `ef` frame and one upload (one start frame). The user's
message gave the scene names and a placeholder for how long each animation
loop lasts ("动画一圈约【你填】秒"), which was left unfilled in all four, so no
loop time is known: every question below that needs it is answered "no
evidence in the existing captures". Nothing was sent to the device. The
contact sheets (all frames stacked and numbered from 1 at the top) and the
single images are PNG files in `C:\pixelbar-raw\render\`, not in the
repository; `python tools/analyze_upload.py CAPTURE.pcap --sheet-dir DIR
[--png-dir DIR]` regenerates them.

### The numbers (all frames of every capture validated: size, sequence, acknowledgements)

| Capture | Scene (the user's label) | Upload bytes | Images | `NN` | `T` | `XX XX` | `ef` `p5` `p6` `p7` | Upload time |
|---|---|---|---|---|---|---|---|---|
| 30 | cat, left column 4th (five stretching cats) | 1064 | 1 | 0..7 | `04` | `ffff` | `01` `04` `06` | 1.33 s |
| 31 | study, right column 2nd (自愿学习, stripes) | 2128 | 2 | 0..15 | `03` | `0064` | `01` `03` `03` | 2.55 s |
| 32 | work, 「下一站 翻身」 | 2128 | 2 | 0..15 | `02` | `0064` | `01` `02` `1e` | 2.55 s |
| 33 | game, right column last (460ms) | 7448 | 7 | 0..55 | `01` | `0064` | `01` `01` `11` | 7.97 s |

Images = data bytes / 1024 (image bytes = 128 per record, 8 records per image;
the upload bytes also count the 5-byte record headers: 133 per record). The
longest upload in all captures so far is capture 33: 7448 bytes, 56 records, 149
chunks of 50 bytes, 7 images, all acknowledged.

### What the images are (rendered with the confirmed 256x32 layout)

Read from the contact sheets and the pixel counts; the descriptions are the
assistant's reading of the pictures, the counts are measured.

- 30: ONE image, a row of five cats in different stretching poses. A single
  image holds all five; no second frame is uploaded.
- 31: two images. A cup with steam, the characters 自愿学习 and a band of
  diagonal stripes; the stripes differ between the frames (977 pixels differ).
- 32: two images that are almost the same: a small figure, a dotted line ending
  in a dot, and the characters 下一站 翻身; only 18 pixels differ.
- 33: seven images: a box with the text 460 ms moves from the left to the right
  with a trail of earlier positions behind it. Pixels that differ between
  consecutive images: 584, 590, 984, 992, 440, 474; no two images are
  identical. The "460ms" is part of the picture, drawn in the image; no record
  header field and no `ef` field of the upload has the value 460.
- Earlier: 21 is two images (52 pixels differ), 23 and 20 are single images.

### Do the number of images and `XX XX` belong together?

Over all seven uploads seen so far:

| Images | `XX XX` seen |
|---|---|
| 1 | `0064` (20, plain text), `ffff` (23 and 30, cats) |
| 2 | `0030` (21, study), `0064` (31 study, 32 work) |
| 7 | `0064` (33) |

- `XX XX` does not follow the image count: one image has `0064` or `ffff`, two
  images have `0030` or `0064`. It is not a frame count (confirmed from these
  values).
- It does not follow the category either: 21 and 31 are both "study", both two
  images, and differ (`0030` against `0064`). It is a per-scene value
  (confirmed: 21 and 22 are the same scene and identical).
- Whether it is a frame interval, a number of loops or something else: no
  evidence in the existing captures. The loop times needed to compare (the
  user's placeholder) are missing. The label "460ms" of capture 33 is not
  `XX XX` (it is `0064`, which is 100, not 460), and it is drawn in the image.
- A test the user could do without any upload: if `XX XX` were the delay of one
  frame in a fixed unit, the loop of 21 (two images, `0030` = 48) would be
  about 2.1 times shorter than the loop of 31 or 32 (two images, `0064` =
  100), and the loop of 33 (seven images of `0064`) would be about 3.5 times
  the loop of 31 or 32. This is a prediction of that hypothesis, not a result.
- The single-image scenes 23 and 30 (both `ffff`) show that a scene that looks
  like an animation (a row of poses) can be one image; if the device moves
  anything in them, the motion does not come from several uploaded frames
  (whether it moves at all: no evidence, not observed).

### Fields in 21 (two images) against 23 (one image) and the new captures

| Field | 21 | 23 | 30 | 31 | 32 | 33 |
|---|---|---|---|---|---|---|
| images | 2 | 1 | 1 | 2 | 2 | 7 |
| `T` = `ef` `p6` | `03` | `04` | `04` | `03` | `02` | `01` |
| `XX XX` | `0030` | `ffff` | `ffff` | `0064` | `0064` | `0064` |
| `ef` `p7` | `09` | `04` | `06` | `03` | `1e` | `11` |
| `ef` `p5` | `01` | `01` | `01` | `01` | `01` | `01` |

- `T` equals the `ef` byte `p6` in all eight uploads seen (20, 21, 22, 23, 30,
  31, 32, 33) (confirmed). By the user's labels the values follow the category:
  work `02` (20 and 32), study `03` (21, 22, 31), cat `04` (23, 30), game `01`
  (33); the clock (`00`) and spectrum (`08`) captures upload nothing. This is a
  correlation over the user's category names, not a documented meaning.
- `p7` differs in every scene, also within a category (work: `21`, `1e`; study:
  `09`, `03`; cat: `04`, `06`). It does not follow the position the user gave
  in the list (cat left column 3rd = `04`, 4th = `06`). Its meaning: no
  evidence.
- `p5` was `01` in every scene capture (and `02` in text mode).

### Upload time against bytes: the device sets the pace

| Bytes | Chunks | Upload time (start frame to final acknowledgement) |
|---|---|---|
| 1064 | 22 | 1.32-1.42 s |
| 2128 | 43 | 2.55-2.63 s |
| 7448 | 149 | 7.97 s |

- The time grows with the number of chunks: about 0.3 s fixed plus 46-52 ms per
  chunk. The speaker's acknowledgement of a chunk took 42-48 ms at the median
  (15-65 ms for ordinary chunks), and TempoHub wrote the next chunk 2-9 ms
  after it (median) after receiving the acknowledgement. So the speaker's
  acknowledgement time sets the pace; about 1.1 kB/s is the fastest seen over
  a whole upload (7448 bytes in 7.97 s: 934 bytes/s). (The 2 ms replies noted
  earlier belong to the text command `e8`, not to the upload chunks.)
- Some acknowledgements were slower: 115-128 ms. In capture 33 they were
  chunks 21, 42, 63, 85 and 106, the chunks that contain the boundary between
  image 1 and 2, 2 and 3, 3 and 4, 4 and 5, 5 and 6 (multiples of 1064 bytes
  of the stream); the sixth boundary (chunk 127) took 56 ms, an ordinary time.
  In the two-image captures (21, 31, 32) the one boundary chunk (21) was slow
  as well. All the other chunks of 33 took at most 65 ms. A per-image extra
  step in the speaker is one reading that fits; that it is a flash write is
  only a guess.
- The start frame was answered after 104-207 ms and the end frame after
  133-214 ms (captures 20-23 and 30-33).
- Upper limit of the device: no evidence. 7 images (7448 bytes) were accepted
  without any change of behaviour; the speaker never refused or slowed down in
  a way that looks like a limit. How many frames it can keep: no evidence in
  the existing captures (at least 7 images were uploaded and acknowledged; what
  the speaker does with them and whether it plays all 7 is not in the captures).
- Whether the host can set the interval between frames: no evidence. `XX XX`
  is sent by the host and differs between scenes, but nothing shows that it is
  an interval.

## Power-cycle test of an uploaded image: upload 3 (confirmed by this live test, 2026-10-25)

Purpose: does an uploaded image survive a power-off of the speaker? Tool and
data as in upload 2: `python tools/send_png.py C:\pixelbar-raw\test-256x32.png
--expect-sha 037720be605a --send --yes-i-closed-tempohub`, the same test image
(bitmap SHA-256 `037720be605a1495`). Before sending, the plan was rebuilt with the
code of the commit used for upload 2 (`9e01c66`) and with the current code: the
25 OUT frames and the expected replies were identical in both (same hashes), and
no file that decides the plan had changed since. Upload 3 of 5. Before it,
`Hello` in colour `336699` was shown with the existing CLI and the user
confirmed the screen was `Hello`, greyish blue (`是，請繼續`).

Device side (confirmed): TempoHub process count 0 before the send; 25/25 frames
written; all 24 expected replies matched exactly; no unsolicited frame, no
timeout, no retry; 1.53 s, as in upload 2. Upload budget used: 3 of 5.

Then, as planned: the screen was checked, the speaker was powered off
completely and connected again, and TempoHub was not to be opened. The user's
words, as typed: `圖片正常，開機了，測試圖，斷電大概1分鐘`. Reading in the order of
the plan: (1) after the upload the picture was normal (圖片正常: the test image
was displayed as intended); (2) after reconnecting and starting ("開機了") the
screen showed the test image (測試圖); (3) the power was off for about one
minute (斷電大概1分鐘, the user's estimate, not measured). TempoHub was not
running when checked afterwards (process count 0); the user did not report
opening it.

Result: the test image was still displayed after the speaker had been powered
off for about one minute. Stated as the plan required: the image stayed after
the power-off. This does not show how it is stored (the text `PWRTEST1` also
stayed after a power-off of at least 10 s, see "Power-cycle test" above), whether
each upload writes flash, how many uploads the storage survives, or whether the
speaker has a battery or capacitor that kept something alive (the user said
the speaker was powered off completely; we do not know whether it has one). The
screen was not photographed or compared pixel by pixel; "測試圖" is the user's
word for what was shown.

### Restore after upload 3 (confirmed by this live test)

Afterwards the user restored the scene in TempoHub (clicked another scene,
then 「兼职成年人」 again). The user's words, as typed: `我已還原：先点别的场景再点回「兼职成年人」，屏幕 恢复了`
("I restored it: clicked another scene, then back to 「兼职成年人」, the screen
recovered"). The user did not say in other words what the screen showed after
the restore; the click was on 「兼职成年人」, as in the two earlier restores. The
USB traffic of this click was not captured.

## Assessment: write risk of a host-driven slideshow (assumptions marked)

Question: could a program make a slow slideshow by uploading one image after
the other from the PC? What is known (confirmed): an upload of one image takes
about 1.5 s and 25 frames; the speaker keeps what was uploaded (this section
above) and the text (`PWRTEST1`) across a power-off; the speaker is busy for
about 0.1-0.2 s at the start and at the end of an upload and slower at image
boundaries. Not known: whether an upload writes flash, what the endurance is,
whether the same place is rewritten each time.

The numbers below are an illustration under ASSUMPTIONS, not measurements:

- Assumption A: every upload erases and rewrites the same flash sectors (no
  wear levelling, the worst case). Assumption B: the endurance is 10,000 cycles
  (a low figure) or 100,000 cycles (a common figure for NOR flash); this chip's
  real value is not known.

| Upload interval | Uploads per day | Days to 10,000 | Days to 100,000 |
|---|---|---|---|
| every 10 s | 8,640 | about 1.2 | about 12 |
| every 1 min | 1,440 | about 7 | about 69 |
| every 10 min | 144 | about 69 | about 694 (1.9 years) |
| every 1 h | 24 | about 417 | about 4,167 (11 years) |

- If the assumptions hold, a slideshow that replaces one image every few
  seconds could use up the storage in days; one image every ten minutes or
  slower is a different order of magnitude. If the speaker spreads writes
  (wear levelling) or writes RAM first and flash later, the real figures are
  better; none of this was measured.
- A safer design under the same assumptions: put several frames in ONE upload
  (the speaker accepted 7 images, 7448 bytes, in one upload from TempoHub) and
  let the speaker play them, instead of one upload per picture. That the
  speaker plays frames of an upload by itself is the user's description of the
  animation scenes; playback was never part of a capture, and how the interval
  is set is not known (see "Animation scenes").
- Rules that follow whatever the real figures are: upload only when the picture
  changes; never in a loop without a limit; keep a counter and a daily cap in
  code (the 5-upload budget file is the test-phase version of this); treat each
  upload as a possible flash write until shown otherwise.

## Uploading your own pictures: `image` and `prepare` (development only, nothing sent)

Status: written and tested with a fake device and with test pictures made in
memory. No converted picture has been uploaded to the speaker yet; the first
real upload of an own picture needs the user's separate approval.

```
python -m pixelbar image PICTURE [--fit cover|contain|stretch] [--focus-x 0..1] [--focus-y 0..1]
                         [--mode dither|threshold] [--threshold N] [--invert] [--autocontrast]
                         [--preview-dir DIR] [--expect-sha HEX]
                         [--send --expect-sha HEX --yes-i-closed-tempohub]
python -m pixelbar prepare PICTURE OUT.png [the same picture options] [--overwrite]
```

- Pillow is an OPTIONAL dependency: `pip install pillow` (tested with Pillow
  13.0.0). It is imported only in `pixelbar/prepare.py`, and only when a
  picture is opened. Without it `image` and `prepare` stop with the message
  "Pillow is not installed ... pip install pillow"; the frame builder, the text
  and colour commands and the tools work as before (tested).
- Conversion: EXIF orientation is applied; transparent pixels count as black;
  pictures larger than 8000 pixels on a side are refused. `--fit cover`
  (default) fills the screen and crops what does not fit, `--focus-x/-y` (0 to
  1, default 0.5) choose which part is kept; `contain` keeps the whole picture
  with black bars (a small picture is enlarged); `stretch` distorts. Because
  the screen is 8:1, `cover` usually keeps only a horizontal band across the
  middle of the picture: a 4:3 photo keeps one sixth of its height. Black and
  white: `--mode dither` (Floyd-Steinberg, default) or `threshold`; a pixel is
  lit when its brightness is at or above `--threshold` (default 128; in dither
  mode it is the quantiser's threshold); `--invert` swaps light and dark
  (transparent areas stay black); `--autocontrast` stretches the contrast first.
- `prepare` only converts and writes a 256x32 black-and-white PNG for you to
  look at (it will not overwrite a file without `--overwrite`). Keep such PNG
  files outside the repository.
- `image` prints a `#` / `.` picture of the bitmap that would be uploaded
  (decoded back from the 1024 bytes, not from an earlier stage), writes a 4x
  preview PNG of that same bitmap to `--preview-dir` (default
  `C:\pixelbar-raw\preview`, outside the repository), and shows how the
  upload differs from capture 20: only data frames and their own checksums
  (the `ef`, start and end frames are identical), with the unknown fields
  (`T`, `XX XX`, `p5`, `p6`, `p7`) at capture 20's values. Without `--send`
  nothing touches the device; a test makes every device function raise and
  blocks importing hidapi while the dry run runs.
- Uploading (`image ... --send --expect-sha HEX --yes-i-closed-tempohub`, or
  the interactive "TempoHub closed?" question): `--expect-sha` is REQUIRED with
  `--send`: at least 12 hex digits of the bitmap SHA-256 that the dry run
  prints, so that exactly the bitmap that was looked at is sent (a missing or
  wrong hash refuses before anything else; in a dry run the hash is only
  checked and the dry run says which hash to use). The bounded exchange of stage 2, once. Every
  picture goes into the scene of capture 20 and replaces it until TempoHub
  uploads it again (clicking another scene and then 「兼职成年人」 restored it
  three times).

### Limits the command line cannot raise

State is in `~/.pixelbar/` (outside the repository): `stage3_uploads.txt` (the
number of uploads started; 3 were used before this work, the file already
said 3), `upload_log.jsonl` (one start line and one end line per upload, with
the bitmap's SHA-256), and the optional `limits.json`
(`{"total": 5, "per_day": 10}`, these are the defaults).

- Total budget: 5 uploads; the 3 already made count. This is the limit set for
  the test phase; it is raised only by editing `limits.json`.
- Daily cap: 10 uploads per calendar day (counted from the log); it was 3 at
  first and was raised on the user's remark that changing the picture ten times
  a day is normal use. The three uploads of 2026-10-25 were written into the
  log afterwards from the session notes (marked in their `note` field; no
  times), so they count towards today.
- No repeat: a bitmap whose SHA-256 equals that of the last COMPLETED upload is
  refused (the picture would not change). A failed attempt counts against the
  budget and the day but is not "the picture is already there". If the screen
  was changed in TempoHub since and the same bitmap really is to be sent again,
  the last `ok` line of the log has to be removed by hand.
- A limit can only be raised by editing `limits.json`; there is no command line
  option for it (a test checks that none of `--total`, `--per-day`, `--limit`,
  `--budget`, `--force`, `--allow-repeat` exists). A damaged `limits.json` or
  log stops everything instead of being ignored.
- The attempt is counted after the device was opened (a missing device costs
  nothing) and before the first frame is written.

### Not verified yet

- One own picture has been uploaded (the silhouette, see "Upload 5"; the
  first attempt to upload it stopped at the first frame, see "Upload attempt
  4"). How a dithered picture or a photograph looks on the real screen,
  whether the default settings give a good result, and the pixel pitch of the
  panel are still unknown.
- (Written before upload 5; since then the real path has been run once, see
  "Upload 5".) The upload path of `image --send` was exercised only with a fake device;
  `device.open_device` has not been run against the real speaker (stages 2 and
  3 used the tools' own code to open it).
- Only single-image uploads are built (one 256x32 picture); multi-image scenes
  and the meaning of `XX XX` are untouched.
- Pillow 13.0.0 only; phone photos with real EXIF data, CMYK, 16-bit and
  animated pictures were not tried (an in-memory PNG with an EXIF orientation
  tag was).
- The default daily cap of 10 (set from the user's remark about normal use,
  not from any measurement of the flash) and the preview folder are choices
  made here, not measured limits; the default folder is a Windows path. Under
  the pessimistic assumptions of the slideshow assessment above (every upload
  rewrites the same sectors, endurance 10,000 / 100,000 cycles), 10 uploads a
  day would take about 1,000 days (2.7 years) / 27 years to reach that
  endurance; this is an illustration, not a measurement.

## Upload attempt 4: stopped at the first frame (confirmed by this live test, 2026-10-25)

What was planned: the silhouette picture (settings `--fit contain --mode
threshold --invert`, bitmap SHA-256 `c6cdc4852165e993`, previewed and bound with
`--expect-sha c6cdc4852165`), sent with `image ... --send` as capture 20's
scene. Before it, the text colour had been set to pure white and `Hello` shown
(`ef 03 ff ff ff`, reply `2f bb ec ef 00 09 03 ff ff ff 00 00 ff ff ff 9c`).

What happened (confirmed, from the tool's output and the upload log): the
limits and the hash check passed (3 of 5 uploads used, 3 of 10 today), the
attempt was counted (budget 4 of 5) and the first frame was written, the `ef`
scene-select frame exactly as in capture 20:

    sent      2e aa ec ef 00 09 | 01 33 66 99 00 01 02 21 ff | e4
    expected  2f bb ec ef 00 09 | 01 33 66 99 00 01 02 21 ff | f5
    received  2f bb ec ef 00 09 | 01 ff ff ff 00 01 02 21 ff | c0     (raw reply 2fbbecef000901ffffff00010221ffc0)

The reply differs from the expected one only in the three colour bytes
(`33 66 99` expected, `ff ff ff` received) and so in its checksum. The rule
"stop at the first reply that is not the expected one" stopped the run: 1 of 25
frames written, no reply matched, no retry, no further frame, nothing else
was sent. The log has the attempt as `started` and `failed` ("reply differs
from the expected one").

What this shows (one reply; the reading is an inference): the speaker answered
the scene-select frame with its CURRENT text colour (white, set a moment
earlier) and not with the colour bytes we sent. In all earlier uploads (2 and 3
and capture 20) the colour in force equalled `33 66 99`, so the echo looked
like a plain echo. So the colour bytes of the scene-select frame are not
applied by the speaker; they are replaced in the reply by the colour in force.
Everything else in the reply (`01`, `00 01 02 21 ff`) equals what was sent.

What it does not show: what the speaker did with the scene-select frame (the
screen after it was not reported; the user's message ended with an empty
"the screen shows:"), and whether a later upload would work: the upload itself
(start, data, end) was not started.

Change to the reply check (made with the user's approval, "the three colour
bytes of the reply may be the current colour, everything else must be the
same"): in `pixelbar/upload_replay.py` the reply to the scene-select frame
(step 0 only) is accepted if it equals the expected reply except for the three
colour bytes (reply bytes 7-9), and its own checksum is right for the bytes
received. Any other difference in that reply (for instance `sel`, `p5`, `p6`,
`p7`, the tag, the command, a stray byte in the padding or a wrong checksum)
still stops the run at once, and every other step still needs an exact match
(tested, including with the raw reply of this attempt as test data). When the
colour differs, the tool logs "reply matched except the colour bytes, which
are the speaker's current colour ..." and the result carries it as
`colour_reported`. The same check is used by every tool that runs the exchange
(`replay_upload.py`, `upload_pixel.py`, `send_png.py` and `image --send`). A
dry run now prints this reply rule. The upload budget of this attempt stays
counted (4 of 5); raising the total is only done by editing `limits.json`.

## Upload 5: the first own picture, a silhouette (confirmed by this live test, 2026-10-25)

Tool and settings: `python -m pixelbar image C:\pixelbar-raw\silhouette-cropped.png
--fit contain --mode threshold --invert --send --expect-sha c6cdc4852165
--yes-i-closed-tempohub`. The picture is a phone screenshot of a black
silhouette (a figure with a bow) on white, 3120x1440, whose 600-pixel black
side bars were cropped off into a copy of 1920x1440 (the copy, not the
original, was used). `contain` scaled it to the 32-pixel height of the screen
(a figure about 15 pixels wide in the middle), `threshold` gave hard edges and
`invert` made the figure light on a dark background: 429 lit pixels, bitmap
SHA-256 `c6cdc4852165e993`, the hash that the dry run printed and that was
given with `--expect-sha`. It was uploaded as capture 20's scene (all unknown
fields as captured). Limits at the time: 4 of 20 uploads used in total (the
user had asked for `limits.json` with `{"total": 20, "per_day": 10}`), 4 of 10
today; this was upload 5 (the log has it as `started` and `ok`).

Before it, the text colour had been set to pure white and `Hello` shown
(`ef 03 ff ff ff`, replies as expected). The user said then that the screen
showed a normal white 「兼职成年人」 (the state after the `ef` frame of the
stopped attempt 4, see above) and, for the `Hello` step, replied `是，繼續`.

Device side (confirmed): TempoHub process count 0; 25 of 25 frames written; all
24 expected replies matched; no unsolicited frame, no timeout, no retry; 1.45
s. The scene-select reply matched under the changed rule: its colour bytes
were `ffffff` (the colour in force) instead of the `33 66 99` that had been
sent.

What the user saw, as typed: `1都正常2是3是，沒有4沒有`. In the order of my four
questions: (1) is the silhouette shown, is its shape right: all normal (都正常);
(2) is the colour pure white: yes; (3) is everything else dark, any leftover of
「兼职成年人」 or `Hello`: yes (dark), none; (4) anything else odd (flicker,
wrong position, cut off): none.

Result: the picture was shown as intended, in pure white. Confirmed by this
test:

- A picture prepared by `prepare.py` (scaled with `contain`, hard threshold,
  inverted) and uploaded through `image --send` is accepted and displayed.
- The image is drawn in the text colour in force (white, set with the colour
  command), not in the colour bytes carried by the scene-select frame (`33 66
  99` was sent, the picture is white). Together with the scene-select reply
  (it reports the colour in force) this settles the question left open for the
  `ef` colour bytes: they are not applied.
- The screen was replaced completely (no leftover) and was still.

Not shown: how a dithered picture or a photograph looks on the real screen
(this was a clean two-tone silhouette); whether the figure's size is as good
as it could be on this 256x32 screen (no other settings were compared on the
speaker); the restore after this upload (not done at the time of writing; the
speaker shows the silhouette in place of 「兼职成年人」 until the scene is
clicked in TempoHub again).

## Several pictures in one upload: the test animation (development; nothing sent)

Words used from here on: a FRAME is one 64-byte message between the PC and the
speaker (an upload of one picture is 25 frames: 1 `ef`, 1 start, 22 data, 1
end); a PICTURE is one 256x32 image (1024 bytes). Earlier sections sometimes
call the pictures of an animation scene "frames" ("2 frames", "7 frames");
those are pictures.

What exists: `python -m pixelbar animation --frames N` (N = 1 to 20; dry run by
default; `--send --expect-sha HEX --yes-i-closed-tempohub` uploads once, with
the same limits, hash binding and bounded exchange as `image`). The pictures
are generated (`pixelbar/testanim.py`, no picture file, no Pillow): a 14x14 block
moves from the far left (picture 1) to the far right (picture 20) in equal
steps, and the picture number is drawn at the top left (6x10 pixel digits). The
first N pictures are the same for every N. The dry run prints the table of
pictures, writes a numbered 3x contact sheet PNG of exactly what would be
uploaded (to `--preview-dir`, outside the repository), and shows the plan.

The plan for N pictures (the upload format of "Scene uploads", with the size
and the number of records and chunks following from N): `ef` scene select (as
capture 20), start with the total size N x 1064, N x 8 records of 133 bytes
numbered from 0 (T and `XX XX` as capture 20), chunks of 50 bytes, end (as
capture 20). N = 2: 46 frames (43 chunks, 2128 bytes, as TempoHub's 2-picture
scenes); N = 7: 152 frames (149 chunks, 7448 bytes, exactly the size of
TempoHub's largest upload, capture 33); N = 20: 429 frames (426 chunks, 21,280
bytes). The plan checks, written from the format and not from the builder's
code, say: the `ef` and end frames are capture 20's; the start frame differs
from capture 20's only in the size (and its checksum), and the size equals the
data sent; the record headers carry capture 20's `T` and `XX XX` and an index
running 0.. without gaps; the data frames carry exactly the pictures, chunk
numbers running 0.. without gaps; the expected replies are the `ef` echo, ready
(chunk size 50), one acknowledgement per chunk but the last, done. A test breaks
the start frame's size on purpose and checks that this is caught.

Time: the speaker takes about 46-52 ms per chunk (see "Animation scenes"), so
20 pictures need about 21 s. The fixed 30 s limit of the exchange would be too
tight for a speaker only 1.5 times slower (32 s), so the limit is now `max(30
s, 5 s + 0.12 s per chunk)`: unchanged 30 s for up to about 200 chunks (every
upload so far, including 7 pictures), 57 s for 20 pictures. Every reply still
has to arrive within 2 s and the first unexpected reply still stops the run
(tested, including a speaker so slow that even 57 s is not enough).

### The staged live test the user chose: 2, then 7, then 20 pictures

Each step is a separate upload (each counts against the budget and the daily
cap) and needs the user's go-ahead after a dry run; any reply that is not the
expected one, or anything odd on the screen, stops the sequence. Before each
the screen shows something else (`Hello` in the colour in force). What the
user is asked to report after each: how many pictures the speaker shows, in
which order, whether the block moves, how fast, whether it loops or stops, and
anything odd.

#### Step 1: 2 pictures (upload 6 of 20, 2026-10-25)

Sent with `python -m pixelbar animation --frames 2 --send --expect-sha
30fb62b8a355 --yes-i-closed-tempohub`; the screen showed white `Hello`
beforehand (user-confirmed). Result: 46/46 frames written, 45 replies, all
matched (the `ef` reply carried the colour in force, `ffffff`, as in upload 5),
no unsolicited frame, 2.61 s in total. Reported by the user, verbatim:
「两张图轮流显示，顺序 1 到 2，方块在移动」 — 已确认（本次实机测试）.

Not answered by that sentence (not asked again, so nothing is claimed): how fast
the pictures change, whether it loops without end, and whether anything else was
odd. Not verified.

#### Step 2: 7 pictures (upload 7 of 20, 2026-10-25)

Sent with `python -m pixelbar animation --frames 7 --send --expect-sha
e29b09844990 --yes-i-closed-tempohub` (TempoHub process count 0 beforehand).
Result: 152/152 frames written, 151 replies, all matched, no unsolicited
frame, 8.20 s in total (about 8 s expected). Reported by the user, verbatim,
answering the four questions (how many pictures / order / speed and loop /
anything odd): 「1.7張圖2對3大概1秒1循環，會循環4沒有」 — 已确认（本次实机测试）.
Read as: 1 seven pictures, 2 the order is right, 3 about "1秒1" and it loops,
4 nothing odd. The phrase 「大概1秒1」 was not asked to be made precise: it is
not recorded whether it means about one second per picture or one second for
the whole loop (7 pictures at 1 s each would be 7 s per loop). Not verified.

#### Step 3: 20 pictures (upload 8 of 20, 2026-10-25)

Sent with `python -m pixelbar animation --frames 20 --send --expect-sha
36822aadd826 --yes-i-closed-tempohub` (TempoHub process count 0 beforehand).
Result: 429/429 frames written, 428 replies, all matched, no unsolicited
frame, 22.25 s in total (about 21 s expected, limit 57 s). Reported by the
user, verbatim: 「1.20張2會對3大概4秒循環一次4沒有」 — 已确认（本次实机测试）.
Read as: 1 twenty pictures, 2 the order is right (the user answered "會對"),
3 about 4 seconds per loop, 4 nothing odd. This also settles the ambiguous
"大概1秒1循環" of step 2 only if it meant "about 1 second per loop"; that
reading was not confirmed.

Derived by arithmetic, not observed per picture (the user gave loop times
estimated by eye): 20 pictures in about 4 s is about 0.2 s per picture; 7
pictures in about 1 s (if that is what was meant) would be about 0.14 s per
picture. These two do not agree exactly and nothing here says the speaker uses
a fixed time per picture; how the speaker chooses the speed (and whether `XX
XX` plays a part) is not known. Not asked whether the block reached the right
edge; not verified.

Not known and to be learned (or to go wrong):

- The speaker's capacity for pictures per scene. The most seen is 7. How
  many fit in the place of the scene with `T` = `02` / `p7` = `21` (which
  holds one picture normally) is not known; TempoHub's scenes of the same kind
  hold 1 or 2. Whether the speaker refuses an upload that is too large (and
  how), or does something worse, is not known: that is the reason for the steps.
- How a multi-picture scene is played (order, speed, loop) when uploaded by our
  code, and what `XX XX` does to it (it stays at capture 20's `0064`). The loop
  times of TempoHub's scenes were never given.
- The storage written by an upload of 20 pictures is about 20 times that of one
  picture; whether that matters for wear is not known (see the write-risk
  assessment).
- Like every upload it replaces the scene 「兼职成年人」 until it is clicked
  again in TempoHub.

### Own pictures as a slideshow: 20 frames of a video (upload 9 of 20, 2026-10-25)

At the user's request: a video file (outside the repo; 1440x1080, 60 fps, 219 s)
was decoded with PyAV (`pip install av` into the embedded Python, with the
user's permission; used only for this extraction, not imported by the
`pixelbar` package). One frame per second for the first 20 seconds (t = 0, 1,
..., 19 s) was scaled to 32 pixels high (about 43 wide), centred on a black
256x32 canvas ("whole picture, centred", the user's choice), then thresholded at
128 (bright = lit, not inverted). The pictures and the contact sheet stay in
`C:\pixelbar-raw`. New command `python -m pixelbar slideshow PIC... [--mode
threshold --fit cover]` (1 to 20 pictures in the given order; dry run by
default; same checks, limits and `--expect-sha` rule as `animation`).

Sent with `--send --expect-sha e7fad7772f18 --yes-i-closed-tempohub` (TempoHub
process count 0 beforehand): 429/429 frames written, 428 replies, all matched,
23.05 s. One unsolicited frame was recorded at step 184: `2fbbec660002100827`
(a device-to-host frame with command `66`, ignored by the rule for unsolicited `ee`/`66`); this
is the first time one was seen during an upload. What it means is not known.

Reported by the user, verbatim: 「1.20張2可以3大概4秒，會循環4沒有」 — 已确认（本次实机测试）.
Read as: 20 pictures, it "works" ("可以", not asked what exactly was
recognised), about 4 seconds per loop (the same as the generated 20-picture
animation), it loops, nothing odd. Not verified: whether the first two pictures
were black as planned, and whether the silhouettes were recognisable; the
number 4 in 3 and 4 is the user's own numbering of the questions.

### Playback speed measured from phone videos (2026-10-25)

Method: the user filmed the speaker's screen (phone, 120 fps slow motion shown
at 0.25x; the video files hold about 29 frames per second, so file time / 4 =
real time; the user's statement, not checked otherwise) and `tools/screen_timing.py
--crop ... --bright 160 --slow 4` found the moments the lit pixels change (the
camera's own frame is about 8 ms in real time). Measured, in real seconds:

| Upload | Pictures | Time per picture | One loop |
|---|---|---|---|
| Video frames (upload 13, same 20 pictures as upload 9) | 20 | 0.198 s (median of 31 gaps, 0.194-0.211) | about 4.0 s (20 x 0.2; the loop itself was not seen twice in that clip) |
| Generated test animation (upload 14) | 7 | about 0.2 s | 1.40 s (successive loops 5.60-5.64 s in file time, 7 loops) |
| Generated test animation (upload 15) | 2 | about 0.20 s (change every 0.81 s in file time) | about 0.4 s |

So the speaker showed every picture for the same time, about 0.2 s (5 pictures
per second), for 2, 7 and 20 pictures: the loop time grows with the number of
pictures, it is not a fixed total. The impression that "more pictures go
faster" (user, by eye) is not supported by these three measurements. Each change
also showed up as a second, smaller event a few tens of milliseconds later
(0.017-0.034 s real) in all three videos; that is probably the camera or the
screen refresh, not a second picture change (not verified). The first seconds
of the video of upload 14 were not slow motion (the user said they looked the
same); the loop measurement used the clean part.

#### TempoHub's own two-picture scenes (the user clicked them in TempoHub and filmed the screen, 120 fps at 0.25x)

The user named the files "上岸" (`20261025_225728.mp4`) and nothing else; I
told them apart by what is on the screen (the screen content in the video:
上岸 with a cat at a laptop, and 自愿学习 with a cup and stripes). The two
scenes are the ones of captures 21/22 (`XX XX` = `0030`) and 31 (`XX XX` = `0064`),
assuming TempoHub's list still holds the same scenes (the pictures match the
descriptions of the captures; not verified byte for byte).

| Scene | `XX XX` of its capture | Measured time per picture (real) | Method |
|---|---|---|---|
| 自愿学习 (`20261025_225741.mp4`) | `0064` = 100 | about 0.20 s (a change every 0.71-0.81 s in file time, 30 changes) | changes of the lit pixels, as above |
| 上岸 (`20261025_225728.mp4`) | `0030` = 48 | about 0.09 s (dips of the brightness every 0.34-0.37 s in file time, mean 0.357 s over the clean stretch) | the two pictures differ in few pixels; the changes were found as short dips of the total brightness in the cat region, not as a change of the picture |

So the scene with the smaller `XX XX` changed pictures about 2.2 times faster
(0.0900 s against 0.2025 s). The simple guess "time per picture = `XX XX` x
2 ms" gives 0.200 s and 0.096 s: the first fits (0.2025 s), the second is about
6% faster than predicted. The camera's frame is 8 ms real, but the figure is a
mean over many changes, so 6% is probably real; the two scenes were measured
with different methods, so a small systematic difference between them is
possible. Evidence for "`XX XX` sets the time per picture": consistent, not
proven; the exact unit is not settled; only two values (and the generated
animations, all `0064`) were measured, in scenes of different content, and
other fields (`T`, `p6`, `p7`, the scene itself) differ between the scenes too.
`ffff` (one-picture cat scenes) was not measured.

#### The experiment: only `XX XX` changed (upload 17, 2026-10-25/26, user-approved)

At the user's request (「可以，只把 XX XX 改成 0030，用 2 張測試動畫上傳」) the
same 2-picture test animation as upload 15 was uploaded again with exactly one
change: the record header `XX XX` from `0064` to `0030` (`animation --frames 2
--xx 30`; `T`, the `ef` frame and every other byte as before; the plan checks
passed, the upload hash covers bitmap and `XX XX`). Upload 16 of the same plan
failed with `OSError: read error` while reading a reply, because the user had
accidentally switched the computer off (user's explanation); the attempt
counted. Upload 17 then went through: 46/46 frames, 45 replies, all matched,
2.70 s. The user filmed the screen (`20261026_073208.mp4`, 120 fps at 0.25x,
21.9 s file time, two pictures showing the block and the digit 2).

Measured (same method as above, real seconds = file time / 4): the pictures
change every 0.36 s of file time (59 changes from 0.77 s to 21.69 s, mean
0.3607 s), that is **0.0902 s per picture** (about 11 pictures per second),
against 0.2025 s per picture for the same animation with `0064`. The
TempoHub scene 上岸 (`0030`) gave 0.0900 s (see above). So, with a single field
changed and nothing else, `XX XX` = `0064` gave 0.2025 s and `0030` gave 0.0902
s per picture: **`XX XX` sets the time per picture (confirmed by this
measurement, two values)**. The relation between the two values: 100 -> 202.5
ms, 48 -> 90.2 ms; a straight line through them would be about 2.16 ms per unit
minus 13.6 ms, simple proportionality (2 ms per unit) gives 200 and 96 ms and
is about 6% off at 48; with only two points the exact formula (and whether
other values work, how fast or slow they can go, what `ffff` does) is not
known. Only `0030` and `0064` have been used, the two values TempoHub itself
used.

#### Third value: `XX XX` = `0018` (upload 18, 2026-10-26, user-approved)

Same 2-picture animation, only `XX XX` = `0x0018` (24; allowed range widened
to `0010`-`0190` at the user's request: 「我同意你用我們沒有用過…」, the message was
cut off after those words, read as "values not used before"). 46/46 frames, 45
replies, all matched, 2.73 s. The user's video `20261026_073718.mp4` (120 fps at
0.25x, 14.5 s file time): at this speed the two pictures have almost the same
total brightness, so the picture changes were found as the short dips of the
total brightness at every change (as for 上岸): 87 dips from 0.51 s to 14.28 s,
mean 0.1601 s of file time = **0.0400 s per picture** (the intervals alternate
0.13 / 0.17 s of file time, which is the camera's frame, 35 ms of file time).

| `XX XX` | time per picture | ms per unit |
|---|---|---|
| `0018` = 24 | 0.0400 s | 1.67 |
| `0030` = 48 | 0.0902 s | 1.88 |
| `0064` = 100 | 0.2025 s | 2.03 |

Not proportional to `XX XX` at the small end. A straight line through these
three points (about 2.13 ms per unit minus 11 ms: 40, 91, 202 ms) predicted
0.63 s for `012c` (300), before it was sent; see the fourth value below.

#### Fourth value: `XX XX` = `012c` (upload 19, 2026-10-26, user-approved)

Same 2-picture animation, only `XX XX` = `0x012c` (300). 46/46 frames, 45
replies, all matched, 2.72 s. The user's video `20261026_073958.mp4` (120 fps
at 0.25x, 13.4 s file time): the picture changes show as event pairs at 1.37,
3.77, 6.20, 8.59, 10.99 and 13.38 s (file time), a change every 2.40 s (the
three brightness dips at 6.23, 8.62 and 11.02 s are 2.40 s apart too), that is
**0.5996 s per picture** (about 1.7 pictures per second; one loop of the two
pictures about 1.2 s). Only 5 intervals were measured, so the figure is good to
about one camera frame (35 ms of file time = 9 ms real) divided by 5.

| `XX XX` | time per picture | ms per unit |
|---|---|---|
| `0018` = 24 | 0.0400 s | 1.67 |
| `0030` = 48 | 0.0902 s | 1.88 |
| `0064` = 100 | 0.2025 s | 2.03 |
| `012c` = 300 | 0.5996 s | 2.00 |

The straight-line prediction (0.63 s) was 5% too high, so the three-point line
was wrong. For 100 and 300 the time is close to 2 ms x `XX XX` (+1.2% and
-0.1%); below that the pictures are shown for less than that (-6% at 48, -17% at
24). Nothing explains the small end (a fixed cost per change that the speaker
saves for short times? a different timer? not tested). What can be said from
the four measurements: larger value = slower, roughly 2 ms per unit from 100
upward; the exact function below 100 is not known. This is a fit to measured
points, not a documented formula.

#### Fifth value: `XX XX` = `0190` (upload 20, 2026-10-26, user-approved)

The user asked for a larger value, because small values may be affected by
rounding or by the speaker itself (their explanation for the small-end
deviation; not verified). Same 2-picture animation, only `XX XX` = `0x0190`
(400, the largest the code allows). 46/46 frames, 45 replies, all matched,
2.67 s. The user's video `20261026_074331.mp4` (120 fps at 0.25x, 31.3 s file
time): 10 brightness dips from 1.78 s to 30.60 s (file time; the event pairs
of the picture changes sit at the same moments), 9 intervals, mean 3.202 s of
file time = **0.8005 s per picture** (about 1.25 pictures per second; one loop
about 1.6 s). Prediction before sending (2 ms x 400): 0.800 s.

| `XX XX` | time per picture | ms per unit |
|---|---|---|
| `0018` = 24 | 0.0400 s | 1.67 |
| `0030` = 48 | 0.0902 s | 1.88 |
| `0064` = 100 | 0.2025 s | 2.03 |
| `012c` = 300 | 0.5996 s | 2.00 |
| `0190` = 400 | 0.8005 s | 2.001 |

So from 100 up the time per picture is 2 ms x `XX XX` within about 1% (three
points: 100, 300, 400); the prediction for 400 was met. The small end (24, 48)
is faster than that. Whether the small-end deviation is the speaker's or a
measuring effect is still open: the figures at 24 and 48 are means over 87 and
59 changes, so noise from the camera frame is small, and the TempoHub scene
上岸 (`0030`) gave the same 0.090 s as the generated animation, but at 24 the
changes were found as brightness dips, not as a change of the picture.

Not known: values above 400 (none has been tried; the code refuses them);
whether very small or large values are safe for the speaker; what `ffff`
means; where the field ends (it is two bytes). Values outside `0010`-`0190` stay refused by the code; any
value of an unknown field needs the user's approval for each use.

### Sixth value: `XX XX` = `01f4` (500) with the Bad Apple pictures (upload 22, 2026-10-26, user-approved, one upload only)

The user asked for this single test (「XX XX=500 的测试，只做这一次」): the 20
pictures of upload 9 (`ba_01..ba_20_gray.png`: one frame per second of a video, t = 0
to 19 s, shrunk to 32 pixels high and centred, threshold 128, not inverted) with
only `XX XX` changed from `0064` to `01f4`. The speaker showed white `Hello`
first (CLI; user-confirmed 「是，正常」). Dry run: 429 frames, 163 of them differ
from the same upload with `0064`, 320 bytes besides checksums differ (the changed
bytes of `XX XX` in 160 records), nothing else. Upload: 429/429 frames written, 428
replies, all matched, no unsolicited frame, 23.27 s, TempoHub process count 0,
sent once, no retry (counter 21 -> 22 of 30).

Reported by the user: 「1正常」 (the loop is normal) and that the slow-motion
video (120 fps at 0.25x) is in Downloads (`20261026_102522.mp4`, 39.9 s of file
time). The user did not give a stopwatch time for one loop; the video, not a
stopwatch, is the measurement.

Measured from the video (screen region only, lit pixels, real seconds = file time
/ 4; the camera had moved, the LED strip below the screen changes colour and was
left out): the picture changes at 2.26, 6.20, 10.18, 14.23, 18.22, 26.31, 30.33,
34.35 and 38.26 s of file time; one change (22.2 s) was missed by the detector
(the pictures there differ little, 8.10 s gap = two changes). From the first to
the last of these, 36.00 s of file time over 9 changes = 4.000 s each, that is
**1.000 s per picture** (individual gaps 3.91-4.05 s, i.e. 0.98-1.01 s real).
Prediction before sending (500 x 2 ms): 1.0 s per picture, 20 pictures about 20
s. So the per-picture time matches the extrapolation, **measured** for this
one upload. One whole loop (20 s real = 80 s of file time) is **not** in the
video (it holds about 10 pictures); the 20 s per loop is arithmetic from the
per-picture time, not an observation. No stopwatch figure exists for it.

| `XX XX` | time per picture | ms per unit |
|---|---|---|
| `0018` = 24 | 0.0400 s | 1.67 |
| `0030` = 48 | 0.0902 s | 1.88 |
| `0064` = 100 | 0.2025 s | 2.03 |
| `012c` = 300 | 0.5996 s | 2.00 |
| `0190` = 400 | 0.8005 s | 2.001 |
| `01f4` = 500 | 1.000 s | 2.000 |

From 100 up the time is 2 ms x `XX XX` within about 1% at 100, 300, 400 and 500 (the
last point is one upload, 9 intervals, and a different set of pictures). Still
untested: values above 500 (the code refuses them), small values below 24.

## Filming a long video, 20 pictures at a time (development, 2026-10-26; nothing sent)

Idea (the user's): the speaker shows one picture per second by itself (XX XX = 500),
so a long video can be filmed from the speaker's screen without speeding anything
up. One upload holds at most 20 pictures (the most tried), so the video is cut into
segments of 20 pictures = 20 s of play; each segment is uploaded, played once for
20 s while a phone films the screen in the normal video mode, and the recording is
cut afterwards. State of each piece:

| Piece | Command / file | State |
|---|---|---|
| Frames | `python -m pixelbar film frames VIDEO [--fit contain] [--no-invert] [--mode threshold] [--dir D]` | written, tested on a generated video, **run once** on the Bad Apple video (the path the user left as a placeholder was taken to be the only Bad Apple file known, `Downloads\Bad Apple but it's in 4k 60fps.mp4`: an assumption): 220 pictures (t = 0..219 s), 99.7 s, defaults contain + invert + threshold 128; written with 11 contact sheets to `C:\pixelbar-raw\badapple\` (outside the repository) |
| Plan | `film plan [--pictures N] [--max-uploads 3]` | written, tested, prints only |
| Capture run | `film run --rehearse` | written, tested; the rehearsal is on a virtual clock and sends nothing. `film run --send` is **refused** (`SEND_ENABLED = False` in `pixelbar/film.py`); `run_film` and `make_uploader` exist and are tested with fake devices, nothing calls the real one |
| Post-processing | `tools/film_post.py VIDEO LOG` | **draft**: pure functions tested on synthetic logs, concatenation tested on a generated video; **never run on a real recording** |
| Earlier tools | `animation`, `slideshow`, `image`, `prepare`, `gui` (video to pictures inside the window), `tools/screen_timing.py` | existing, see above |

Polarity (the user's decision, 2026-10-26): keep the polarity of the original video, no
inversion, no automatic polarity: the pictures were re-extracted with `film frames
VIDEO --no-invert --fit contain --mode threshold --threshold 128 --overwrite` (220
pictures, 32 s, written over the inverted ones in `C:\pixelbar-raw\badapple\`, with
11 contact sheets and `overview_every4th.png`). `film stats` (read-only) on them: the
share of lit pixels is at most **16.8%** (1376 of 8192 pixels: picture 93; the full
frame of a 4:3 video "contained" into 256x32 is 43x32 pixels, so no picture can be
brighter than that with this fit), mean 7.6%, median 7.1%; **0 pictures above 25%**;
4 completely dark, 82 above 10%, 20 above 15%; per segment the maximum is 9.9-16.8%
and the mean 3.7-13.4%. So the brightest moment lights about one sixth of the screen
(an all-white block of 43x32 pixels in the middle). How bright that looks to the eye
or the camera, and whether it matters for the speaker, was not measured.

Plan for the whole Bad Apple video (`film plan`): 220 pictures, 11 segments of 20
(1-20, 21-40, ..., 201-220), each: upload 23.3 s (measured, upload 22: 23.27 s) +
play 20 s + buffer 2 s = 45.3 s; all 11: 498.3 s (8.3 min). **Upload quota: 22 of
30 used, 8 left; the plan needs 11, which is 3 too many** (the total limit would
have to be 33); the program only reports it, `limits.json` is changed by the user
only. A trial of 3 segments (`--max-uploads 3`, the default) fits and takes about
136 s.

Capture run (as designed): uploads the segments in order, after each upload checks
every reply (the first mismatch, timeout or exception stops everything, no retry),
waits 22 s (20 s of play + 2 s of buffer, also after the last), logs the wall-clock
time of every upload start and end to the millisecond in `film_log.jsonl` (outside
the repository), never makes more than `--max-uploads` uploads (default 3), and
before sending would ask whether TempoHub is fully closed and whether the phone is
already recording in the normal video mode (not slow motion). Each upload is the
same single bounded upload as `slideshow --send`, with the usual limits and the log.

Post-processing draft: ties the recording to the log with one anchor, the second
of the recording at which the first upload started (given by hand with `--anchor`;
without it the script prints a GUESS, the first big change of the screen picture
after a quiet start, and refuses to write), computes the window of every segment
(end of its upload to end of its 20 s of play, with optional `--lead`/`--trim`),
and joins the windows in order without changing the speed of the frames.

### Three-segment trial (2026-10-26, user-approved: 「我确认计划和预览，现在做 3 段试跑」, then 「开始」)

Original polarity (no invert), segments 1-3 = pictures 1-60, `XX XX` = `0x01f4`. Sending
was enabled for this one run only (`film run --send --this-run-only` with the three
confirmations; `SEND_ENABLED` stays `False` in the code). Before it: TempoHub process
count 0, the screen showed white `Hello` (CLI, reply as usual), the user confirmed the
phone was recording in the normal video mode. **First attempt refused before anything
was sent**: segment 1 (pictures 1-20) is bitmap-identical to upload 22 (hash
`062242de80f8`), and the "same as the last completed upload" rule stopped it (counter
still 22, no log). The user chose to repeat it (「2」): only the `ok` line of upload 22
was removed from `~/.pixelbar/upload_log.jsonl` (backup kept next to it, `limits.json`
untouched), then the run was started again.

Log `C:\pixelbar-raw\badapple\film_log.jsonl` (outside the repository; wall clock of the
computer, millisecond precision; `t` = seconds since `run_start`):

| Segment | Pictures | Upload start (wall) | Upload end (wall) | t start - t end | Device replies | Wait |
|---|---|---|---|---|---|---|
| 1 | 1-20 | 10:53:18.244 | 10:53:41.674 | 0.047 - 23.485 s | 429/429 frames written, 428 replies matched, no unsolicited frame; the `ef` reply carried the current colour `ffffff`, last data reply `00 03 01 a8`, end reply `00 05` | 22 s, 10:53:41.677 - 10:54:03.691 |
| 2 | 21-40 | 10:54:03.798 | 10:54:27.571 | 45.610 - 69.375 s | the same: 429/429, 428 matched, none unsolicited | 22 s, 10:54:27.572 - 10:54:49.585 |
| 3 | 41-60 | 10:54:49.693 | 10:55:13.514 | 91.500 - 115.328 s | the same: 429/429, 428 matched, none unsolicited | 22 s, 10:55:13.515 - 10:55:35.530 |

Upload times 23.17, 23.36 and 23.45 s (the plan used 23.3 s); the run stopped by
itself at 10:55:35.532 (`--max-uploads 3 reached`, 137.3 s in total). All replies matched
in all three uploads, no timeout, no exception, nothing was retried; the budget counter went
from 22 to **25 of 30** (uploads 23, 24, 25; the aborted first attempt did not count).
Whether the screen showed anything unusual during the three uploads and the passes, and
whether the recording is usable, was not yet reported by the user at the time of writing
(**not verified**).

**Redo (the same day, at the user's request).** The user had paused the recording by
accident in the middle of the first run (the user's words, 「我剛剛中間不小心要暫停了我可以重來一次嗎?」),
so the three segments were sent again; the first run's log was renamed
`film_log_run1_interrupted.jsonl` and is not used for cutting. The first start of the redo
was refused **before anything was sent** by the pre-flight check, which compared
segment 3 with the last completed upload (segment 3 of the first run): a mistake in
the check, not in the device; it now judges "the same as the last completed upload"
along the sequence (segment 1 against the log, each later segment against the one before
it; the send itself re-checks every segment against the log at its turn; tests added).
TempoHub was closed (process count 0 after the user closed it from the tray), the screen
showed `Hello` again (CLI), the user said 「開始」, and the run went through:

| Segment | Pictures | Upload start (wall) | Upload end (wall) | t start - t end | Device replies | Wait |
|---|---|---|---|---|---|---|
| 1 | 1-20 | 11:03:05.891 | 11:03:29.804 | 0.046 - 23.953 s | 429/429 frames written, 428 replies matched, none unsolicited | 22 s, 11:03:29.808 - 11:03:51.822 |
| 2 | 21-40 | 11:03:51.888 | 11:04:15.895 | 46.046 - 70.046 s | the same | 22 s, 11:04:15.897 - 11:04:37.913 |
| 3 | 41-60 | 11:04:38.000 | 11:05:01.834 | 92.156 - 115.984 s | the same | 22 s, 11:05:01.836 - 11:05:23.851 |

Upload times 23.53, 23.56 and 23.55 s; stopped by itself at 11:05:23.853 (`--max-uploads 3
reached`, 138.0 s). Counter: **28 of 30** (uploads 26, 27, 28). All replies matched, no timeout,
no retry. Cycle length segment start to next segment start: 45.84 s and 46.11 s (the plan:
45.3 s; the difference is the 0.05-0.1 s the program takes between segments plus the slightly
longer uploads).

### Full 11-segment run, stopped at segment 3 (2026-10-26, 11:45-11:46)

After the user's go-ahead (「開始」; TempoHub process count 0, `Hello` on the screen, quota 28 of 44,
`--max-uploads 11`, the user filming) the run uploaded segments 1 and 2 completely and was **stopped by the first
mismatching reply in segment 3**, as designed (no retry, nothing re-sent). Log `film_log.jsonl`:

| Segment | Upload start (wall) | Upload end (wall) | Result |
|---|---|---|---|
| 1 (pictures 1-20) | 11:45:05.894 | 11:45:29.556 | 429/429 frames, 428 replies matched, 23.313 s |
| 2 (pictures 21-40) | 11:45:51.644 | 11:46:15.237 | 429/429 frames, 428 replies matched, 23.312 s |
| 3 (pictures 41-60) | 11:46:37.332 | 11:46:38.317 | **stopped at step 12** of 429: 13 frames written, 12 replies matched; the expected reply was the acknowledgement of data chunk 10 (`2fbbec17000400030 00a`...), the speaker sent `2fbbec7700010221` instead (command `77`, payload `02`, valid checksum); 0.734 s |

Counter 31 of 44 (uploads 29, 30, 31). The pre-flight check, the ef frame (reply: current colour `ffffff`), the start frame
and ten data acknowledgements of segment 3 were all as before. **What the stray frame is is not known**: the speaker is
documented to send an unsolicited `77` with payload `02` at times (captures 04 and 08, just before the app's time
synchronisation; guess: a request for the time), but never during an upload before; why it came exactly here (its
timing 11:46:38, a time sync due, the user touching the speaker or the phone being moved, any other cause) is **not
known** (no test, only the log). The upload code treats only unsolicited `ee` and `66` frames as ignorable; `77` is a mismatch,
so the exchange stopped. The device was left with an incomplete upload of segment 3 (the start frame and 11 of 426 data
chunks were written, no end frame): its screen state was not yet reported by the user (**not verified**); an official scene
in TempoHub restores it. No rule was changed because of this; accepting `77` as ignorable would need the user's approval.

**Second full run, stopped at segment 4 (11:54-11:56, same day, the user's 「开始」 after 「录影已经停了，11 段全部重来」).**
Segments 1-3 went through completely (429/429 frames, 428 replies each; uploads 23.33, 23.28 and 23.30 s; upload starts
11:54:18.109, 11:55:03.825 and 11:55:49.608, upload ends 11:54:41.717, 11:55:27.508 and 11:56:13.198; waits 22 s). Segment 4
(pictures 61-80) started at 11:56:35.273 and was stopped at **step 5** (6 frames written, 5 replies matched, 0.359 s): the
speaker sent **the same frame again, `2fbbec7700010221`** (`77`, payload `02`), instead of the acknowledgement of data
chunk 3. The run stopped, nothing was retried or re-sent (counter **35 of 44**, uploads 32-35; the user was told what
happened).

Observation (**a coincidence of two events, not a measurement of a cause**): the two stray frames came at 11:46:38.3 and
11:56:36.0, that is **597.7 s apart (about 10 minutes)**, and both fell into an upload. If the speaker sends `77` `02` about
every 10 minutes (guess: it asks for a time synchronisation periodically; the captures 04/08 show it before the app's
time sync), a run of 8.3 minutes will meet it with a probability of about 83% unless it is started shortly after one, and the strict
rule (only `ee` and `66` are ignored) stops the run every time. Untested: the period, whether sending a time
synchronisation (`77` with the date, as TempoHub does) stops the requests, whether the frame is harmless in the middle of an
upload (the next acknowledgement may follow it, as with `ee`/`66`). No rule or code was changed because of it.

**Rule change at the user's request** (「1要2還在，接著」, after being offered it): the exact frame `2fbbec7700010221` (the
speaker's `77` with payload `02`, checksum included) is now recorded and ignored during an upload like the `ee` and `66` pushes
(`TIME_REQUEST_PUSH` in `pixelbar/upload_replay.py`); any other `77`, another payload, a wrong checksum or any other command
still stops the run (tests). The recording was still running, so the run was **continued with segments 4 to 11**
(`--first-segment 4 --max-uploads 8`, no `Hello` shown first; quota 35 -> 43 of 44), log `film_log.jsonl`
(the logs of the earlier runs were renamed `film_log_run*_...jsonl`):

| Segment | Upload start | Upload end | Replies |
|---|---|---|---|
| 4 | 11:59:47.747 | 12:00:11.400 | 429/429, 428 matched, **1 unsolicited frame ignored** |
| 5 | 12:00:33.536 | 12:00:57.399 | 429/429, 428 matched |
| 6 | 12:01:19.509 | 12:01:43.149 | 429/429, 428 matched, **1 unsolicited frame ignored** |
| 7 | 12:02:05.222 | 12:02:29.096 | 429/429, 428 matched |
| 8 | 12:02:51.176 | 12:03:15.038 | 429/429, 428 matched |
| 9 | 12:03:37.132 | 12:04:01.038 | 429/429, 428 matched |
| 10 | 12:04:23.145 | 12:04:46.928 | 429/429, 428 matched |
| 11 | 12:05:09.030 | 12:05:33.068 | 429/429, 428 matched |

All eight uploads matched (23.4-23.6 s each), the run ended itself at 12:05:55.084 (`run_end`, 8 uploads, 367.4 s); counter **43 of
44**. **My earlier guess (a `77` push about every 10 minutes) was wrong**: two more `77` frames came during segments 4 and 6
(one in each; their exact times were not kept, only the count, so they lie between 11:59:47-12:00:11 and 12:01:19-12:01:43), that
is 3 minutes 20 s and 1 minute 30 s after the earlier ones at 11:56:36.0, not 10. The four known `77` `02` frames (11:46:38.3,
11:56:36.0, and one each in segments 4 and 6) are not periodic as far as seen; **why the speaker sends them is not known** (a time
synchronisation request is only a guess from the captures 04/08; whether it happens when the phone is moved or the speaker touched
was not tested). Nothing was sent in answer to them. The screen showed no problem in the recording (not yet looked at).

### The whole Bad Apple filmed and edited (2026-10-26)

The user's long recording (`20261026_115327.mp4`, normal video mode, 1280x720, 30 fps, 798.8 s, 2.4 GB, in `Downloads`,
not in the repository) covers the second full run (segments 1-3, 11:54:18-11:56:13), the stop and the failure screen, and the continued
run (segments 4-11, until 12:05:55). The start of the recording is on purpose the computer's monitor (user: 「電腦螢幕但是故意的」),
the phone is held still until 20.4 s and then moved down to the speaker's screen between 20.4 s and about 25 s (measured from the
frame differences). **Seen on the speaker's screen in the recording (confirmed): after the stopped upload (segment 4, 11:56:36) the
speaker itself shows 「图片传输失败，请重新上传」 ("picture transfer failed, please upload again") about a minute later (at 200 s of
the video, 11:57:38)** — the user's 「圖片傳輸失敗請重新上傳」 was this text. Also confirmed: the scene loops (pictures 1-20 again and
again) until the next upload starts, the text 「图片上传中」 follows with changing dots.

Mapping of the log (the computer's clock, both logs by wall time) to the video: the 1-second picture grids of segments 2-11 fix it to
about 0.03 s up to a whole second (concentration R 0.35-0.88, offsets -0.09 to -0.19 s around the best fit, the same for the first
and the second run, which shows that the wall clock of the computer is the common reference); the whole second was chosen with the
appearance of the upload text of segment 2 at about 43.5 s (its upload started 45.72 s after the first upload start; so the video
second of the first upload start is -2.77, i.e. the recording began 2.77 s after the first upload started) and the first picture
seen at 24 s being picture 4. The text appeared about 0.5 s after the log's upload start in this recording (about 0 s in the
earlier one): the latency of the upload text is not constant (not explained). The anchor used: **-2.77 s**; windows (video
seconds): segment 1 20.84-40.84, 2 66.63-86.63, 3 112.32-132.32, 4 350.52-370.52, 5 396.52-416.52, 6 442.27-462.27, 7 488.22-508.22,
8 534.16-554.16, 9 580.16-600.16, 10 626.05-646.05, 11 672.19-692.19. Opening: the video from 19.40 s (1.4 s of the still monitor, then the
pan to the speaker while segment 1 already plays; user's wish: 「影片剛開始就是要對著電腦螢幕然後慢慢滑到我們音箱那邊」, the long still part
cut away) to 20.84 s, no music, the original video on its first picture.

Result `C:\pixelbar-raw\badapple\final_bad_apple_filmed.mp4` (outside the repository): H.264 1280x1440 (the recording on top, the
original Bad Apple video below it, in step), 30 fps, **221.45 s** (1.44 s opening + 220.0 s), AAC audio **220.52 s**: the music of the
original video from its second 0 (the recording's own sound is not in it); the original's audio is 219.08 s long, so the last 0.9 s are
silent. Written in 857 s (seeking to each window; the 800-s, 2.4-GB recording; the second of two passes over the original video for the
lower half). Checked by looking at frames at 0.5, 1.3, 2.5, 5, 25, 60, 110, 150 and 221 s: the pan from the monitor to the speaker, and
at every sampled second the lit picture of the speaker is the same picture as the original below it (silhouette of the same shape and
polarity); no upload text in the sampled frames. Not checked: every second of the 221 s (frame by frame), the sound against the picture
by ear, the exact join of the windows.

### Post-processing of the redo recording (2026-10-26)

The user's phone recording (normal video mode, 1280x720, 30 fps, 257.2 s, 778 MB, in
`Downloads`, not in the repository) covers the whole redo. The user's anchor, 「大概1:30」,
fits: the first screen change in the video is at **86.91 s** and shows the text
**「图片上传中...」** (the dots animate; later 「图片上传中」 / 「图片上传中.」): that is what the
speaker shows while a scene is being uploaded (**confirmed from the recording**; before, the
white `Hello`). The phone's clock was about 50 s behind the computer (file name 11:00:45 vs the
computer's 11:01:36; consistency with the end of the recording, not used for the cutting).

Calibration: the picture changes of a pass sit on a 1-second grid (events 112.37, 113.37, ... in
pass 1). Locking that grid (circular mean, concentration R 0.68-0.72, residual sd 0.14 s of the
detector) gives the start of the three passes at video **110.37, 156.48 and 202.42 s**, which is the
log's upload end plus a constant **86.42 s** within -0.01, +0.02 and +0.01 s. The next upload's
text appears at 132.41 s (log + 86.42 = 132.47) and 178.5 s (178.58). Only the very first
appearance, 86.91 s, is 0.44 s later than the same mapping gives (86.47): the first upload replaced a
plain text, the later ones a scene (an interpretation: the first screen change is slower;
not tested). So the anchor read from the first appearance of the upload text is about 0.45 s too late;
the script was run with the anchor **86.466 s** (= 86.42 s + the log time of the first upload start, 0.046 s).

Windows (the 20 s of each pass, 20 pictures x 1.0 s): segment 1 **110.37-130.37 s**, segment 2 **156.47-176.47 s**,
segment 3 **202.40-222.40 s**; the upload text is gone before each window (it disappears at about 110.1-110.3 s in
segment 1) and the next upload's text comes 2.0 s after each window ends (132.41 s), so the 2-s buffers hold the changeover.
Draft `C:\pixelbar-raw\badapple\draft_3segments.mp4` (outside the repository): the windows joined in order, 1800
frames, 30 fps, **60.00 s** (3 x 20.00 s), 1280x720, H.264, re-encoded without changing the frame spacing (no speed
change). Checked by looking at frames at 0.5, 10, 19.5, 20.5, 30, 39.5, 40.5, 50 and 59.5 s: no upload text at any
of them, the first pictures of each segment are the dark opening pictures of the video and the last ones small
pictures, as the frame lists say. The tool's first write attempt failed (`mpeg4` could not be opened at 1280x720 without a
bit rate); it now uses `libx264` and falls back to `mpeg4` with a bit rate.

After the draft the user said it looked normal and readable but a little blurry (「看起來都挺正常的 … 能看出來但是感覺有點糊」)
and that the recording will be redone. Two changes asked for the cutting: **drop the sound of the recording and lay the music of
the original video under the pictures** (`film_post.py --music "Bad Apple ... .mp4"`; the music file is read from `Downloads`, not
copied into the repository): 1 picture per second of the video means segments 1..n in order are the MV's seconds 0..20n, so the
sound is taken from second 0 for as long as the joined windows (`--music-start` for another start). Checked on the existing
recording: `draft_3segments_music.mp4`, 60.0 s of video (H.264) and 60.0 s of audio (AAC), the loudness envelope of its sound has a
correlation of 0.993 with the first 60 s of the MV (the recording's own sound is not in it). Each picture is the video's frame at
the start of its second and stays for one second, so the picture can be up to half a second ahead of or behind the music
(a property of 1 picture per second, not measured further). **Cutting time**: the windows are now reached by seeking to the
keyframe before each; the 60-s draft with music took 94 s from the 257-s, 778 MB recording (before the seek change about 3 minutes
of decoding for the video alone were needed); for 11 segments (220 s of output from about 8.5 minutes of recording, probably
1.5-2 GB at this bit rate) about 3-6 minutes are to be expected (an estimate, not measured).

Not checked: that the joins look smooth to the eye (the user decides from the draft), the exact picture at the cut
(an edge of up to one camera frame = 33 ms), and the quality of the picture of a 43x32-pixel block on a 720p recording.

**Not verified yet**: that the speaker plays a 20-picture scene at exactly 1.0 s per
picture over a whole 20-s pass (only 9 changes of one upload were measured); what the
screen shows while an upload is running (the "uploading" picture the alignment idea
relies on is not known); how soon after the last reply of an upload the first picture
of the new segment appears, and whether the previous segment keeps playing until then;
that the speaker accepts 3 or 11 uploads in a row at this pace; the 45.3-s cycle with
a phone camera in real time (clock drift between the phone and the computer: the log
uses the computer's clock, the anchor is read from the video); that the video quality
of the phone recording is enough to read a 256x32 screen; the concatenation with a real
recording; whether invert + threshold 128 is the right look (the user decides from the
previews); and whether more than 20 pictures per upload could be used (see the limits
section).

## Allowed `XX XX` range and the "same upload" rule (changed 2026-10-26, at the user's request)

- The code accepts `XX XX` from `0x0030` (48) to `0x01f4` (500) for uploads of
  several pictures; everything else (including `0xffff`) is refused. The GUI
  offers only 0.2-0.8 s (`0x0064`-`0x0190`).
- **Calibrated: `0x0018`-`0x0190` only** (measured values: 24, 48, 100, 300,
  400). **Above `0x0190` there is no calibration data**: `0x01f4` (500) is an
  extrapolation of "about 2 ms per unit", predicting about 1.0 s per picture,
  and has to be checked with a stopwatch. Nothing is known about whether the
  speaker accepts such a value at all.
- The limits and the log now treat "the same pictures with a different
  `XX XX`" as a different upload; only identical pictures with an identical
  `XX XX` are refused as a repeat (`0x0064` is the plain upload). The upload
  identity is the SHA-256 of the bitmap, followed by `"XX"` and the two bytes
  of `XX XX` when it is not `0x0064`.
- With `--xx` the dry run also prints how the upload differs from the same
  upload with `0x0064`: only the changed bytes of `XX XX` in every record and
  the checksum of every frame that holds one (check written from the stream
  layout, not from the builder).

## Limits: what the captures and the live tests say about sizes (read-only analysis, 2026-10-26)

Nothing was sent for this section. Tools: `python tools/analyze_replies.py
C:\pixelbar-raw` (every device-to-host frame of all 28 captures, per command:
payload, which host command preceded it, which byte offsets ever vary; needs
tshark) and `python tools/boundaries.py` (arithmetic only). The captures hold
489 device-to-host frames and 513 host-to-device frames; every checksum is
right.

### 1. Device replies that could mean a size, a count or a buffer

The device-to-host commands are `17` (uploads, 395 frames), `65 66 68 6a 77 84
86 87 c6 c8 c9 d5 d7 d8 f0` (start-up queries, captures 01 and 06 only, plus
`66`/`77` also elsewhere), `e7`, `e8`, `ee`, `ef`. There is no reply anywhere
that is documented or seen to carry "free space", "scene count left" or "maximum
pictures"; no query was sent after an upload; the speaker never reports a
remaining size after the end frame (`00 05`, no data). The candidates below are
only numbers that could be sizes. Offsets are payload offsets (frame offset =
payload offset + 6).

| Command | Payload (reply) | Offset: value | Reading | Status |
|---|---|---|---|---|
| `17` ready (reply to the start frame) | `00 01 00 32` (all 8 captures, and every upload we sent) | 2-3: `0x0032` = 50 | chunk size: the host cut the data into 50-byte chunks in all 8 TempoHub uploads and all of ours, each acknowledged | **confirmed** that 50-byte chunks are accepted; that the speaker *announces* the size with these bytes: guess (never saw another value; no other chunk size was ever tried) |
| `17` acknowledgement | `00 03 NN NN` | 2-3: chunk number (captures: up to `0x0093` = 147; our uploads: up to `0x01a8` = 424) | chunk number, two bytes | **confirmed** two bytes: the speaker answered `00 03 01 a8` ... in our 20-picture uploads |
| `65` | `02 00 00 40` (captures 01, 06) | 2-3: `0x0040` = 64 big-endian, or `0x4000` = 16384 little-endian | 64 = the report size of the vendor collection (`63` bytes of data + report id) or the largest packet; 16384 = 16 KiB of something | **guess**, no evidence for either. 16384 as the capacity of one scene is contradicted by the live uploads: 21280 bytes were accepted, acknowledged and all 20 pictures were shown (user-confirmed); if it is a capacity it is of something else |
| `d8` | `00 00 01 05 01 01 01 00 01 00 01 01 0a 0e 0a 00 31 a0 00 83 00 30 00 00 00 a0 08` (27 B, the same 4 times) | 16-17: `0x31a0` = 12704; 18-19: `0x0083` = 131; 20-21: `0x0030` = 48; 24-25: `0x00a0` = 160; 12-14: `0a 0e 0a` = 10, 14, 10 | unknown table; the "capability table" in the earlier table is a guess | **guess only**. Remark: 160 is the number of records of a 20-picture upload (8 per picture) and the number of pictures we stopped at, 20; and `0x0030` is also one of the `XX XX` values TempoHub uses. Both could be coincidences. No test separates them yet |
| `e7` (read the text back) | `00 10 32 LL text` (12 frames, 7 captures; `00 20 32 ...` once) | 2: `0x32` = 50 in every reply; 3: `LL` = text byte count (confirmed, captures 07-09) | `0x32` could be "at most 50 text bytes" | **guess**. Against it as an enforced limit: a 55-byte text was acknowledged and shown completely in scroll mode (live length tests) |
| `6a` | 44 bytes, identical every time | repeated groups `3c 0a` / `3c ff` after indexes 02..07 | a table with a number 60 (`0x3c`) and 10 per entry | unknown, no size-like field recognised |
| `c8` | `c8 24 78 bb c6 6f` | all 6 | address-like | guess |
| `c9` | UTF-8 text `花再 Halo PixelBar` | all 20 | device name | **confirmed** (decodes as text) |
| `c6` | `01 03 06` | 0-2 | a version? | guess |
| `ee` | 12 bytes, 6 distinct (mode/colour state) | see the display-mode section | state, not a size | confirmed as state, see above |
| `66`, `77`, `84`, `86`, `87`, `d5`, `d7`, `68`, `f0` | 1-3 bytes | `66`: `10 0b` / `10 08`; `77`: `01` or `02`; `84`: `05 01`; `86`: `0e 01 01`; `87`: `02 02 01`; `d5`: `00`; `d7`: `01 01`; `68`: `00`; `f0`: `1a ff 01` | small values | no size-like field recognised; meanings unknown |

What the live uploads add (user-confirmed): 20 pictures, 160 records, 21280 bytes,
426 chunks were accepted, all replies matched, and the speaker showed all 20 pictures
in order (20 pictures of the generated animation and of the video frames). So the
capacity of the scene slot is at least 20 pictures / 21280 bytes. The upper limit
is **not known**; nothing above 20 pictures has been sent (and the code refuses
more than 20).

### 2. The record header `T 00 NN XX XX`

- The header is **5 bytes**, not 6: `T`, a two-byte field `00 NN`, then `XX XX`
  (so `0x0064` appears as the bytes `00 64`). This is confirmed: 133 = 5 + 128
  for every record, and the builder reproduces the captured uploads byte for
  byte. (A header written as `T 00 NN 00 XX XX` with an extra `00` after `NN`
  would have 6 bytes and does not match the captures; the `00` before `64` is
  the high byte of `XX XX`.)
- Is `NN` one byte, or is `00 NN` a two-byte index? The captures cannot say:
  byte 1 is `00` in every record of every upload, and the largest index ever
  seen in a capture is 55 (capture 33, 56 records). Our own uploads went to
  index 159 (`00 9f`, 20 pictures), still below 256, and worked. **No
  evidence** either way. Not tested: an index of 256 or more.
- What the boundary means: 8 records per picture, so **32 pictures = 256
  records = indexes 0..255**, the last picture a one-byte index can address.
  A 33rd picture needs index 256: with a two-byte index (`01 00`) it is
  addressable; with a one-byte index it wraps or is refused. Which one the
  speaker does is unknown; so is whether the speaker uses the index at all
  (we know only that the records were sent in order, 0.. without gaps).
  The total-size boundary comes before it: see below (30 pictures stay under
  32767 bytes; 32 pictures are 34048 bytes).

### 3. Boundaries beyond 20 pictures (arithmetic: `python tools/boundaries.py`)

One picture = 1024 bytes of image = 8 records = 1064 bytes in the upload (the
size the start frame declares); 21.28 chunks of 50 bytes. These are the points
where a field or a size would run out, not facts about the speaker:

| Boundary | Pictures that stay below it | Next picture needs | Status |
|---|---|---|---|
| 16 KiB (16384 bytes) total | 15 (15960 B) | 17024 B | **passed**: 20 pictures (21280 B) worked |
| Total 32767 (`0x7fff`) / 32768 | 30 (31920 B) | 32984 B (31 pictures) | not tested; relevant if the size is a signed 16-bit number |
| Total 65535 (`0xffff`) / 65536 | 61 (64904 B) | 65968 B (62 pictures) | not tested; relevant if the size is a 16-bit number (the start frame has a 32-bit field, `00 00` + four bytes; its upper bytes were always 0 in all uploads) |
| Record index 127 / 128 (signed byte) | 16 | 17th picture (index 128) | **passed**: 20 pictures (indexes up to 159) worked, so a signed one-byte index caused no trouble (if the speaker uses the index at all) |
| Record index 255 (one byte) | 32 (34048 B, 256 records) | 33rd picture (index 256) | not tested |
| Chunk number 255 (one byte) | 12 (256 chunks) | 13 pictures = 277 chunks | **ruled out**: our uploads used chunk numbers up to 425 and the speaker acknowledged `00 03 01 a8` ... (two bytes) |
| Chunk number 65535 (two bytes) | 3079 | 3080 | theoretical; the upload would take about 50 minutes at 46 ms per chunk |
| This repository's own limit | 20 (426 chunks, 21280 B, 429 frames) | 21 | set by us (`MAX_SLIDES`), not by the speaker |

Frames of an upload = 1 `ef` + 1 start + one per chunk + 1 end (20 pictures:
429; 30 pictures: 642; 32: 684; 62: 1323). The exchange time limit in the code
(`total_limit_for`) grows with the chunks (57 s for 20 pictures; 82 s for 30).

### What an experiment beyond 20 pictures could show (not done, needs the user's approval)

Pictures 21-30 stay below 32767 bytes and below index 255, so the first useful
steps are 21 pictures (168 records; tests the `d8` candidate 160), then 30
(31920 B; the last size below 32767), then 31 and 32 (34048 B; index 255
exactly); 33 pictures would be the first with a two-byte index. Each step is a
real upload (one budget unit, the usual limits). The code has to be changed to
allow it (`MAX_SLIDES`, `testanim` makes only 20 pictures). The speaker may
refuse, truncate, play fewer pictures or misbehave; none of that is known.

## Unknown fields in the scene upload, and the risk of changing them

"Risk" lines are guesses about what could go wrong; none was tested. The
builder takes these values as inputs; until their meaning is known, a real
upload should only repeat the captured values.

| Field | Confirmed | Not known | Risk if changed (guess) |
|---|---|---|---|
| Record header `T` (`01`, `02`, `03`, `04`) | same in every record of an upload; equals `ef` byte `p6` in captures 20, 21, 22, 23, 30, 31, 32, 33; by the user's category names: game `01`, work `02`, study `03`, cat `04` (see "Animation scenes") | what it means (type, category, slot?) | the speaker may refuse the upload, or file the image under another scene, or overwrite another scene's data; we do not know whether the speaker keys scenes on it |
| Record header `XX XX` (`0064` in 20, 31, 32, 33; `0030` in 21 and 22; `ffff` in 23 and 30) | same in every record of an upload; the same value for the same data (21, 22); not tied to the number of images (1, 2 or 7 images with `0064`) nor to the category (study: `0030` and `0064`) | what it means; whether it is checked against the data | if it is something like a frame delay, a wrong value changes how a 2-image scene plays or hides it; if it is a check value, a wrong one may get the upload refused. Only three different values were seen, so a check value cannot be excluded (a guess that it is not one: they look like round numbers) |
| `ef` frame `sel` `01`, `p4` `00`, trailing `ff` | constant in captures 20-25 | meaning | the speaker may ignore or reject the frame |
| `ef` frame `p5` (`01` here, `02` in text mode) | `01` in all six scene captures; `02` in every text-mode frame | that it is a mode number (a guess) | selecting the wrong screen mode could leave the display in text mode |
| `ef` frame `p6` | equals `T` in all uploads; `00` and `08` for clock and spectrum (no upload) | its meaning (category? guess) | selects a scene kind; a wrong value may show another scene or a built-in one |
| `ef` frame `p7` (`21`, `1e`, `09`, `03`, `04`, `06`, `11`, and `00` for clock and spectrum) | differs per scene, also within a category (work `21` / `1e`, study `09` / `03`, cat `04` / `06`) | its meaning (item number? guess) | may address a specific stored scene; a wrong value may replace or select another scene |
| `ef` frame RGB (`33 66 99` in all six) | equals the text colour in force at the time; RESOLVED by upload attempt 4 and upload 5: the speaker does not apply it (the reply carries the colour in force, `ffffff`, and the picture was white although `33 66 99` was sent) | nothing open about this field now (its role in the frame is still unexplained) | replaying the captured frame does not change the text colour |
| `17` start payload, leading `00 00` | constant | meaning (upload type or slot?) | the speaker may refuse or misfile the upload |
| `17` total size | equals records x 133 (1064 = 8 x 133, 2128 = 16 x 133); counts the headers | what the speaker does with a wrong size | a mismatch may make it refuse, wait for more data, or keep a truncated scene |
| Chunk size 50 | announced by the speaker in its ready reply (`00 32`) | whether it varies by model or firmware | a real upload should read it from the ready reply and stop if it differs from 50 |
| Aborted upload (no end frame, a chunk missing or not acknowledged) | never happened in the captures | what state the speaker is left in | a partial or damaged scene, or a speaker waiting for the rest of the upload |
| Persistence and flash | the uploaded test image was still shown after about one minute without power (upload 3, user-confirmed); the speaker answered start and end frames after about 0.1-0.2 s and took 115-128 ms for 5 of the 6 image boundaries in capture 33, against 42-48 ms for ordinary chunks (a hint that it does something per image, guess: it stores something) | whether a scene is stored on the device, whether the upload writes flash, how often it may | repeated uploads might wear the flash; a stored scene may be replaced |

## Unknowns (open questions, 2026-10-25)

Things that are NOT known. Nothing below is a finding; where there is a hint
it is called a hint.

### 1. Is the `e8` text written to flash?

Unknown. It matters for timed refresh: if every `e8` is a flash write, a
program that updates the text every few seconds could wear the flash out, and
the safe refresh rate is far lower than if it only touches RAM.

- Hint that the device keeps the text itself: in capture 01 the first `e7`
  read-back, before any `e8` in that file, returned the text `lspss`. So some
  text survived across TempoHub sessions. That does not say RAM or flash, and
  where `lspss` came from is not known.
- Hint about timing: the `cc` reply to every `e8` arrived about 2 ms after the
  write. A flash write could take longer, but the device may well answer
  before writing, so this proves nothing.
- Power-cycle test (2026-10-25, see "Live length tests"): after sending
  `PWRTEST1` and cutting the speaker's power for at least 10 seconds (the
  user's figure), the user reported the screen was the same as before
  (`斷電前一樣`). So the text stays after a power cycle (confirmed by this live
  test). It does NOT prove that each `e8` writes flash, that the memory is
  flash rather than something else, or how many writes it survives; nor do we
  know if the speaker has a battery.
- Not established: whether the device debounces or delays writes, and whether
  writing the same text again does anything.
- Our code sent 12 `e8` frames and 2 colour frames on 2026-10-25, a few
  minutes apart or less, with no error, no odd reply and nothing odd on the
  screen. That does not test flash wear: a handful of writes cannot show a
  limit of thousands.
- Rule until this is known: treat each `e8` as a possible flash write: no
  loops, no refresh faster than a person would retype it. No timed refresh
  code exists yet (deliberately).
- The power-cycle test has been done (above): the text stayed. A test of the
  write count limit is not possible in a few frames and was not attempted.
- A later test would need commands we have not sent from our code (an `e7`
  read-back, a power cycle); it needs the user's approval first.

### 2. The meaning of `03` (and the other constant bytes)

- UPDATE after captures 10-17: `ef` has a first byte `sel` = `01`/`02`/`03`;
  `03` goes with the colour change, `02` with the main-mode click, `01` with a
  sub-option click (correlation confirmed, see "Captures 10-17"). Before that
  only `03` had been seen, so there was no evidence what it meant. Still
  unknown: what the device does if `sel` and the rest do not agree.
- Same situation for: the leading `00` of the `e8` payload (always `00`: A,
  AB, ABC, 你好, Hello), and the `00 00 ff ff ff` tail of the `ef` reply
  (identical in all four colour replies).
- To learn it: a capture of TempoHub changing another colour setting (e.g. a
  background or theme colour, if it has one), or sending other values, which
  is a risk we have not taken.

### 3. Maximum text length

- One 64-byte frame carries at most 55 bytes of text (report size limit). The
  count field is one byte, so the protocol might allow more through several
  frames, but no multi-frame transfer was ever seen.
- Verified on the device (live tests, 2026-10-25): 5, 6, 12, 24 and 55 UTF-8
  bytes are accepted and acknowledged, up to the one-frame maximum of 55. See
  "Live length tests" for what the screen showed in static centre and in
  scroll left to right. The CLI default `--max-bytes` is still 6; raising it
  is allowed up to 55 (hard limit).
- The `e7` read-back contains a constant byte `32` and a mode byte (`10`
  static, `20` scroll). The `32` (50 as a number) could be a limit, but
  nothing supports that: a 55-byte text was acknowledged.
- Static centre cut the 55-byte text after `V` (32 characters of ours); scroll
  showed all 55. Not known: the exact static cut-off rule (bytes, width or
  font), text longer than one frame, and TempoHub's own length limit (a
  capture of a long text typed in TempoHub would show it).

### 4. Display options in TempoHub (scroll/static + alignment)

The command bytes are now known: see "Captures 10-17". What remains open:

- Consequence for the length tests: what the screen shows for long text
  (whole, cut off, scrolling) depends on the device's current main mode and
  sub-option. A result such as "truncated" or "scrolled" describes the setting
  in force, not a protocol limit. The setting at test time must be recorded
  with each result (set it in TempoHub beforehand; our `e8` is not known to
  change it).
- Whether `e8` itself resets or changes the mode: in scroll left to right it
  did not (three `e8` frames, scrolling stayed left to right; live test of
  2026-10-25). Not checked for the other modes.
- Whether the device applies the RGB inside a `sel = 01/02` frame: not known.
- Not captured: the scroll speed, if TempoHub has such a setting.

### 5. Other open items

- `ee` status byte 5: `01` before the first `e8` of capture 02, `02` after it
  and in every later capture; meaning unknown (byte 6 = main mode and byte 7 =
  sub-option are explained in "Captures 10-17").
- The 44-byte `6a` table and the other device-info replies (`d8`, `65`, `66`,
  `d5`, `68`, `84`, `86`, `d7`, `f0`, `c6`).
- Images: scene uploads (`17`) were captured in 20-23, see "Scene uploads". Not
  known: whether uploads are stored on the device, whether they wear flash,
  and the meaning of the record header fields.
- Text and colour in one run (colour then text) was never captured from
  TempoHub; the CLI sends them as two separately verified frames.

## Earlier notes from another session (merged)

Added to the same branch by a separate session before captures existed. Kept
as written; the "Guess" below is now settled: the screen commands travel on
the vendor HID interface 4, collection 0xFF14 (see "Captures 02-06").

- USB VID:PID = 0x2D99:0xA106 (Windows Device Manager hardware ID, MI_00 = audio interface). Confirmed.
- USB composite device on root hub USBPcap1, port 10. Interfaces seen in USBPcapCMD: microphone,
  speaker, one HID consumer control, two vendor-defined HID. Confirmed.
- Guess: screen control uses one of the two vendor-defined HID interfaces. Not yet verified by capture.
- Raw captures taken on USBPcap1 include keyboard/mouse traffic: filter to the speaker's device
  address before saving anything into captures/.
