"""The stage-2 exchange logic against a fake device (no hidapi, no hardware).

Run: python -m unittest discover -s tests -t .
"""
import ast
import json
import os
import tempfile
import unittest

from pixelbar import frames, upload, upload_replay


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeDevice:
    """script: {write index: [frames the speaker sends after that write]}; delay: seconds per delivered frame."""

    def __init__(self, clock, script, delay=0.0):
        self.clock, self.script, self.delay = clock, script, delay
        self.writes, self.queue = [], []

    def write(self, frame):
        self.script_index = len(self.writes)
        self.writes.append(frame)
        self.queue += self.script.get(self.script_index, [])
        return len(frame)

    def read(self, size, timeout_ms):
        if self.queue:
            self.clock.t += self.delay
            return list(self.queue.pop(0))
        self.clock.t += timeout_ms / 1000
        return []


def reply(cmd, payload):
    return frames.build_frame(cmd, payload, report_id=frames.REPORT_ID_IN, tag=frames.TAG_REPLY)


STEPS, CHECKS = upload_replay.load_plan()
GOOD = {i: [s[2]] for i, s in enumerate(STEPS) if s[2] is not None}


def go(script, delay=0.0):
    clock = Clock()
    dev = FakeDevice(clock, script, delay)
    result = upload_replay.run(dev, STEPS, clock=clock, log=lambda line: None)
    return result, dev


