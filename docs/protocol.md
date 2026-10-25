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

## Unknown fields in the scene upload, and the risk of changing them

"Risk" lines are guesses about what could go wrong; none was tested. The
builder takes these values as inputs; until their meaning is known, a real
upload should only repeat the captured values.

| Field | Confirmed | Not known | Risk if changed (guess) |
|---|---|---|---|
| Record header `T` (`02`, `03`, `04`) | same in every record of an upload; equals `ef` byte `p6` in captures 20, 21, 22, 23; different per scene | what it means (type, category, slot?) | the speaker may refuse the upload, or file the image under another scene, or overwrite another scene's data; we do not know whether the speaker keys scenes on it |
| Record header `XX XX` (`0064` in 20, `0030` in 21 and 22, `ffff` in 23) | same in every record of an upload; the same value for the same data (21, 22) | what it means; whether it is checked against the data | if it is something like a frame delay, a wrong value changes how a 2-image scene plays or hides it; if it is a check value, a wrong one may get the upload refused. Only three different values were seen, so a check value cannot be excluded (a guess that it is not one: they look like round numbers) |
| `ef` frame `sel` `01`, `p4` `00`, trailing `ff` | constant in captures 20-25 | meaning | the speaker may ignore or reject the frame |
| `ef` frame `p5` (`01` here, `02` in text mode) | `01` in all six scene captures; `02` in every text-mode frame | that it is a mode number (a guess) | selecting the wrong screen mode could leave the display in text mode |
| `ef` frame `p6` | equals `T` in all uploads; `00` and `08` for clock and spectrum (no upload) | its meaning (category? guess) | selects a scene kind; a wrong value may show another scene or a built-in one |
| `ef` frame `p7` (`21`, `09`, `04`, and `00` for clock and spectrum) | differs per scene | its meaning (item number? guess) | may address a specific stored scene; a wrong value may replace or select another scene |
| `ef` frame RGB (`33 66 99` in all six) | equals the text colour in force at the time | whether the speaker applies the RGB of this frame | if it does, a different RGB would recolour the text; if our current colour differs from the captured one, replaying the captured frame could change the text colour |
| `17` start payload, leading `00 00` | constant | meaning (upload type or slot?) | the speaker may refuse or misfile the upload |
| `17` total size | equals records x 133 (1064 = 8 x 133, 2128 = 16 x 133); counts the headers | what the speaker does with a wrong size | a mismatch may make it refuse, wait for more data, or keep a truncated scene |
| Chunk size 50 | announced by the speaker in its ready reply (`00 32`) | whether it varies by model or firmware | a real upload should read it from the ready reply and stop if it differs from 50 |
| Aborted upload (no end frame, a chunk missing or not acknowledged) | never happened in the captures | what state the speaker is left in | a partial or damaged scene, or a speaker waiting for the rest of the upload |
| Persistence and flash | the speaker answered start and end frames after about 0.1-0.2 s (guess: it stores something) | whether a scene is stored on the device, whether the upload writes flash, how often it may | repeated uploads might wear the flash; a stored scene may be replaced |

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
