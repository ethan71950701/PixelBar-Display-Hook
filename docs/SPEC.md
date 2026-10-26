# Edifier HALO PixelBar: what is known about its USB protocol

This is the short version: only what was **confirmed**, each with where it came from. Guesses and unknowns are in
their own sections at the end. The evidence, in the order it was gathered (and with the early guesses that were later
corrected), is in [protocol.md](protocol.md); section names are given in *italics*.

How things were confirmed: **capture** = seen in a USB capture of the official app (EDIFIER TempoHub); **live** = sent
from this repository's code to a real speaker and the owner looked at the result; **measured** = measured with a phone
video of the screen. All of it comes from **one speaker** (VID:PID `2d99:a106`, described by its owner as the
"enhanced" edition; the firmware version was not recorded). Nothing is known about other units.

## 1. Transport (capture)

- USB composite device, VID `0x2D99`, PID `0xA106`, manufacturer string "Jieli Technology" (the chip maker).
- The screen is driven over **USB HID**, interface 4, the vendor collection with **usage page `0xFF14`**. The other vendor
  collection (`0xFF13`) and the consumer-control interface (volume keys) were never used.
- Output reports (host to speaker) use **Report ID `0x2E`**, input reports **Report ID `0x2F`**; every report is **64 bytes**
  (1 byte report ID + 63 bytes of data) on interrupt endpoints. *Device identification; HID report descriptor of interface 4.*
- The speaker must be in USB sound-card input mode, and the official app must be closed (two programs fighting for the
  device is not tested and not recommended).

## 2. Frame format (capture, live)

    byte 0     report ID        2e (host to speaker) or 2f (speaker to host)
    byte 1     tag              aa (host to speaker); bb (speaker to host); cc (the reply to e8 only)
    byte 2     ec               constant
    byte 3     command
    byte 4-5   payload length   big endian
    byte 6..   payload          at most 57 bytes
    next byte  checksum         sum of bytes 1 .. last payload byte, modulo 256
    rest       zero padding up to 64 bytes

All frames in all captures check out; the code builds them byte-exact (`pixelbar/frames.py`, `tests/`).

## 3. Commands

| Cmd | Direction | Payload | Meaning | Status |
|---|---|---|---|---|
| `e8` | to speaker | `00 <n> <UTF-8 text>`, `n` = number of **bytes** | set the screen text; reply (tag `cc`) payload `01` | capture, live |
| `e7` | to speaker, payload `00` | | read the text back; reply `00 XX 32 <n> <text>`, `XX` = `10` static / `20` scroll | capture |
| `ef` | to speaker | `03 R G B` | set the text colour (RGB); the speaker echoes it | capture, live |
| `ef` | to speaker | `sel R G B 00 02 major sub ff` (9 bytes) | `sel` `02`: set the main mode; `sel` `01`: set the sub-option. `major` `00` static, `01` scroll. `sub`: scroll `00` left to right, `01` right to left; static `00` left, `01` centre, `02` right, `03` justified | capture (the code does not send it yet) |
| `ee` | speaker to host | `00 R G B 00 02 major sub ff 01 00 00` | status (colour, mode, sub-option) | capture |
| `17` | both | see section 5 | upload of a scene (pictures) | capture, live |
| `77` | to speaker | `year(2) month day hour minute second 00 01` | clock; answered with `01` | capture (never sent by this code) |
| `c9` | speaker to host | UTF-8 `花再 Halo PixelBar` | device name | capture |

Other commands the app sends at start-up (`65 66 68 6a 84 86 87 c6 c8 d5 d7 d8 f0`) were only seen; their replies have been
documented but what they mean is **unknown** (section 8). *Known command table.*

### Text (live)

- UTF-8, one frame holds at most **55 bytes** of text. A 5, 12, 24 and 55 byte text was acknowledged; **static** mode cut the 55-byte
  text after the first characters, **scroll** mode showed all of it. The command line limits text to 6 bytes by default
  (the longest it was first tried with); raise `--max-bytes` step by step. Only characters from the tested allow list should
  be sent. *Live length tests; Character display tests.*
- Text survives a power-off (user report, a text sent and the speaker unplugged for 10 s or more).

### Text colour (live)

- `ef 03 R G B`: the speaker takes the colour (RGB order, confirmed with red, green and a blue-grey) and the text turns that
  colour. **Pictures are drawn in the current text colour** (section 5), so the colour is how a picture gets its colour.

## 4. The screen

- 256 x 32 pixels, one bit per pixel, one colour at a time (the text colour). Pixel byte layout in section 5. (live)
- While a picture upload is running the speaker shows **「图片上传中」** with one to three moving dots; after an upload that
  was cut off, about a minute later it shows **「图片传输失败，请重新上传」** ("picture transfer failed, please upload again").
  Selecting any official scene in TempoHub restores a normal screen. (seen in a phone recording)

