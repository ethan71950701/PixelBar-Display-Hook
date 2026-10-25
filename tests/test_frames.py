"""Built frames must equal the bytes TempoHub sent (tests/fixtures/captured_frames.json).

Run: python -m unittest discover -s tests -t .
"""
import json
import os
import unittest

from pixelbar import frames

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "captured_frames.json")


def captured(capture):
    with open(FIXTURE, encoding="utf-8") as fh:
        return [bytes.fromhex(k["hex"]) for k in json.load(fh) if k["capture"] == capture]


class FrameTests(unittest.TestCase):
    def test_text_frames_match_captures(self):
        for text, capture in [("A", "usb-02-text-A"), ("AB", "usb-03-text-AB"),
                              ("ABC", "usb-04-text-ABC"), ("你好", "usb-07-text-nihao")]:
            with self.subTest(text=text):
                self.assertIn(frames.text_frame(text), captured(capture))

    def test_color_frames_match_captures(self):
        # 05: bytes seen, hex not recorded; 08: #330200; 09: device received 00 33 07
        for rgb, capture in [((0x27, 0x99, 0xFF), "usb-05-color"),
                             ((0x33, 0x02, 0x00), "usb-08-color-red"),
                             ((0x00, 0x33, 0x07), "usb-09-color-green")]:
            with self.subTest(rgb=rgb):
                self.assertIn(frames.color_frame(*rgb), captured(capture))

    def test_expected_replies_match_what_the_device_sent(self):
        # Replies read from the device in stages B/C and in captures 08 / 09.
        self.assertEqual(frames.expected_text_reply(),
                         bytes.fromhex("2fccece8000101a2").ljust(64, b"\x00"))
        self.assertEqual(frames.expected_color_reply(0x33, 0x02, 0x00),
                         bytes.fromhex("2fbbecef0009033302000000ffffffd4").ljust(64, b"\x00"))
        # capture 09: IN payload 03 00 33 07 00 00 ff ff ff (checksum not asserted by hand)
        self.assertEqual(frames.expected_color_reply(0x00, 0x33, 0x07)[:15],
                         bytes.fromhex("2fbbecef0009030033070000ffffff"))

    def test_frame_shape(self):
        f = frames.text_frame("你好")
        self.assertEqual(len(f), 64)
        self.assertEqual(f[:3], b"\x2e\xaa\xec")
        self.assertEqual(f[6:8], b"\x00\x06")  # leading 00, UTF-8 byte count (not chars)

    def test_parse_hex_color(self):
        self.assertEqual(frames.parse_hex_color("#330200"), (0x33, 0x02, 0x00))
        self.assertEqual(frames.parse_hex_color("336699"), (0x33, 0x66, 0x99))
        for bad in ("", "12345", "1234567", "zz0000"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                frames.parse_hex_color(bad)

    def test_rejects_untested_input(self):
        with self.assertRaises(ValueError):
            frames.text_frame("")
        with self.assertRaises(ValueError):
            frames.text_frame("x" * (frames.MAX_TEXT_BYTES + 1))
        with self.assertRaises(ValueError):
            frames.color_frame(256, 0, 0)


if __name__ == "__main__":
    unittest.main()