class PlanTests(unittest.TestCase):
    def test_plan_checks_pass(self):
        for name, ok in CHECKS.items():
            self.assertTrue(ok, name)

    def test_plan_shape(self):
        self.assertEqual(len(STEPS), 25)
        self.assertEqual(sum(1 for s in STEPS if s[2] is None), 1)
        self.assertIsNone(STEPS[-2][2])            # the last data chunk is not acknowledged
        self.assertEqual(STEPS[0][1][3], frames.CMD_SET_COLOR)
        self.assertEqual(STEPS[-1][1][6:8], b"\x00\x04")

    def test_only_capture_20_and_no_hardware_imports(self):
        self.assertEqual(upload_replay.KEY, "20")
        with open(upload_replay.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported |= {a.name for a in node.names} | {(node.module or "").split(".")[0]}
        self.assertEqual(imported & {"hid", "device", "serial", "usb"}, set())


PIXEL = (128, 4)
with open(upload_replay.FIXTURE, encoding="utf-8") as _fh:
    IMAGE = bytes.fromhex(json.load(_fh)["20"]["image_hex"])


class PixelTests(unittest.TestCase):
    def test_pixel_location(self):
        self.assertEqual(upload.pixel_location(128, 4), (128, 4))      # page 0, column 128, bit 4
        self.assertEqual(upload.pixel_location(0, 8), (256, 0))        # next page
        self.assertEqual(upload.pixel_location(255, 31), (3 * 256 + 255, 7))
        with self.assertRaises(ValueError):
            upload.pixel_location(256, 0)

    def test_set_pixel_changes_one_bit(self):
        changed = upload.set_pixel(IMAGE, *PIXEL)
        self.assertEqual(sum(bin(a ^ b).count("1") for a, b in zip(IMAGE, changed)), 1)
        self.assertEqual(IMAGE[128], 0x00)
        self.assertEqual(changed[128], 0x10)
        self.assertEqual(upload.unpack_pixels(changed)[4][128], 1)
        self.assertEqual(upload.set_pixel(changed, *PIXEL, on=False), IMAGE)

    def test_allowed_and_rejected_pixels(self):
        self.assertEqual(upload_replay.pixel_problems(IMAGE, *PIXEL), [])
        for x, y in [(0, 0), (255, 0), (0, 31), (255, 31), (3, 4), (128, 3), (252, 4), (128, 28)]:
            self.assertTrue(upload_replay.pixel_problems(IMAGE, x, y), (x, y))    # corners and nearer than 4 to a border
        self.assertTrue(upload_replay.pixel_problems(IMAGE, 100, 6))              # on a lit pixel / inside the text area
        self.assertTrue(upload_replay.pixel_problems(IMAGE, 128, 12))             # below the top of the text

    def test_plan_differs_in_one_frame_only(self):
        steps, checks = upload_replay.load_plan(pixel=PIXEL)
        for name, ok in checks.items():
            self.assertTrue(ok, name)
        original, _ = upload_replay.load_plan()
        diffs = upload_replay.frame_differences(steps, [s[1] for s in original])
        self.assertEqual(list(diffs), [4])                  # data seq 2: the first data byte of record 1 is image byte 128
        self.assertEqual(len(diffs[4]), 2)                  # that byte and the frame's own checksum
        self.assertEqual([s[2] for s in steps], [s[2] for s in original])   # same expected replies
        a, b = original[4][1], steps[4][1]
        data_at, checksum_at = diffs[4]
        self.assertEqual(checksum_at, 6 + int.from_bytes(a[4:6], "big"))
        self.assertEqual(a[data_at] ^ b[data_at], 0x10)                   # bit 4 of one byte
        self.assertEqual((b[checksum_at] - a[checksum_at]) % 256, 0x10)   # the frame checksum follows from it

    def test_disallowed_pixel_fails_the_plan_checks(self):
        _, checks = upload_replay.load_plan(pixel=(0, 0))
        self.assertFalse(all(checks.values()))

    def test_budget_refuses_the_sixth_upload(self):
        with tempfile.TemporaryDirectory() as d:
            budget = upload_replay.UploadBudget(os.path.join(d, "sub", "count.txt"))
            self.assertEqual([budget.take() for _ in range(5)], [1, 2, 3, 4, 5])
            with self.assertRaises(RuntimeError):
                budget.take()
            self.assertEqual(budget.used(), 5)


class ExchangeTests(unittest.TestCase):
    def test_success_writes_the_captured_frames_unchanged(self):
        result, dev = go(GOOD)
        self.assertTrue(result["ok"])
        self.assertEqual((result["written"], result["matched"]), (25, 24))
        self.assertEqual(dev.writes, [s[1] for s in STEPS])
        self.assertEqual(result["unsolicited"], [])

    def test_unsolicited_ee_and_66_are_recorded_and_ignored(self):
        ee = reply(0xEE, bytes.fromhex("0033669900020001ff010000"))
        n66 = reply(0x66, bytes([0x10, 0x08]))
        script = dict(GOOD)
        script[6] = [ee, n66, STEPS[6][2]]
        result, dev = go(script)
        self.assertTrue(result["ok"])
        self.assertEqual([i for i, _ in result["unsolicited"]], [6, 6])
        self.assertEqual(len(dev.writes), 25)

    def test_wrong_ack_stops_at_once_without_retry(self):
        script = dict(GOOD)
        script[5] = [reply(upload.CMD_UPLOAD, b"\x00\x03\x00\x63")]
        result, dev = go(script)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stopped_at"], 5)
        self.assertEqual(len(dev.writes), 6)                      # nothing after the bad step, no resend
        self.assertEqual(len(set(dev.writes)), len(dev.writes))   # no frame was written twice
        self.assertIn("differs", result["reason"])
        self.assertIsNotNone(result["raw"])

    def test_other_pushes_are_not_tolerated(self):
        script = dict(GOOD)
        script[3] = [reply(0x77, b"\x02")]
        result, dev = go(script)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stopped_at"], 3)

    def test_timeout_stops(self):
        script = dict(GOOD)
        del script[1]
        result, dev = go(script)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stopped_at"], 1)
        self.assertEqual(len(dev.writes), 2)
        self.assertIn("no matching reply", result["reason"])
        self.assertAlmostEqual(result["elapsed"], 2.0, delta=0.1)

    def test_other_chunk_size_stops_before_any_data(self):
        script = dict(GOOD)
        script[1] = [reply(upload.CMD_UPLOAD, b"\x00\x01\x00\x20")]
        result, dev = go(script)
        self.assertFalse(result["ok"])
        self.assertEqual(len(dev.writes), 2)
        self.assertIn("chunk size 32", result["reason"])

    def test_total_time_limit(self):
        result, dev = go(GOOD, delay=1.9)   # every reply on time (< 2 s) but 24 x 1.9 s > 30 s
        self.assertFalse(result["ok"])
        self.assertIn("total time limit", result["reason"])
        self.assertLess(len(dev.writes), 25)

    def test_short_write_stops(self):
        class Short(FakeDevice):
            def write(self, frame):
                super().write(frame)
                return -1
        clock = Clock()
        result = upload_replay.run(Short(clock, GOOD), STEPS, clock=clock, log=lambda line: None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stopped_at"], 0)


if __name__ == "__main__":
    unittest.main()
