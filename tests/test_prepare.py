"""Picture preparation (needs Pillow), the upload limits and the image command.

Test pictures are made in memory with Pillow; no picture file or capture is read.
The tests of the limits, the de-duplication and the device-free dry run do not
need Pillow.

Run: python -m unittest discover -s tests -t .
"""
import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from pixelbar import __main__ as cli
from pixelbar import device, image, image_send, limits, prepare, upload, upload_replay
from tests.test_upload_replay import Clock, FakeDevice

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

needs_pillow = unittest.skipUnless(HAVE_PIL, "Pillow is not installed (pip install pillow)")


def lit_box(rows):
    pts = [(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), max(xs), min(ys), max(ys)) if pts else None


def count(rows):
    return sum(map(sum, rows))


@needs_pillow
class PrepareTests(unittest.TestCase):
    def run_prepare(self, img, **kw):
        return prepare.prepare_image(img, **kw)

    def test_output_is_always_256x32_and_binary(self):
        for size in [(10, 10), (256, 32), (1000, 300), (300, 1000), (1, 1), (8000, 8), (7, 5000)]:
            for fit in prepare.FITS:
                for mode in prepare.MODES:
                    with self.subTest(size=size, fit=fit, mode=mode):
                        rows = self.run_prepare(Image.new("RGB", size, (200, 120, 40)), fit=fit, mode=mode)
                        self.assertEqual((len(rows), {len(r) for r in rows}), (32, {256}))
                        self.assertTrue({v for r in rows for v in r} <= {0, 1})

    def test_saved_png_has_only_0_and_255(self):
        rows = self.run_prepare(Image.linear_gradient("L").resize((300, 100)))
        _, _, brightness = image.decode_png(image.rows_to_png(rows))
        self.assertEqual((len(brightness), len(brightness[0])), (32, 256))
        self.assertTrue({v for r in brightness for v in r} <= {0, 255})

    def test_white_and_black(self):
        for mode in prepare.MODES:
            self.assertEqual(count(self.run_prepare(Image.new("L", (50, 50), 255), mode=mode)), 256 * 32)
            self.assertEqual(count(self.run_prepare(Image.new("L", (50, 50), 0), mode=mode)), 0)

    def test_contain_keeps_the_proportions(self):
        square = self.run_prepare(Image.new("L", (100, 100), 255), fit="contain", mode="threshold")
        self.assertEqual(lit_box(square), (112, 143, 0, 31))                  # 32x32, centred
        wide = self.run_prepare(Image.new("L", (200, 100), 255), fit="contain", mode="threshold")
        x0, x1, y0, y1 = lit_box(wide)
        self.assertEqual((x1 - x0 + 1, y1 - y0 + 1), (64, 32))                # 2:1 stays 2:1
        self.assertEqual(count(self.run_prepare(Image.new("L", (512, 64), 255), fit="contain", mode="threshold")), 256 * 32)

    def test_stretch_fills_and_cover_fills(self):
        for fit in ("stretch", "cover"):
            self.assertEqual(count(self.run_prepare(Image.new("L", (100, 100), 255), fit=fit, mode="threshold")), 256 * 32)

    def test_cover_focus_chooses_the_kept_part(self):
        tall = Image.new("L", (512, 128), 0)
        tall.paste(255, (0, 0, 512, 64))                                      # top half white
        self.assertEqual(count(self.run_prepare(tall, focus_y=0, mode="threshold")), 256 * 32)
        self.assertEqual(count(self.run_prepare(tall, focus_y=1, mode="threshold")), 0)
        wide = Image.new("L", (1024, 32), 0)
        wide.paste(255, (0, 0, 512, 32))                                      # left half white
        self.assertEqual(count(self.run_prepare(wide, focus_x=0, mode="threshold")), 256 * 32)
        self.assertEqual(count(self.run_prepare(wide, focus_x=1, mode="threshold")), 0)
        self.assertEqual(count(self.run_prepare(wide, focus_x=0.5, mode="threshold")), 128 * 32)   # the middle: the border is in the centre

    def test_threshold_gives_known_results(self):
        img = Image.new("L", (256, 32), 127)
        img.paste(128, (128, 0, 256, 32))
        right_half = [[1 if x >= 128 else 0 for x in range(256)] for _ in range(32)]
        self.assertEqual(self.run_prepare(img, mode="threshold", threshold=128), right_half)
        self.assertEqual(count(self.run_prepare(img, mode="threshold", threshold=127)), 256 * 32)
        self.assertEqual(count(self.run_prepare(img, mode="threshold", threshold=129)), 0)
        self.assertEqual(count(self.run_prepare(img, mode="threshold", threshold=0)), 256 * 32)

    def test_dither_gives_about_the_right_share(self):
        for grey, low, high in [(64, 0.22, 0.28), (128, 0.47, 0.53), (192, 0.72, 0.78)]:
            with self.subTest(grey=grey):
                share = count(self.run_prepare(Image.new("L", (256, 32), grey), mode="dither")) / (256 * 32)
                self.assertTrue(low <= share <= high, share)

    def test_invert_and_transparency(self):
        self.assertEqual(count(self.run_prepare(Image.new("L", (40, 40), 255), invert=True, mode="threshold")), 0)
        self.assertEqual(count(self.run_prepare(Image.new("L", (40, 40), 0), invert=True, mode="threshold")), 256 * 32)
        clear = Image.new("RGBA", (40, 40), (255, 255, 255, 0))              # white but fully transparent
        self.assertEqual(count(self.run_prepare(clear, mode="threshold")), 0)
        self.assertEqual(count(self.run_prepare(clear, mode="threshold", invert=True)), 0)   # still black, not white
        self.assertEqual(count(self.run_prepare(Image.new("RGBA", (40, 40), (255, 255, 255, 255)), mode="threshold")), 256 * 32)
        palette = Image.new("P", (40, 40), 0)
        palette.putpalette([255, 255, 255] + [0] * 765)                      # index 0 is white ...
        palette.info["transparency"] = 0                                     # ... but transparent
        self.assertEqual(count(self.run_prepare(palette, mode="threshold")), 0)

    def test_autocontrast(self):
        img = Image.new("L", (256, 32), 140)
        img.paste(160, (128, 0, 256, 32))
        self.assertEqual(count(self.run_prepare(img, mode="threshold")), 256 * 32)                       # both above 128
        self.assertEqual(count(self.run_prepare(img, mode="threshold", autocontrast=True)), 128 * 32)    # stretched: only the lighter half

    def test_exif_orientation_is_applied(self):
        upright = Image.new("L", (256, 64), 0)
        upright.paste(255, (0, 0, 128, 32))                                  # white block at the top left
        turned = upright.transpose(getattr(Image, "Transpose", Image).ROTATE_90)   # as a phone would store it
        exif = Image.Exif()
        exif[0x0112] = 6                                                     # "rotate 270 to see it upright"
        buf = io.BytesIO()
        turned.save(buf, "PNG", exif=exif)
        buf.seek(0)
        opened = Image.open(buf)
        if opened.getexif().get(0x0112) != 6:
            self.skipTest("this Pillow does not carry the EXIF orientation through PNG")
        self.assertEqual(self.run_prepare(opened, mode="threshold"), self.run_prepare(upright, mode="threshold"))

    def test_too_large_is_refused(self):
        with self.assertRaises(prepare.PrepareError) as ctx:
            self.run_prepare(Image.new("L", (8001, 4)))
        self.assertIn("8000", str(ctx.exception))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "big.png")
            Image.new("L", (4, 8001)).save(path)
            with self.assertRaises(prepare.PrepareError):
                prepare.open_image(path)

    def test_same_input_same_settings_same_hash(self):
        img = Image.linear_gradient("L").resize((400, 90))
        a = image_send.bitmap_sha256(image.pack_pixels(self.run_prepare(img)))
        b = image_send.bitmap_sha256(image.pack_pixels(self.run_prepare(img.copy())))
        c = image_send.bitmap_sha256(image.pack_pixels(self.run_prepare(img, mode="threshold")))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_bad_options(self):
        img = Image.new("L", (10, 10))
        for bad in [dict(fit="zoom"), dict(mode="halftone"), dict(focus_x=1.5), dict(focus_y=-0.1), dict(threshold=256)]:
            with self.subTest(bad=bad), self.assertRaises(prepare.PrepareError):
                self.run_prepare(img, **bad)


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = self._dir.name
        self.addCleanup(self._dir.cleanup)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def log(self, *entries):
        self.write(limits.LOG_FILE, "".join(json.dumps(e) + "\n" for e in entries))

    def test_defaults_and_the_file_override(self):
        self.assertEqual(limits.load_limits(self.dir), (5, 10))
        self.write(limits.LIMITS_FILE, '{"total": 8, "per_day": 4}')
        self.assertEqual(limits.load_limits(self.dir), (8, 4))
        self.write(limits.LIMITS_FILE, "not json")
        with self.assertRaises(limits.LimitsError):
            limits.load_limits(self.dir)
        self.write(limits.LIMITS_FILE, '{"total": -1}')
        with self.assertRaises(limits.LimitsError):
            limits.load_limits(self.dir)

    def test_total_budget_refuses_at_the_limit(self):
        self.write(limits.BUDGET_FILE, "4")
        self.assertEqual(limits.problems_for_upload("a" * 64, self.dir, "2026-10-26"), [])
        self.write(limits.BUDGET_FILE, "5")
        problems = limits.problems_for_upload("a" * 64, self.dir, "2026-10-26")
        self.assertEqual(len(problems), 1)
        self.assertIn("budget", problems[0])
        self.write(limits.LIMITS_FILE, '{"total": 6}')        # only an edit of the file raises it
        self.assertEqual(limits.problems_for_upload("a" * 64, self.dir, "2026-10-26"), [])

    def test_daily_cap(self):
        today = "2026-10-26"
        self.log(*[{"date": today, "sha256": "x", "status": "started"}] * 9)
        self.assertEqual(limits.problems_for_upload("b" * 64, self.dir, today), [])          # 9 of 10 used: one more is allowed
        self.log(*[{"date": today, "sha256": "x", "status": "started"}] * 10)
        problems = limits.problems_for_upload("b" * 64, self.dir, today)
        self.assertTrue(any("daily" in p for p in problems))
        self.assertEqual(limits.problems_for_upload("b" * 64, self.dir, "2026-10-27"), [])   # another day

    def test_the_same_bitmap_as_the_last_completed_upload_is_refused(self):
        self.log({"date": "2026-10-25", "sha256": "A", "status": "started"}, {"date": "2026-10-25", "sha256": "A", "status": "ok"},
                 {"date": "2026-10-25", "sha256": "B", "status": "started"}, {"date": "2026-10-25", "sha256": "B", "status": "failed"})
        self.assertTrue(any("same" in p for p in limits.problems_for_upload("A", self.dir, "2026-10-26")))   # last COMPLETED one
        self.assertEqual(limits.problems_for_upload("B", self.dir, "2026-10-26"), [])                        # a failed one does not block
        self.assertEqual(limits.problems_for_upload("C", self.dir, "2026-10-26"), [])

    def test_no_command_line_option_raises_a_limit(self):
        for sub in ("image", "prepare"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                cli.main([sub, "--help"])
            text = out.getvalue()
            for word in ("--total", "--per-day", "--limit", "--budget", "--force", "--allow-repeat"):
                self.assertNotIn(word, text)


class SendTests(unittest.TestCase):
    """image_send.send_upload with a fake device: limits, counting, logging, no retry."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = self._dir.name
        self.addCleanup(self._dir.cleanup)
        rows = [[1 if (x + y) % 7 == 0 else 0 for x in range(256)] for y in range(32)]
        self.bitmap = image.pack_pixels(rows)
        self.steps, self.checks = upload_replay.load_plan(image=self.bitmap)
        self.script = {i: [s[2]] for i, s in enumerate(self.steps) if s[2] is not None}
        self.opened = 0
        self.clock = Clock()

    def opener(self, script=None):
        def open_device():
            self.opened += 1
            return FakeDeviceWithClose(self.clock, script if script is not None else self.script)
        return open_device

    def send(self, bitmap=None, script=None, day="2026-10-26"):
        return image_send.send_upload(self.steps, bitmap or self.bitmap, self.opener(script), state_dir=self.dir, day=day, log=lambda line: None)

    def budget(self):
        return upload_replay.UploadBudget(limits.state_path(limits.BUDGET_FILE, self.dir)).used()

    def statuses(self):
        return [e["status"] for e in limits.UploadLog(limits.state_path(limits.LOG_FILE, self.dir)).entries()]

    def test_plan_checks_pass(self):
        self.assertTrue(all(self.checks.values()), self.checks)

    def test_success_counts_and_logs(self):
        result = self.send()
        self.assertTrue(result["ok"])
        self.assertEqual(result["written"], 25)
        self.assertEqual(self.budget(), 1)
        self.assertEqual(self.statuses(), ["started", "ok"])

    def test_the_same_bitmap_is_not_sent_twice_and_the_device_is_not_even_opened(self):
        self.send()
        opened = self.opened
        with self.assertRaises(image_send.UploadRefused) as ctx:
            self.send()
        self.assertIn("same as the last completed upload", str(ctx.exception))
        self.assertEqual(self.opened, opened)
        self.assertEqual(self.budget(), 1)

    def test_a_used_up_budget_refuses_before_opening_the_device(self):
        with open(limits.state_path(limits.BUDGET_FILE, self.dir), "w", encoding="utf-8") as fh:
            fh.write("5")
        with self.assertRaises(image_send.UploadRefused):
            self.send()
        self.assertEqual((self.opened, self.budget()), (0, 5))

    def test_the_daily_cap_refuses(self):
        log = limits.UploadLog(limits.state_path(limits.LOG_FILE, self.dir))
        for _ in range(10):
            log.append(date="2026-10-26", sha256="x", status="started")
        with self.assertRaises(image_send.UploadRefused):
            self.send()
        self.assertEqual(self.opened, 0)

    def test_failure_stops_counts_logs_and_does_not_retry(self):
        script = dict(self.script)
        script[5] = [upload.frames.build_frame(upload.CMD_UPLOAD, b"\x00\x03\x00\x63", report_id=0x2F, tag=0xBB)]
        dev_holder = []

        def open_device():
            self.opened += 1
            dev = FakeDeviceWithClose(self.clock, script)
            dev_holder.append(dev)
            return dev

        result = image_send.send_upload(self.steps, self.bitmap, open_device, state_dir=self.dir, day="2026-10-26", log=lambda line: None)
        self.assertFalse(result["ok"])
        self.assertEqual(self.opened, 1)                               # one attempt, no second open
        self.assertEqual(len(dev_holder[0].writes), 6)                 # nothing after the bad step
        self.assertEqual((self.budget(), self.statuses()), (1, ["started", "failed"]))
        self.assertTrue(dev_holder[0].closed)

    def test_a_missing_device_costs_nothing(self):
        def broken():
            raise device.DeviceError("no device")
        with self.assertRaises(device.DeviceError):
            image_send.send_upload(self.steps, self.bitmap, broken, state_dir=self.dir, day="2026-10-26", log=lambda line: None)
        self.assertEqual((self.budget(), self.statuses()), (0, []))


class FakeDeviceWithClose(FakeDevice):
    closed = False

    def close(self):
        self.closed = True


class CliTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = self._dir.name
        self.addCleanup(self._dir.cleanup)
        patcher = mock.patch.object(limits, "STATE_DIR", os.path.join(self.dir, "state"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def forbid_the_device(self):
        stack = contextlib.ExitStack()
        for name in ("open_device", "send_frame", "tempohub_running", "find_path"):
            stack.enter_context(mock.patch.object(device, name, side_effect=AssertionError(f"device.{name} must not be called")))
        stack.enter_context(mock.patch.dict(sys.modules, {"hid": None}))     # importing hidapi would fail
        return stack

    @needs_pillow
    def test_image_without_send_never_touches_the_device_and_previews_the_real_bitmap(self):
        path = os.path.join(self.dir, "picture.png")
        Image.linear_gradient("L").resize((300, 100)).save(path)
        with self.forbid_the_device():
            text = self.run_cli("image", path, "--preview-dir", self.dir)
        self.assertIn("dry run: nothing was sent", text)
        self.assertIn("uploads used 0 of 5 in total", text)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "state")))      # no budget or log file was written
        previews = [f for f in os.listdir(self.dir) if f.endswith("_x4.png")]
        self.assertEqual(len(previews), 1)
        with open(os.path.join(self.dir, previews[0]), "rb") as fh:
            width, height, brightness = image.decode_png(fh.read())
        self.assertEqual((width, height), (1024, 128))
        bitmap = image.pack_pixels(prepare.prepare_file(path))
        expected = image.scale_rows(upload.unpack_pixels(bitmap), 4)           # the bitmap that would be uploaded, enlarged
        self.assertEqual([[1 if v > 127 else 0 for v in row] for row in brightness], expected)

    @needs_pillow
    def test_image_shows_only_data_frames_differing_from_capture_20(self):
        path = os.path.join(self.dir, "p.png")
        Image.linear_gradient("L").resize((256, 32)).save(path)
        with self.forbid_the_device():
            text = self.run_cli("image", path, "--preview-dir", self.dir)
        self.assertNotIn("[FAIL]", text)
        self.assertIn("[OK] ef, start and end frames equal the TempoHub frames of capture 20", text)
        self.assertIn("[OK] every differing frame is a data frame and differs only in image bytes and its own checksum", text)

    def send_attempt(self, *extra):
        """Run `image PIC --send ...` with the device forbidden and no terminal; return the SystemExit text."""
        path = os.path.join(self.dir, "p.png")
        Image.linear_gradient("L").resize((256, 32)).save(path)
        with self.forbid_the_device(), mock.patch.object(sys, "stdin", io.StringIO("")):       # not a terminal: no question can be asked
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                cli.main(["image", path, "--preview-dir", self.dir, "--send", *extra])
        return str(ctx.exception), image_send.bitmap_sha256(image.pack_pixels(prepare.prepare_file(path)))

    @needs_pillow
    def test_send_needs_the_confirmation_before_any_device_access(self):
        _, sha = self.send_attempt("--expect-sha", "0" * 12)          # (computes the real hash too)
        message, _ = self.send_attempt("--expect-sha", sha[:12])
        self.assertIn("not confirmed", message)                       # the hash matched; the missing terminal stops it next

    @needs_pillow
    def test_send_needs_the_hash_of_the_previewed_bitmap(self):
        message, sha = self.send_attempt()
        self.assertIn("--send needs --expect-sha", message)
        self.assertIn(sha[:16], message)                              # it tells which hash to copy
        message, _ = self.send_attempt("--expect-sha", "0" * 12)
        self.assertIn("plan verification failed", message)
        message, _ = self.send_attempt("--expect-sha", sha[:11])      # too short to bind
        self.assertIn("plan verification failed", message)

    @needs_pillow
    def test_dry_run_checks_the_hash_when_one_is_given(self):
        path = os.path.join(self.dir, "p.png")
        Image.linear_gradient("L").resize((256, 32)).save(path)
        sha = image_send.bitmap_sha256(image.pack_pixels(prepare.prepare_file(path)))
        with self.forbid_the_device():
            good = self.run_cli("image", path, "--preview-dir", self.dir, "--expect-sha", sha[:12])
            bad = self.run_cli("image", path, "--preview-dir", self.dir, "--expect-sha", "f" * 12)
        self.assertIn("[OK] --expect-sha matches", good)
        self.assertIn("[FAIL] --expect-sha matches", bad)

    @needs_pillow
    def test_prepare_writes_a_binary_256x32_png_and_will_not_overwrite(self):
        src, out = os.path.join(self.dir, "s.png"), os.path.join(self.dir, "o.png")
        Image.linear_gradient("L").resize((400, 90)).save(src)
        with self.forbid_the_device():
            self.run_cli("prepare", src, out)
        with open(out, "rb") as fh:
            width, height, brightness = image.decode_png(fh.read())
        self.assertEqual((width, height), (256, 32))
        self.assertTrue({v for r in brightness for v in r} <= {0, 255})
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
            cli.main(["prepare", src, out])
        self.assertIn("exists", str(ctx.exception))
        self.run_cli("prepare", src, out, "--overwrite")

    def test_without_pillow_the_picture_commands_explain_and_the_others_still_work(self):
        missing = prepare.PrepareError("Pillow is not installed. The image and prepare commands need it: pip install pillow")
        with mock.patch.object(prepare, "_pillow", side_effect=missing):
            for argv in (["image", "x.jpg"], ["prepare", "x.jpg", "y.png"]):
                with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                    cli.main(argv)
                self.assertIn("pip install pillow", str(ctx.exception))
            self.assertIn("dry run", self.run_cli("text", "Hi"))
            self.assertIn("dry run", self.run_cli("color", "ff0000"))

    def test_the_new_modules_have_no_device_imports_and_pillow_stays_in_prepare(self):
        def imports(module):
            with open(module.__file__, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names |= {(node.module or "").split(".")[0]} | {a.name for a in node.names}
            return names

        for module in (prepare, limits, image_send, image, upload):
            self.assertEqual(imports(module) & {"hid", "serial", "usb"}, set(), module.__name__)
        for module in (limits, image_send, image, upload, upload_replay, device):
            self.assertNotIn("PIL", imports(module), module.__name__)
        self.assertNotIn("device", imports(prepare))                          # the picture code does not use the device module
        self.assertIn("PIL", imports(prepare))                                # Pillow is imported here, and only here


if __name__ == "__main__":
    unittest.main()
