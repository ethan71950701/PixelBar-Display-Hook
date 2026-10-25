"""Frame builders for the HALO PixelBar HID protocol (see docs/protocol.md).

A frame is one 64-byte HID report on the 0xFF14 vendor collection:

    Report ID | tag  0xEC | cmd | len (2, BE) | payload | checksum | zero padding

Report ID 0x2E and tag 0xAA are host->device. The checksum is the sum of the
tag byte through the last payload byte, mod 256 (the Report ID is excluded).
This module only builds bytes; nothing here talks to the device.
"""

FRAME_SIZE = 64
REPORT_ID_OUT = 0x2E
TAG_OUT = 0xAA
SECOND_MAGIC = 0xEC

CMD_SET_TEXT = 0xE8
CMD_SET_COLOR = 0xEF

# report id + tag + magic + cmd + 2 length bytes + checksum
_OVERHEAD = 7
MAX_PAYLOAD = FRAME_SIZE - _OVERHEAD
# text payload = 00 + count + bytes; the longest captured text is 6 bytes, so
# anything beyond one frame is untested and rejected rather than guessed.
MAX_TEXT_BYTES = MAX_PAYLOAD - 2


def build_frame(cmd, payload=b"", report_id=REPORT_ID_OUT, tag=TAG_OUT):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload of {len(payload)} bytes does not fit one {FRAME_SIZE}-byte frame")
    body = bytes([tag, SECOND_MAGIC, cmd]) + len(payload).to_bytes(2, "big") + bytes(payload)
    body += bytes([sum(body) & 0xFF])
    return (bytes([report_id]) + body).ljust(FRAME_SIZE, b"\x00")


def text_frame(text):
    """cmd e8: `00`, UTF-8 byte count, UTF-8 bytes (confirmed with A, AB, ABC, 你好)."""
    data = text.encode("utf-8")
    if not data:
        raise ValueError("empty text was never captured")
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError(f"text is {len(data)} UTF-8 bytes; only one frame (max {MAX_TEXT_BYTES}) is supported")
    return build_frame(CMD_SET_TEXT, b"\x00" + bytes([len(data)]) + data)


def color_frame(r, g, b):
    """cmd ef: `03`, R, G, B (R G B order confirmed; the meaning of `03` is unknown)."""
    for v in (r, g, b):
        if not 0 <= v <= 255:
            raise ValueError("colour components must be 0..255")
    return build_frame(CMD_SET_COLOR, bytes([0x03, r, g, b]))


def parse_hex_color(value):
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError("colour must be 6 hex digits, e.g. ff0000")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
