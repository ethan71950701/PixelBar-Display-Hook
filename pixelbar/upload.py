"""Build the scene-upload frame sequence (cmd 0x17). Dry run only.

This module and its command line only build and print bytes. It does not open
or write any device and does not import hidapi (or pixelbar.device); anything
without --dry-run is refused. The format is the one seen in captures 20, 21 and
23 (docs/protocol.md, "Scene uploads"):

    ef frame      scene select, sent first (the captures show none after the upload)
    17 start      00 00 + u32 total size (the size counts the 5-byte record headers)
    17 data       00 02 + u16 sequence + up to 50 bytes of the record stream
    17 end        00 04

The record stream is 133-byte records: header `T 00 NN XX XX` (T and XX XX
are copied by the caller; their meaning is unknown) + 128 bytes of image. An
image is 256x32, 1 bit per pixel, 1024 bytes: 4 pages of 256 bytes, one byte =
8 vertical pixels, least significant bit on top.
"""
import argparse
import json
import os
import sys

from . import frames

CMD_UPLOAD = 0x17

WIDTH, HEIGHT = 256, 32
IMAGE_BYTES = WIDTH * HEIGHT // 8
RECORD_DATA = 128
RECORD_HEADER = 5
CHUNK_BYTES = 50  # the speaker announces 00 32 in its ready reply

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "captured_uploads.json")


