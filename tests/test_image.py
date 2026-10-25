"""PNG -> bitmap conversion and the upload plan for a whole image. PNG data are built in memory; no file is read.

Run: python -m unittest discover -s tests -t .
"""
import json
import struct
import unittest
import zlib

from pixelbar import image, upload, upload_replay


def make_png(width, height, color, depth, rows, palette=b"", interlace=0, corrupt=False):
    """Rows are already-packed scanline bytes (no filter byte)."""
    def chunk(kind, body):
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc ^ 1 if corrupt else crc)

    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))
    if palette:
        out += chunk(b"PLTE", palette)
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def gray_png(value_at):
    rows = [[value_at(x, y) for x in range(256)] for y in range(32)]
    return image.encode_png_gray(rows), rows


class DecodeTests(unittest.TestCase):
    def test_gray_round_trip_and_threshold(self):
        data, rows = gray_png(lambda x, y: 255 if (x, y) == (3, 4) else 128 if (x, y) == (5, 6) else 127 if (x, y) == (7, 8) else 0)
        lit = image.png_to_pixels(data)                       # default threshold 128: 128 is lit, 127 is not
        self.assertEqual(sum(map(sum, lit)), 2)
        self.assertEqual((lit[4][3], lit[6][5], lit[8][7]), (1, 1, 0))
        self.assertEqual(sum(map(sum, image.png_to_pixels(data, 200))), 1)
        self.assertEqual(sum(map(sum, image.png_to_pixels(data, 1))), 3)
        with self.assertRaises(ValueError):
            image.png_to_pixels(data, 256)

    def test_other_sizes_are_errors_not_scaled(self):
        for w, h in [(255, 32), (256, 31), (512, 64), (128, 16)]:
            data = image.encode_png_gray([[255] * w for _ in range(h)])
            with self.assertRaises(ValueError) as ctx:
                image.png_to_pixels(data)
            self.assertIn("256x32", str(ctx.exception))
            self.assertIn(f"{w}x{h}", str(ctx.exception))

    def test_rgb_rgba_palette_and_one_bit(self):
        white_row = bytes([255, 255, 255]) * 256
        black_row = bytes(3 * 256)
        data = make_png(256, 32, 2, 8, [white_row] + [black_row] * 31)
        self.assertEqual(sum(image.png_to_pixels(data)[0]), 256)
        self.assertEqual(sum(image.png_to_pixels(data)[1]), 0)
        # RGBA: white but fully transparent counts as black; opaque white is lit
        px = bytes([255, 255, 255, 0]) * 128 + bytes([255, 255, 255, 255]) * 128
        data = make_png(256, 32, 6, 8, [px] + [bytes(4 * 256)] * 31)
        self.assertEqual(image.png_to_pixels(data)[0][:128], [0] * 128)
        self.assertEqual(image.png_to_pixels(data)[0][128:], [1] * 128)
        # palette: index 1 = white
        data = make_png(256, 32, 3, 8, [bytes([1]) * 256] + [bytes(256)] * 31, palette=bytes([0, 0, 0, 255, 255, 255]))
        self.assertEqual(sum(image.png_to_pixels(data)[0]), 256)
        # 1-bit gray: first 8 pixels white
        data = make_png(256, 32, 0, 1, [bytes([0xFF]) + bytes(31)] + [bytes(32)] * 31)
        self.assertEqual(image.png_to_pixels(data)[0][:10], [1] * 8 + [0, 0])
        # 16-bit gray uses the high byte
        data = make_png(256, 32, 0, 16, [bytes([0x80, 0x00]) * 256] + [bytes(512)] * 31)
        self.assertEqual(sum(image.png_to_pixels(data)[0]), 256)

    def test_rejects_bad_files(self):
        with self.assertRaises(ValueError):
            image.png_to_pixels(b"not a png")
        good = make_png(256, 32, 0, 8, [bytes(256)] * 32)
        with self.assertRaises(ValueError):
            image.png_to_pixels(make_png(256, 32, 0, 8, [bytes(256)] * 32, corrupt=True))
        with self.assertRaises(ValueError):
            image.png_to_pixels(make_png(256, 32, 0, 8, [bytes(256)] * 32, interlace=1))
        with self.assertRaises(ValueError):
            image.png_to_pixels(good[:-20])