## 5. Scene (picture) upload, command `17` (capture 20-33, live)

One upload replaces the scene that the official app calls "兼职成年人" (the scene slot of capture 20) until another official scene
is selected in TempoHub. Order of frames, host to speaker, with the speaker's replies:

1. **Scene select**: `ef` payload `01 33 66 99 00 01 02 21 ff`. The speaker echoes it, but with the **colour it currently has**
   in bytes 1-3 (the colour bytes sent are not applied). Nothing else precedes the data.
2. **Start**: `17` payload `00 00 <total>` (`total` = 4 bytes, big endian, = number of records x 133). Reply `00 01 00 32`
   ("ready", chunk size **50**).
3. **Data**: chunks of at most **50 bytes** of the record stream, each as `17` payload `00 02 <seq (2 bytes, big endian)> <data>`,
   `seq` counting from 0. Each chunk is acknowledged with `00 03 <seq>` **except the last one**. The speaker sets the pace
   (about 46 ms per chunk, a little more at picture boundaries). Chunk numbers above 255 occur (up to 425 were used), so `seq`
   is two bytes.
4. **End**: `17` payload `00 04`; reply `00 05`.

### The record stream

A sequence of **records of 133 bytes**: a 5-byte header `T 00 NN XX XX` and 128 bytes of picture data.

- `T`: one byte, the same in every record; `0x02` in all our uploads (it equals byte 7 of the scene-select payload; in the captures it
  differs by scene category).
- `00 NN`: the record number counted from 0, no gaps (whether it is one byte or two cannot be told: the largest used was 159).
- `XX XX`: two bytes, the same in every record; **sets how long the speaker shows each picture** (section 6).
- A **picture** is 8 consecutive records = 1024 bytes of data: 4 pages of 256 bytes; page `p` holds the screen rows `8p` to
  `8p+7`; within a page, byte `x` is the column `x`; bit 0 is the **top** row of the page ("LSB on top"); a set bit is a lit pixel.
  No compression, no encryption, no checksum besides the frame checksum. (confirmed on the screen with a test image)
- Several pictures in one upload are simply concatenated; the speaker plays them in order and loops. **1, 2, 7 and 20 pictures** were
  uploaded (7448 bytes was the largest TempoHub upload; 21,280 bytes, 20 pictures, 426 chunks, 429 frames, took about 23.3 s).
  The upper limit of the scene slot is **not known**.

## 6. Time per picture (measured)

All pictures of a scene are shown for the same time, whatever their number, then the scene loops. The time is set by `XX XX`
(same pictures, only this field changed):

| `XX XX` | time per picture |
|---|---|
| `0x0018` (24) | 0.040 s |
| `0x0030` (48) | 0.090 s |
| `0x0064` (100, the captured value) | 0.2025 s |
| `0x012C` (300) | 0.600 s |
| `0x0190` (400) | 0.8005 s |
| `0x01F4` (500) | 1.000 s |

From 100 upwards it is **2 ms x `XX XX`** within about 1%; below 100 the speaker is faster than that. Anything outside
`0x0030`-`0x01F4` was not tried, and `0xFFFF` (used by the official one-picture scenes) is never sent. *Several pictures in one
upload; Playback speed measured from phone videos.*

## 7. Frames the speaker sends by itself (capture, live)

Seen between the replies: `ee` (after text is set), `66` (payload `10 0b` or `10 08`) and `77` with payload `02` (the frame
`2f bb ec 77 00 01 02 21`). The `77 02` frame was seen four times in the middle of uploads, at irregular times; it does not need
an answer for the upload to go on. The code ignores exactly these frames. Why the speaker sends them is **unknown**.

## 8. Not known (and not tested)

- Any other speaker, edition or firmware (section 1).
- How many times the storage can be rewritten safely. Text and pictures survive a power-off, so it is stored; wear per upload
  and per day is not known (this is why the code keeps an upload budget).
- The meaning of `T`, the scene-select bytes `p5`, `p6`, `p7` and the leading `00 00` of the start frame (always the captured values).
- The maximum number of pictures in one upload, and whether `00 NN` is one or two bytes (index 255 or more was never used).
- The meaning of the start-up queries (`65 66 68 6a 84 86 87 c6 c8 d5 d7 d8 f0`). The `c8` reply looks like an address, `d8` like a
  table of capabilities; both are guesses. Whether any of them reports a capacity: no evidence.
- What the unprompted `77 02` means (a request for the time is a guess).
- Whether the official app's own "animation" scenes use other values of the fields above.
