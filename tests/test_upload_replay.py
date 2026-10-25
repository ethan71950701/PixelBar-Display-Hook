"""The stage-2 exchange logic against a fake device (no hidapi, no hardware).

Run: python -m unittest discover -s tests -t .
"""
import ast
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
