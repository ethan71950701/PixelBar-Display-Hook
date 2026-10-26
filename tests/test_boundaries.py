"""tools/boundaries.py: the arithmetic behind the size and index boundaries in docs/protocol.md."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import boundaries as b  # noqa: E402


class BoundaryTests(unittest.TestCase):
    def test_the_numbers_of_the_uploads_that_were_sent(self):
        self.assertEqual((b.STREAM_PER_IMAGE, b.chunks(1), b.chunks(2), b.chunks(7), b.chunks(20)), (1064, 22, 43, 149, 426))
        self.assertEqual(b.chunks(20) + 3, 429)                       # frames of the 20-picture upload that was sent

    def test_the_boundaries(self):
        self.assertEqual(b.pictures_within(32767), 30)
        self.assertEqual(b.pictures_within(65535), 61)
        self.assertEqual(b.pictures_within(16384), 15)
        self.assertEqual(256 // b.RECORDS_PER_IMAGE, 32)
        self.assertEqual(b.chunks(12), 256)
        self.assertEqual(b.chunks(13), 277)

    def test_main_prints_without_error(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            b.main()
        self.assertIn("32 pictures use indexes 0..255 exactly", out.getvalue())


if __name__ == "__main__":
    unittest.main()
