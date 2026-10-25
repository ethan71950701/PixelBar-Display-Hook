"""The generated test animation, upload plans of several pictures, and the animation command (dry run).

No picture file and no capture is read; nothing here touches a device.

Run: python -m unittest discover -s tests -t .
"""
import contextlib
import io
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

from pixelbar import __main__ as cli
from pixelbar import device, image, image_send, limits, testanim, upload, upload_replay
from tests.test_upload_replay import Clock, FakeDevice


def bitmap_of(n):
    return b"".join(image.pack_pixels(rows) for rows in testanim.frames(n))


class ClosingDevice(FakeDevice):
    closed = False

    def close(self):
        self.closed = True


class GeneratorTests(unittest.TestCase):
    def test_twenty_pictures_of_the_right_size_with_a_moving_block(self):
        pictures = testanim.frames(20)
        self.assertEqual(len(pictures), 20)
        xs = [testanim.block_x(k) for k in range(20)]
        self.assertEqual((xs[0], xs[-1]), (0, 256 - testanim.BLOCK_W))
        self.assertTrue(all(b > a for a, b in zip(xs, xs[1:])))
        for k, rows in enumerate(pictures):
            self.assertEqual((len(rows), {len(r) for r in rows}), (32, {256}))
            self.assertTrue({v for r in rows for v in r} <= {0, 1})
            block = sum(rows[y][x] for y in range(16, 30) for x in range(xs[k], xs[k] + 14))
            self.assertEqual(block, 14 * 14)                              # the whole block is lit

    def test_every_picture_is_different_and_the_beginning_does_not_depend_on_n(self):
        pictures = testanim.frames(20)
        self.assertEqual(len({str(p) for p in pictures}), 20)
        self.assertEqual(testanim.frames(2), pictures[:2])
        self.assertEqual(testanim.frames(7), pictures[:7])

    def test_the_number_is_drawn_top_left_and_clear_of_the_block(self):
        for k in (0, 9, 19):
            rows = testanim.frame_rows(k)
            number = sum(rows[y][x] for y in range(0, 14) for x in range(0, 20))
            self.assertGreater(number, 0)
            self.assertTrue(all(rows[y][x] == 0 for y in range(0, 14) for x in range(20, 256)))   # nothing else in the top rows

    def test_bad_counts_are_refused(self):
        for bad in (0, 21, -1):
            with self.assertRaises(ValueError):
                testanim.frames(bad)
        with self.assertRaises(ValueError):
            testanim.frame_rows(20)


class PlanTests(unittest.TestCase):
    def test_plans_for_2_7_and_20_pictures_pass_every_check(self):
        for n in (2, 7, 20):
            with self.subTest(pictures=n):
                steps, checks = upload_replay.load_plan(image=bitmap_of(n))
                self.assertTrue(all(checks.values()), {k: v for k, v in checks.items() if not v})
                chunks = math.ceil(n * 8 * 133 / 50)
                self.assertEqual(len(steps), 3 + chunks)
                self.assertIsNone(steps[-2][2])                              # the last data chunk is not acknowledged
                declared = int.from_bytes(steps[1][1][8:12], "big")
                self.assertEqual(declared, n * 1064)

    def test_seven_pictures_have_the_size_of_tempohubs_largest_upload(self):
        steps, _ = upload_replay.load_plan(image=bitmap_of(7))
        self.assertEqual((int.from_bytes(steps[1][1][8:12], "big"), len(steps) - 3), (7448, 149))      # capture 33

    def test_a_single_picture_plan_is_the_same_whichever_way_it_is_built(self):
        one = bitmap_of(1)
        steps, checks = upload_replay.load_plan(image=one)
        self.assertTrue(all(checks.values()))
        self.assertEqual(len(steps), 25)

    def test_a_wrong_plan_is_caught_by_the_independent_checks(self):
        steps, checks = upload_replay.load_plan(image=bitmap_of(2))
        # a builder that forgot to update the size would be caught: check the check
        c = {"type": 2, "xx": 0x64}
        built = [s[1] for s in steps]
        broken = list(built)
        start = bytearray(broken[1])
        start[8:12] = (1064).to_bytes(4, "big")                              # the single-picture size
        start[12] = sum(start[1:12]) & 0xFF
        broken[1] = bytes(start)
        results = upload_replay._multi_image_checks(steps, broken, bitmap_of(2), c, bytes.fromhex("0133669900010221ff"), built[:25])
        self.assertTrue(any(not ok for ok in results.values()))

    def test_total_time_limit_grows_with_the_upload(self):
        self.assertEqual(upload_replay.total_limit_for(upload_replay.load_plan()[0]), 30.0)               # 25 frames: as before
        self.assertEqual(upload_replay.total_limit_for(upload_replay.load_plan(image=bitmap_of(7))[0]), 30.0)
        self.assertEqual(upload_replay.total_limit_for(upload_replay.load_plan(image=bitmap_of(20))[0]), 57.0)

    def test_frame_differences_copes_with_a_longer_plan(self):
        steps, _ = upload_replay.load_plan(image=bitmap_of(2))
        original, _ = upload_replay.load_plan()
        diffs = upload_replay.frame_differences(steps, [s[1] for s in original])
        self.assertTrue(all(i < 25 for i in diffs))