class PackTests(unittest.TestCase):
    def one_pixel(self, x, y):
        rows = [[0] * 256 for _ in range(32)]
        rows[y][x] = 1
        return rows

    def test_bit_orders(self):
        for (x, y), lsb, msb, index in [((0, 0), 0x01, 0x80, 0), ((0, 7), 0x80, 0x01, 0), ((5, 8), 0x01, 0x80, 256 + 5),
                                       ((255, 31), 0x80, 0x01, 3 * 256 + 255), ((128, 4), 0x10, 0x08, 128)]:
            with self.subTest(pixel=(x, y)):
                a = image.pack_pixels(self.one_pixel(x, y), "lsb-top")
                b = image.pack_pixels(self.one_pixel(x, y), "msb-top")
                self.assertEqual((a[index], b[index]), (lsb, msb))
                self.assertEqual(sum(1 for v in a if v), 1)
                self.assertEqual(sum(1 for v in b if v), 1)

    def test_default_matches_the_builder_layout(self):
        rows = [[(x * 7 + y * 3) % 5 == 0 for x in range(256)] for y in range(32)]
        self.assertEqual(image.pack_pixels(rows), upload.pack_pixels(rows))
        self.assertEqual(upload.unpack_pixels(image.pack_pixels(rows)), [[int(v) for v in r] for r in rows])
        with self.assertRaises(ValueError):
            image.pack_pixels(rows, "diagonal")

    def test_sha_prefix_binding(self):
        import hashlib
        bitmap = image.pack_pixels(self.one_pixel(9, 9))
        digest = hashlib.sha256(bitmap).hexdigest()
        self.assertTrue(image.sha_matches(bitmap, digest[:12]))
        self.assertTrue(image.sha_matches(bitmap, digest[:16].upper()))
        self.assertFalse(image.sha_matches(bitmap, digest[:11]))            # too short to bind
        self.assertFalse(image.sha_matches(bitmap, "0" * 12))
        self.assertFalse(image.sha_matches(bitmap, "zz" + digest[2:14]))
        other = image.pack_pixels(self.one_pixel(9, 10))
        self.assertFalse(image.sha_matches(other, digest[:12]))             # another image does not match

    def test_preview_shape(self):
        text = image.preview(self.one_pixel(2, 1)).split("\n")
        self.assertEqual(len(text), 33)
        self.assertTrue(all(len(line) == 4 + 256 for line in text))
        self.assertEqual(text[2][4:7], "..#")


class PlanForAnImageTests(unittest.TestCase):
    def test_captured_image_gives_the_captured_frames(self):
        with open(upload_replay.FIXTURE, encoding="utf-8") as fh:
            captured = bytes.fromhex(json.load(fh)["20"]["image_hex"])
        steps, checks = upload_replay.load_plan(image=captured)
        self.assertTrue(all(checks.values()), checks)
        reference, _ = upload_replay.load_plan()
        self.assertEqual([s[1] for s in steps], [s[1] for s in reference])

    def test_other_image_changes_only_data_frames(self):
        rows = [[1 if x in (0, 255) or y in (0, 31) else 0 for x in range(256)] for y in range(32)]   # a one-pixel frame
        bitmap = image.pack_pixels(rows)
        steps, checks = upload_replay.load_plan(image=bitmap)
        for name, ok in checks.items():
            self.assertTrue(ok, name)
        reference, _ = upload_replay.load_plan()
        diffs = upload_replay.frame_differences(steps, [s[1] for s in reference])
        self.assertTrue(diffs)
        self.assertTrue(all(2 <= i <= 23 for i in diffs))           # only data frames, never ef, start or end
        for i in (0, 1, 24):
            self.assertEqual(steps[i][1], reference[i][1])
        self.assertEqual([s[2] for s in steps], [s[2] for s in reference])   # same expected replies

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            upload_replay.load_plan(image=bytes(1000))
        with self.assertRaises(ValueError):
            upload_replay.load_plan(image=bytes(1024), pixel=(128, 4))


if __name__ == "__main__":
    unittest.main()
