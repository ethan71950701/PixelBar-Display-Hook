"""Write a 256x32 black/white test PNG for checking orientation and edges.

Layout: 1-pixel white border, an asymmetric "F" near the top-left, "TEST 123"
centred, and a 3x3 block near the bottom-right corner. Standard library only.

Usage: python tools/make_test_image.py OUT.png   (keep OUT.png outside the repo)
"""
import struct
import sys
import zlib

W, H = 256, 32

GLYPHS = {
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
}


def draw_glyph(img, ch, x0, y0, scale):
    for gy, row in enumerate(GLYPHS[ch]):
        for gx, bit in enumerate(row):
            if bit == "1":
                for dy in range(scale):
                    for dx in range(scale):
                        img[y0 + gy * scale + dy][x0 + gx * scale + dx] = 255


def build():
    img = [[0] * W for _ in range(H)]
    for x in range(W):
        img[0][x] = img[H - 1][x] = 255
    for y in range(H):
        img[y][0] = img[y][W - 1] = 255
    draw_glyph(img, "F", 6, 4, 2)
    text, scale = "TEST 123", 2
    step = 6 * scale
    x = (W - len(text) * step + scale) // 2
    for ch in text:
        if ch != " ":
            draw_glyph(img, ch, x, (H - 7 * scale) // 2, scale)
        x += step
    for dy in range(3):
        for dx in range(3):
            img[H - 6 + dy][W - 7 + dx] = 255
    return img


def write_png(path, img):
    raw = b"".join(b"\x00" + bytes(row) for row in img)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 0, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    image = build()
    write_png(sys.argv[1], image)
    for row in image:
        print("".join("#" if p else "." for p in row))
