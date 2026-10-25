"""The upload builder must reproduce TempoHub's frames of captures 20, 21 and 23 byte for byte.

The data are hex text in tests/fixtures/captured_uploads.json (image bytes, record header
parameters, the ef frame, and every OUT / IN frame in capture order); no .pcap or image
file is read.

Run: python -m unittest discover -s tests -t .
"""
import ast
import json
import os
import unittest

from pixelbar import frames, upload

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "captured_uploads.json")
with open(FIXTURE, encoding="utf-8") as fh:
    CAPTURES = json.load(fh)


def build(key):
    c = CAPTURES[key]
    ef = bytes.fromhex(c["ef_payload"])  # 01 R G B 00 01 p6 p7 ff
    return upload.upload_sequence(bytes.fromhex(c["image_hex"]), c["type"], c["xx"], tuple(ef[1:4]), ef[7])


class UploadTests(unittest.TestCase):
    def test_out_frames_match_captures(self):
        for key, c in CAPTURES.items():
            with self.subTest(capture=c["capture"]):
                sequence = build(key)
                expected = [bytes.fromhex(h) for h in c["out"]]
                self.assertEqual(len(sequence), len(expected))
                for i, (got, want) in enumerate(zip(sequence, expected)):
                    self.assertEqual(got, want, f"frame {i}")

    def test_expected_replies_match_captures(self):
        for key, c in CAPTURES.items():
            with self.subTest(capture=c["capture"]):
                self.assertEqual(upload.expected_replies(build(key)), [bytes.fromhex(h) for h in c["in"]])

    def test_sequence_order_and_shape(self):
        sequence = build("20")
        self.assertEqual(sequence[0][3], frames.CMD_SET_COLOR)       # ef first, nothing after the end frame
        self.assertEqual(sequence[1][3:6], bytes([upload.CMD_UPLOAD, 0, 6]))
        self.assertEqual(sequence[1][6:12], bytes.fromhex("000000000428"))  # 1064 = 8 x 133
        self.assertEqual(sequence[-1][6:8], b"\x00\x04")
        self.assertEqual(sequence[-1][3], upload.CMD_UPLOAD)
        self.assertTrue(all(len(f) == 64 for f in sequence))

    def test_sizes(self):
        self.assertEqual(len(build("20")), 25)   # ef + start + 22 data + end
        self.assertEqual(len(build("21")), 46)   # two images
        self.assertEqual(len(build("23")), 25)

    def test_record_stream_layout(self):
        c = CAPTURES["21"]
        stream = upload.record_stream(bytes.fromhex(c["image_hex"]), c["type"], c["xx"])
        self.assertEqual(len(stream), 16 * 133)
        self.assertEqual(stream[:5].hex(), "0300000030")
        self.assertEqual(stream[133:138].hex(), "0300010030")

    def test_pixel_packing_round_trip(self):
        image = bytes.fromhex(CAPTURES["23"]["image_hex"])
        self.assertEqual(upload.pack_pixels(upload.unpack_pixels(image)), image)
        rows = upload.unpack_pixels(bytes([0x01] + [0] * (upload.IMAGE_BYTES - 1)))
        self.assertEqual(rows[0][0], 1)          # least significant bit is the top pixel
        self.assertEqual(sum(map(sum, rows)), 1)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            upload.record_stream(b"", 2, 100)
        with self.assertRaises(ValueError):
            upload.record_stream(bytes(1000), 2, 100)          # not a whole image
        with self.assertRaises(ValueError):
            upload.record_stream(bytes(1024), 256, 100)
        with self.assertRaises(ValueError):
            upload.pack_pixels([[0] * 256] * 31)

    def test_module_has_no_device_path(self):
        with open(upload.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported |= {a.name for a in node.names} | {(node.module or "").split(".")[0]}
        self.assertEqual(imported & {"hid", "device", "serial", "usb"}, set())
        called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertEqual(called & {"write", "open_path", "send_frame"}, set())

    def test_cli_refuses_without_dry_run(self):
        with self.assertRaises(SystemExit) as ctx:
            upload.main(["--image-hex", "00" * 1024, "--type", "2", "--xx", "100", "--rgb", "336699", "--item", "33"])
        self.assertIn("refused", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