class XxTests(unittest.TestCase):
    def test_a_changed_xx_xx_changes_only_the_record_headers(self):
        bitmap = bitmap_of(2)
        old, _ = upload_replay.load_plan(image=bitmap)
        new, checks = upload_replay.load_plan(image=bitmap, xx=0x0030)
        self.assertTrue(all(checks.values()), {k: v for k, v in checks.items() if not v})
        self.assertEqual(len(old), len(new))
        stream = b"".join(s[1][10:6 + int.from_bytes(s[1][4:6], "big") - 0] for s in new[2:-2])
        self.assertIn(bytes([2, 0, 0, 0x00, 0x30]), stream)               # the first record header: T 00 00 XX XX
        self.assertNotIn(bytes([2, 0, 0, 0x00, 0x64]), stream)
        self.assertEqual(old[0][1], new[0][1])                            # ef frame unchanged
        self.assertEqual(old[1][1], new[1][1])                            # start frame (same size) unchanged
        self.assertEqual(old[-1][1], new[-1][1])                          # end frame unchanged
        self.assertTrue(any(a[1] != b[1] for a, b in zip(old, new)))
        self.assertTrue(any("0x0030" in k for k in checks))

    def test_only_values_tempohub_used_and_only_for_several_pictures(self):
        for bad in (0x0000, 0x000F, 0x0191, 0xFFFF):
            with self.subTest(xx=bad), self.assertRaises(ValueError):
                upload_replay.load_plan(image=bitmap_of(2), xx=bad)
        with self.assertRaises(ValueError):
            upload_replay.load_plan(image=bitmap_of(1), xx=0x0030)
        with self.assertRaises(ValueError):
            upload_replay.load_plan(xx=0x0030)
        for ok in (0x0064, 0x0018, 0x00C8):
            steps, checks = upload_replay.load_plan(image=bitmap_of(2), xx=ok)
            self.assertTrue(all(checks.values()), ok)

    def test_the_identity_hash_depends_on_xx_xx(self):
        bitmap = bitmap_of(2)
        self.assertEqual(image_send.identity_sha256(bitmap), image_send.bitmap_sha256(bitmap))
        self.assertNotEqual(image_send.identity_sha256(bitmap, 0x0030), image_send.bitmap_sha256(bitmap))
        self.assertNotEqual(image_send.identity_sha256(bitmap, 0x0030), image_send.identity_sha256(bitmap, 0x0064))


class ExchangeTests(unittest.TestCase):
    def script(self, steps):
        return {i: [s[2]] for i, s in enumerate(steps) if s[2] is not None}

    def test_twenty_pictures_need_the_longer_limit_at_the_speakers_pace(self):
        steps, _ = upload_replay.load_plan(image=bitmap_of(20))
        clock = Clock()
        dev = FakeDevice(clock, self.script(steps), delay=0.05)               # 50 ms for every acknowledgement
        result = upload_replay.run(dev, steps, clock=clock, log=lambda line: None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["written"], 429)
        self.assertAlmostEqual(result["elapsed"], 428 * 0.05, delta=0.5)      # 428 replies: about 21 s, inside even the old 30 s
        clock = Clock()
        dev = FakeDevice(clock, self.script(steps), delay=0.075)              # a speaker only 1.5 times slower: 32 s ...
        result = upload_replay.run(dev, steps, clock=clock, log=lambda line: None)
        self.assertTrue(result["ok"])                                         # ... is fine inside the 57 s of this upload
        self.assertGreater(result["elapsed"], 30)
        clock = Clock()
        dev = FakeDevice(clock, self.script(steps), delay=0.075)
        result = upload_replay.run(dev, steps, clock=clock, log=lambda line: None, total_limit=30)    # the old fixed limit would have stopped it
        self.assertFalse(result["ok"])
        self.assertIn("total time limit", result["reason"])

    def test_a_runaway_speaker_is_still_stopped(self):
        steps, _ = upload_replay.load_plan(image=bitmap_of(20))
        clock = Clock()
        dev = FakeDevice(clock, self.script(steps), delay=0.2)                # 0.2 s per chunk would need 85 s
        result = upload_replay.run(dev, steps, clock=clock, log=lambda line: None)
        self.assertFalse(result["ok"])
        self.assertIn("total time limit", result["reason"])
        self.assertLess(result["written"], 429)

    def test_send_upload_of_seven_pictures_with_a_fake_device(self):
        with tempfile.TemporaryDirectory() as state:
            bitmap = bitmap_of(7)
            steps, _ = upload_replay.load_plan(image=bitmap)
            clock = Clock()
            devices = []

            def open_device():
                devices.append(ClosingDevice(clock, self.script(steps)))
                return devices[-1]

            result = image_send.send_upload(steps, bitmap, open_device, state_dir=state, day="2026-10-26", log=lambda line: None)
            self.assertTrue(result["ok"])
            self.assertEqual((result["written"], len(devices), devices[0].closed), (152, 1, True))
            self.assertEqual(upload_replay.UploadBudget(limits.state_path(limits.BUDGET_FILE, state)).used(), 1)
            with self.assertRaises(image_send.UploadRefused):                 # the same bitmap again: refused
                image_send.send_upload(steps, bitmap, open_device, state_dir=state, day="2026-10-26", log=lambda line: None)
            self.assertEqual(len(devices), 1)


class CliTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = self._dir.name
        self.addCleanup(self._dir.cleanup)
        patcher = mock.patch.object(limits, "STATE_DIR", os.path.join(self.dir, "state"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def forbid_the_device(self):
        stack = contextlib.ExitStack()
        for name in ("open_device", "send_frame", "tempohub_running", "find_path"):
            stack.enter_context(mock.patch.object(device, name, side_effect=AssertionError(f"device.{name} must not be called")))
        stack.enter_context(mock.patch.dict(sys.modules, {"hid": None}))
        return stack

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def test_dry_run_prints_the_plan_writes_the_sheet_and_touches_nothing_else(self):
        with self.forbid_the_device():
            text = self.run_cli("animation", "--frames", "7", "--preview-dir", self.dir)
        self.assertIn("dry run: nothing was sent", text)
        self.assertIn("declared size 7448 bytes", text)
        self.assertNotIn("[FAIL]", text)
        self.assertIn("To upload it: add --send --expect-sha", text)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "state")))      # no budget or log was written
        sheets = [f for f in os.listdir(self.dir) if f.endswith("_x3.png")]
        self.assertEqual(len(sheets), 1)
        with open(os.path.join(self.dir, sheets[0]), "rb") as fh:
            width, height, _ = image.decode_png(fh.read())
        self.assertEqual((width, height), (256 * 3, (7 * 32 + 6 * 3) * 3))

    def test_xx_option_dry_run_and_refusals(self):
        with self.forbid_the_device():
            text = self.run_cli("animation", "--frames", "2", "--xx", "30", "--preview-dir", self.dir)
        self.assertIn("XX XX is changed from the captured 0x0064 to 0x0030", text)
        self.assertNotIn("[FAIL]", text)
        self.assertIn("record header XX XX is the requested 0x0030", text)
        for bad, frames_ in (("ffff", "2"), ("30", "1"), ("0f", "2")):
            with self.subTest(xx=bad), self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                cli.main(["animation", "--frames", frames_, "--xx", bad, "--preview-dir", self.dir])
            self.assertIn("error", str(ctx.exception))

    def test_the_count_must_be_1_to_20(self):
        for bad in ("0", "21"):
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                cli.main(["animation", "--frames", bad, "--preview-dir", self.dir])
            self.assertIn("between 1 and 20", str(ctx.exception))

    def test_send_needs_the_matching_hash_and_the_confirmation_before_any_device_access(self):
        sha = image_send.bitmap_sha256(bitmap_of(2))
        attempts = [((), "--send needs --expect-sha"), (("--expect-sha", "0" * 12), "plan verification failed"),
                    (("--expect-sha", sha[:12]), "not confirmed")]
        for extra, message in attempts:
            with self.subTest(extra=extra), self.forbid_the_device(), mock.patch.object(sys, "stdin", io.StringIO("")):
                with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                    cli.main(["animation", "--frames", "2", "--preview-dir", self.dir, "--send", *extra])
                self.assertIn(message, str(ctx.exception))

    def test_the_new_modules_have_no_device_or_pillow_imports(self):
        import ast
        for module in (testanim,):
            with open(module.__file__, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            names = {n.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for n in (a.name for a in node.names)}
            names |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            self.assertEqual(names & {"hid", "serial", "usb", "PIL", "device"}, set())

    def test_stack_png_has_the_expected_size(self):
        pictures = testanim.frames(3)
        width, height, brightness = image.decode_png(image.stack_png(pictures, 2, 3))
        self.assertEqual((width, height), (512, (3 * 32 + 2 * 3) * 2))
        self.assertTrue({v for r in brightness for v in r} <= {0, 90, 255})


if __name__ == "__main__":
    unittest.main()