def pack_pixels(rows):
    """32 rows of 256 values (0 or 1) -> 1024 bytes, vertical bytes, LSB on top."""
    if len(rows) != HEIGHT or any(len(r) != WIDTH for r in rows):
        raise ValueError(f"an image is {WIDTH} columns x {HEIGHT} rows")
    out = bytearray(IMAGE_BYTES)
    for page in range(HEIGHT // 8):
        for col in range(WIDTH):
            out[page * WIDTH + col] = sum((1 if rows[page * 8 + bit][col] else 0) << bit for bit in range(8))
    return bytes(out)


def unpack_pixels(image):
    rows = [[0] * WIDTH for _ in range(HEIGHT)]
    for page in range(HEIGHT // 8):
        for col in range(WIDTH):
            for bit in range(8):
                rows[page * 8 + bit][col] = (image[page * WIDTH + col] >> bit) & 1
    return rows


def pixel_location(x, y):
    """(byte index, bit) of the pixel (x, y) in a 256x32 image: vertical bytes, least significant bit on top."""
    if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
        raise ValueError(f"pixel ({x}, {y}) is outside the {WIDTH}x{HEIGHT} image")
    return (y // 8) * WIDTH + x, y % 8


def set_pixel(image_data, x, y, on=True):
    """Return a copy of the image data with the pixel (x, y) of the first image switched on or off."""
    index, bit = pixel_location(x, y)
    data = bytearray(image_data)
    data[index] = data[index] | (1 << bit) if on else data[index] & ~(1 << bit)
    return bytes(data)


def record_stream(image_data, record_type, xx):
    """Cut the image data into 128-byte pieces and give each its 5-byte header."""
    if not image_data or len(image_data) % IMAGE_BYTES:
        raise ValueError(f"image data must be a whole number of {IMAGE_BYTES}-byte images")
    if not 0 <= record_type <= 0xFF or not 0 <= xx <= 0xFFFF:
        raise ValueError("record type is one byte, XX XX two bytes")
    count = len(image_data) // RECORD_DATA
    if count > 0xFFFF:
        raise ValueError("too many records")
    stream = bytearray()
    for i in range(count):
        stream += bytes([record_type]) + i.to_bytes(2, "big") + xx.to_bytes(2, "big")
        stream += image_data[i * RECORD_DATA:(i + 1) * RECORD_DATA]
    return bytes(stream)


def upload_frames(image_data, record_type, xx):
    """Start frame, data frames, end frame (OUT frames only, 64 bytes each)."""
    stream = record_stream(image_data, record_type, xx)
    out = [frames.build_frame(CMD_UPLOAD, b"\x00\x00" + len(stream).to_bytes(4, "big"))]
    for seq, start in enumerate(range(0, len(stream), CHUNK_BYTES)):
        out.append(frames.build_frame(CMD_UPLOAD, b"\x00\x02" + seq.to_bytes(2, "big") + stream[start:start + CHUNK_BYTES]))
    out.append(frames.build_frame(CMD_UPLOAD, b"\x00\x04"))
    return out


def scene_select_frame(rgb, p6, p7):
    """ef frame seen before every upload: 01 R G B 00 01 p6 p7 ff.

    p6 equalled the record header byte T in all three uploads; p7 is unknown.
    The R G B bytes carried the text colour in force in the captures.
    """
    return frames.build_frame(frames.CMD_SET_COLOR, bytes([0x01, *rgb, 0x00, 0x01, p6, p7, 0xFF]))


def upload_sequence(image_data, record_type, xx, rgb, p7):
    """The OUT frames of one upload in capture order: ef first, then 17 start/data/end.

    No ef frame follows the upload: none was seen after it in the captures.
    """
    return [scene_select_frame(rgb, record_type, p7)] + upload_frames(image_data, record_type, xx)


def expected_replies(out_frames):
    """Replies TempoHub got, in the order seen: the ef echo, ready, an ack per chunk but the last, done."""
    def reply(cmd, payload):
        return frames.build_frame(cmd, payload, report_id=frames.REPORT_ID_IN, tag=frames.TAG_REPLY)

    ef, start, *data_and_end = out_frames
    chunks = data_and_end[:-1]
    replies = [reply(frames.CMD_SET_COLOR, ef[6:15]), reply(CMD_UPLOAD, b"\x00\x01\x00" + bytes([CHUNK_BYTES]))]
    replies += [reply(CMD_UPLOAD, b"\x00\x03" + seq.to_bytes(2, "big")) for seq in range(len(chunks) - 1)]
    replies.append(reply(CMD_UPLOAD, b"\x00\x05"))
    return replies


def captured_matches(sequence):
    try:
        with open(FIXTURE, encoding="utf-8") as fh:
            known = json.load(fh)
    except OSError:
        return []
    return [v["capture"] for v in known.values() if [bytes.fromhex(h) for h in v["out"]] == sequence]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pixelbar.upload", description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="required: print what would be sent; nothing is sent")
    ap.add_argument("--image-hex", required=True, help="image data as hex text (or @file with the hex text), 1024 bytes per image")
    ap.add_argument("--type", type=lambda s: int(s, 0), required=True, help="record header byte T (meaning unknown)")
    ap.add_argument("--xx", type=lambda s: int(s, 0), required=True, help="record header XX XX, e.g. 0x0064 (meaning unknown)")
    ap.add_argument("--rgb", required=True, metavar="RRGGBB", help="colour bytes of the ef frame (the text colour in force)")
    ap.add_argument("--item", type=lambda s: int(s, 0), required=True, help="ef frame byte p7 (meaning unknown)")
    ap.add_argument("--show-all", action="store_true", help="print every frame")
    args = ap.parse_args(argv)
    if not args.dry_run:
        sys.exit("refused: sending is not implemented; use --dry-run")
    try:
        text = args.image_hex
        if text.startswith("@"):
            with open(text[1:], encoding="utf-8") as fh:
                text = fh.read()
        sequence = upload_sequence(bytes.fromhex("".join(text.split())), args.type, args.xx, frames.parse_hex_color(args.rgb), args.item)
    except (ValueError, OSError) as err:
        sys.exit(f"error: {err}")
    print(f"{len(sequence)} OUT frames: 1 ef, 1 start, {len(sequence) - 3} data, 1 end")
    hits = captured_matches(sequence)
    print("identical to the TempoHub frames of capture: " + (", ".join(hits) if hits else "none (not a captured upload)"))
    for frame in (sequence if args.show_all else sequence[:2] + sequence[-1:]):
        used = frame.rstrip(b"\x00").hex()
        print(f"  {used}")
    print("dry run: nothing was sent.")


if __name__ == "__main__":
    main()
