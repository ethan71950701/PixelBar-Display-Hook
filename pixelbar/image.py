"""PNG (256x32, black background, white pixels) -> 1024-byte bitmap for a scene upload.

Pure Python, no third-party packages, no device access. The size must be exactly
256x32: nothing is scaled or cropped. A pixel is lit when its brightness is at
or above the threshold (default 128); transparent pixels count as black.

Byte layout (confirmed on the screen for capture 20's text): 4 pages of 256
bytes; one byte is the 8 pixels of one column in one page; the least significant
bit is the topmost pixel. `bit_order="msb-top"` is the other possibility and
only an option, not an assumption the project has confirmed.
"""
import struct
import zlib

WIDTH, HEIGHT = 256, 32
BIT_ORDERS = ("lsb-top", "msb-top")
DEFAULT_THRESHOLD = 128
_SAMPLES = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}   # PNG colour type -> samples per pixel
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunks(data):
    if data[:8] != _SIGNATURE:
        raise ValueError("not a PNG file")
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body, crc = data[pos + 8:pos + 8 + length], data[pos + 8 + length:pos + 12 + length]
        if len(body) != length or len(crc) != 4 or struct.unpack(">I", crc)[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise ValueError("damaged PNG (chunk checksum or length)")
        yield kind, body
        pos += 12 + length
        if kind == b"IEND":
            return
    raise ValueError("damaged PNG (no IEND chunk)")


def _unfilter(raw, width, height, bits_per_pixel):
    stride = (width * bits_per_pixel + 7) // 8
    bpp = max(1, bits_per_pixel // 8)
    if len(raw) != height * (stride + 1):
        raise ValueError("damaged PNG (image data has the wrong length)")
    rows, prev = [], bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if kind == 1:
                line[i] = (line[i] + a) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + b) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            elif kind != 0:
                raise ValueError(f"damaged PNG (filter type {kind})")
        rows.append(bytes(line))
        prev = line
    return rows


def decode_png(data):
    """Return (width, height, brightness rows): brightness 0-255 per pixel, on a black background."""
    chunks = list(_chunks(data))
    if not chunks or chunks[0][0] != b"IHDR":
        raise ValueError("damaged PNG (no IHDR first)")
    width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if interlace:
        raise ValueError("interlaced PNG files are not supported")
    if color not in _SAMPLES or depth not in (1, 2, 4, 8, 16) or (color in (2, 4, 6) and depth < 8) or (color == 3 and depth == 16):
        raise ValueError(f"unsupported PNG format (colour type {color}, depth {depth})")
    palette = next((b for k, b in chunks if k == b"PLTE"), b"")
    raw = zlib.decompress(b"".join(b for k, b in chunks if k == b"IDAT"))
    samples = _SAMPLES[color]
    lines = _unfilter(raw, width, height, samples * depth)
    scale = 255 // ((1 << min(depth, 8)) - 1)
    rows = []
    for line in lines:
        if depth < 8:
            per_byte = 8 // depth
            values = [(line[i // per_byte] >> (8 - depth * (i % per_byte + 1))) & ((1 << depth) - 1) for i in range(width * samples)]
        elif depth == 8:
            values = list(line[:width * samples])
        else:
            values = [line[2 * i] for i in range(width * samples)]   # high byte of each 16-bit sample
        row = []
        for x in range(width):
            px = values[x * samples:(x + 1) * samples]
            if color == 0:
                lum, alpha = px[0] * scale, 255
            elif color == 4:
                lum, alpha = px[0] * scale, px[1]
            elif color == 3:
                if px[0] * 3 + 2 >= len(palette):
                    raise ValueError("damaged PNG (palette index out of range)")
                r, g, b = palette[px[0] * 3:px[0] * 3 + 3]
                lum, alpha = 0.299 * r + 0.587 * g + 0.114 * b, 255
            else:
                lum, alpha = 0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2], (px[3] if color == 6 else 255)
            row.append(lum * alpha / 255)
        rows.append(row)
    return width, height, rows


def encode_png_gray(rows):
    """8-bit grayscale PNG bytes from rows of 0-255 values (used by tests and examples)."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(int(v) for v in r) for r in rows)
    ihdr = struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 0, 0, 0, 0)
    return _SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def png_to_pixels(data, threshold=DEFAULT_THRESHOLD):
    """Rows of 0/1 (1 = lit) from a 256x32 PNG; any other size is an error."""
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be 0..255")
    width, height, brightness = decode_png(data)
    if (width, height) != (WIDTH, HEIGHT):
        raise ValueError(f"the PNG is {width}x{height}; it must be exactly {WIDTH}x{HEIGHT} (nothing is scaled or cropped)")
    return [[1 if v >= threshold else 0 for v in row] for row in brightness]


def pack_pixels(rows, bit_order="lsb-top"):
    """Rows of 0/1 -> 1024 bytes: vertical bytes in 4 pages of 256, bit order as given."""
    if bit_order not in BIT_ORDERS:
        raise ValueError(f"bit order must be one of {BIT_ORDERS}")
    if len(rows) != HEIGHT or any(len(r) != WIDTH for r in rows):
        raise ValueError(f"an image is {WIDTH} columns x {HEIGHT} rows")
    out = bytearray(WIDTH * HEIGHT // 8)
    for page in range(HEIGHT // 8):
        for col in range(WIDTH):
            value = 0
            for k in range(8):
                if rows[page * 8 + k][col]:
                    value |= (1 << k) if bit_order == "lsb-top" else (0x80 >> k)
            out[page * WIDTH + col] = value
    return bytes(out)


def sha_matches(bitmap, prefix, minimum=12):
    """True if the SHA-256 of the bitmap starts with `prefix` (at least `minimum` hex digits)."""
    import hashlib
    prefix = prefix.strip().lower()
    return len(prefix) >= minimum and all(c in "0123456789abcdef" for c in prefix) and hashlib.sha256(bitmap).hexdigest().startswith(prefix)


def png_to_bitmap(data, threshold=DEFAULT_THRESHOLD, bit_order="lsb-top"):
    return pack_pixels(png_to_pixels(data, threshold), bit_order)


def preview(rows):
    """Text picture of the 256x32 image: '#' lit, '.' dark, with a column ruler and row numbers."""
    ruler = "    " + "".join(str((x // 10) % 10) if x % 10 == 0 else " " for x in range(WIDTH))
    lines = [ruler]
    lines += [f"{y:2d}  " + "".join("#" if v else "." for v in row) for y, row in enumerate(rows)]
    return "\n".join(lines)
