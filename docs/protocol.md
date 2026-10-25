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
- Not established: whether the text survives a speaker power cycle, and
  whether the device debounces or delays writes.
- Rule until this is known: treat each `e8` as a possible flash write: no
  loops, no refresh faster than a person would retype it. No timed refresh
  code exists yet (deliberately).
- A later test would need commands we have not sent from our code (an `e7`
  read-back, a power cycle); it needs the user's approval first.

### 2. The meaning of `03` (and the other constant bytes)

- `ef` payload = `03` + R G B. Only `03` has ever been seen: capture 05, 08,
  09 (TempoHub) and our own send. No sample ever differed, so there is no
  evidence what it means. Guesses (zone, slot, mode) have no support.
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
- Verified on the device: 6 UTF-8 bytes (`你好`) and 5 (`Hello`). Anything
  longer is untested. The CLI refuses text over `--max-bytes` (default 6) and
  always refuses more than 55 bytes.
- The `e7` read-back contains two constant bytes, `10 32` (16 and 50 as
  numbers), in every reply. They could be limits, but nothing supports that.
- Not known: what the screen does with long text (scroll, truncate, or
  reject), and TempoHub's own length limit (a capture of a long text typed in
  TempoHub would show it).

### 4. Other open items

- `ee` status reply bytes 5 and 7 (`01/00` -> `02/01` after the first `e8`).
- The 44-byte `6a` table and the other device-info replies (`d8`, `65`, `66`,
  `d5`, `68`, `84`, `86`, `d7`, `f0`, `c6`).
- Images: no image command has been captured at all.
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
