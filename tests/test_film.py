"""The filming tools: segments, plan, capture run (fake uploads, virtual clock), frame extraction and the post-processing draft.

Only synthetic data: pictures made by testanim, videos generated into a temporary folder; no real video or picture file is read,
no device is opened.

Run: python -m unittest discover -s tests -t .
"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

from pixelbar import __main__ as cli  # noqa: E402
from pixelbar import device, film, image, limits, testanim  # noqa: E402
import film_post  # noqa: E402

try:
    import av
    from PIL import Image
except ImportError:
    av = None


class PlanTests(unittest.TestCase):
    def test_segments_of_a_whole_video(self):
        segs = film.segments(220)
        self.assertEqual(len(segs), 11)
        self.assertEqual((segs[0]["first"], segs[0]["last"], segs[-1]["first"], segs[-1]["last"]), (1, 20, 201, 220))
        self.assertTrue(all(s["pictures"] == 20 for s in segs))
        tail = film.segments(45)
        self.assertEqual([(s["first"], s["last"], s["pictures"]) for s in tail], [(1, 20, 20), (21, 40, 20), (41, 45, 5)])
        with self.assertRaises(film.FilmError):
            film.segments(0)

    def test_times_of_a_segment_and_of_the_whole_plan(self):
        t = film.segment_times({"pictures": 20})
        self.assertAlmostEqual(t["upload"], 23.3)
        self.assertEqual((t["play"], t["buffer"]), (20.0, 2.0))
        self.assertAlmostEqual(t["total"], 45.3)
        p = film.plan(220, used=22, total_limit=30)
        self.assertAlmostEqual(p["total_seconds"], 11 * 45.3)
        self.assertEqual([r["starts_at"] for r in p["segments"][:3]], [0.0, 45.3, 90.6])
        short = film.segment_times({"pictures": 5})
        self.assertAlmostEqual(short["upload"], 23.3 / 4)
        self.assertEqual(short["play"], 5.0)

    def test_quota_is_reported_and_never_changed(self):
        p = film.plan(220, used=22, total_limit=30)
        self.assertEqual((p["needed"], p["available"], p["excess"], p["raise_to"]), (11, 8, 3, 33))
        self.assertEqual((p["trial_uploads"], p["trial_fits"]), (3, True))
        text = film.format_plan(p)
        self.assertIn("3 more than the quota allows", text)
        self.assertIn("at least 33", text)
        self.assertIn("only you change that file", text)
        enough = film.plan(60, used=22, total_limit=30)
        self.assertEqual(enough["excess"], 0)
        self.assertIn("the quota is enough", film.format_plan(enough))
        full = film.plan(220, used=30, total_limit=30)
        self.assertEqual((full["available"], full["trial_fits"]), (0, False))
        self.assertIn("DOES NOT fit", film.format_plan(full))

    def test_plan_command_reads_the_state_but_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(limits, "STATE_DIR", os.path.join(folder, "state")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(["film", "plan", "--pictures", "220", "--dir", folder])
            self.assertIn("11 uploads", out.getvalue())
            self.assertIn("0 of 5 used", out.getvalue())           # the defaults when there is no state at all
            self.assertFalse(os.path.exists(os.path.join(folder, "state")))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.t = {"now": 0.0}
        self.events = []
        self.segs = film.segments(100)                              # 5 segments

        def now():
            return datetime.datetime(2026, 10, 26, 12, 0, 0) + datetime.timedelta(seconds=self.t["now"]), self.t["now"]

        self.log = film.Log(None, now, echo=lambda text: self.events.append(text))
        self.uploaded, self.sleeps = [], []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t["now"] += seconds

    def ok_upload(self, seg):
        self.uploaded.append(seg["index"])
        self.t["now"] += 23.3
        return {"ok": True, "written": 429, "matched": 428, "elapsed": 23.3, "reason": None, "unsolicited": []}

    def test_uploads_in_order_with_the_wait_and_a_hard_limit(self):
        result = film.run_film(self.segs, self.ok_upload, self.sleep, self.log, max_uploads=3)
        self.assertEqual(self.uploaded, [1, 2, 3])                  # the hard limit, not the 5 segments
        self.assertEqual(result, {"done": 3, "stopped": "max-uploads", "ok": True})
        self.assertEqual(self.sleeps, [22.0, 22.0, 22.0])            # 20 s of play + 2 s of buffer after every upload, also the last
        self.assertAlmostEqual(self.t["now"], 3 * 45.3)

    def test_the_log_has_exact_times_for_every_start_and_end(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "film_log.jsonl")
            log = film.Log(path, lambda: (datetime.datetime(2026, 10, 26, 12, 0, 0) + datetime.timedelta(seconds=self.t["now"]), self.t["now"]),
                           echo=lambda text: None)
            film.run_film(self.segs[:2], self.ok_upload, self.sleep, log, max_uploads=3)
            with open(path, encoding="utf-8") as fh:
                records = [json.loads(line) for line in fh]
        names = [r["event"] for r in records]
        self.assertEqual(names, ["run_start", "upload_start", "upload_end", "wait_start", "wait_end",
                                 "upload_start", "upload_end", "wait_start", "wait_end", "run_end"])
        starts = [r for r in records if r["event"] == "upload_start"]
        ends = [r for r in records if r["event"] == "upload_end"]
        self.assertEqual((starts[0]["t"], ends[0]["t"], starts[1]["t"], ends[1]["t"]), (0.0, 23.3, 45.3, 68.6))
        self.assertEqual(starts[0]["wall"], "2026-10-26T12:00:00.000")        # to the millisecond
        self.assertEqual(ends[0]["wall"], "2026-10-26T12:00:23.300")
        self.assertTrue(all(r["ok"] for r in ends))

    def test_the_first_bad_reply_stops_without_a_retry(self):
        calls = []

        def upload(seg):
            calls.append(seg["index"])
            self.t["now"] += 5
            return {"ok": seg["index"] != 2, "written": 10, "matched": 9, "elapsed": 5.0,
                    "reason": None if seg["index"] != 2 else "reply 7 did not match", "unsolicited": []}

        result = film.run_film(self.segs, upload, self.sleep, self.log, max_uploads=5)
        self.assertEqual(calls, [1, 2])                              # segment 2 failed: nothing after it, and no second try of it
        self.assertFalse(result["ok"])
        self.assertIn("did not match", result["stopped"])
        self.assertEqual(self.sleeps, [22.0])                       # no wait after the failed one

    def test_an_exception_stops_too(self):
        def upload(seg):
            if seg["index"] == 2:
                raise OSError("read error")
            return self.ok_upload(seg)

        result = film.run_film(self.segs, upload, self.sleep, self.log, max_uploads=5)
        self.assertEqual((result["done"], result["ok"]), (1, False))
        self.assertIn("read error", result["stopped"])
        self.assertEqual(self.uploaded, [1])

    def test_confirmation_needs_both_answers(self):
        for answers, expected in ((["y", "y"], True), (["y", "n"], False), (["n", "y"], False), (["", ""], False), (["y"], False)):
            with self.subTest(answers=answers):
                it = iter(answers)

                def ask(question):
                    try:
                        return next(it)
                    except StopIteration:
                        raise EOFError from None

                self.assertEqual(film.confirm_filming(ask), expected)


class CliTests(unittest.TestCase):
    def forbid_the_device(self):
        stack = contextlib.ExitStack()
        for name in ("open_device", "send_frame", "tempohub_running", "find_path"):
            stack.enter_context(mock.patch.object(device, name, side_effect=AssertionError(f"device.{name} must not be called")))
        stack.enter_context(mock.patch.dict(sys.modules, {"hid": None}))
        return stack

    def test_sending_is_not_enabled_and_the_rehearsal_sends_nothing(self):
        self.assertFalse(film.SEND_ENABLED)
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(limits, "STATE_DIR", os.path.join(folder, "state")), self.forbid_the_device():
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                cli.main(["film", "run", "--pictures", "60", "--dir", folder, "--send"])
            self.assertIn("not enabled", str(ctx.exception))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(["film", "run", "--pictures", "100", "--dir", folder, "--rehearse"])
            text = out.getvalue()
            self.assertIn("REHEARSAL on a virtual clock", text)
            self.assertEqual(text.count("upload_start"), 3)                     # --max-uploads 3 by default
            self.assertIn("--max-uploads 3 reached", text)
            self.assertIn("nothing was sent", text)
            self.assertFalse(os.path.exists(os.path.join(folder, "state")))
            self.assertFalse(os.path.exists(os.path.join(folder, "film_log.jsonl")))
            with self.assertRaises(SystemExit):                                  # without --rehearse or --send it only explains
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.main(["film", "run", "--pictures", "60", "--dir", folder])

    def test_the_real_uploader_is_built_from_the_checked_plan(self):
        """make_uploader exists but is never called by the CLI; with a fake device it makes one checked upload."""
        from tests.test_upload_replay import Clock, FakeDevice
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as state:
            files = []
            for k in range(5):
                path = os.path.join(folder, film.FRAME_NAME.format(k + 1))
                with open(path, "wb") as fh:
                    fh.write(image.rows_to_png(testanim.frame_rows(k), 1))
                files.append(path)
            seg = film.segments(5)[0]
            bitmap = film.segment_bitmap(files, seg)
            from pixelbar import upload_replay
            steps, _ = upload_replay.load_plan(image=bitmap, xx=film.XX)
            script = {i: [s[2]] for i, s in enumerate(steps) if s[2] is not None}
            devices = []

            class Closing(FakeDevice):
                def close(self):
                    pass

            def open_device():
                devices.append(Closing(Clock(), script))
                return devices[-1]

            with mock.patch.object(limits, "STATE_DIR", state):
                result = film.make_uploader(files, open_device=open_device, log=lambda line: None)(seg)
            self.assertTrue(result["ok"], result)
            stream = b"".join(f[10:6 + int.from_bytes(f[4:6], "big")] for f in devices[0].writes[2:-2])
            self.assertIn(bytes([2, 0, 0, 0x01, 0xF4]), stream)                  # XX XX = 0x01f4 in the record headers


@unittest.skipIf(av is None, "PyAV or Pillow is not installed")
class FramesTests(unittest.TestCase):
    def make_video(self, path, seconds=4, fps=10, size=(64, 48)):
        """Alternating white and black every second."""
        with av.open(path, "w", format="mp4") as container:
            stream = container.add_stream("mpeg4", rate=fps)
            stream.width, stream.height, stream.pix_fmt = size[0], size[1], "yuv420p"
            for i in range(seconds * fps):
                grey = 255 if (i // fps) % 2 == 0 else 0
                for packet in stream.encode(av.VideoFrame.from_image(Image.new("RGB", size, (grey, grey, grey)))):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

    def test_one_picture_per_second_inverted_and_contained(self):
        with tempfile.TemporaryDirectory() as folder:
            video = os.path.join(folder, "clip.mp4")
            self.make_video(video)
            out = os.path.join(folder, "frames")
            n = film.extract_frames(video, out)
            self.assertIn(n, (4, 5))                                              # t = 0, 1, 2, 3 (and 4 if the clip ends on it)
            files = film.frame_files(out)
            self.assertEqual([os.path.basename(f) for f in files[:4]], [film.FRAME_NAME.format(k) for k in (1, 2, 3, 4)])
            lit = [sum(map(sum, image.png_to_pixels(open(f, "rb").read()))) for f in files[:4]]
            contained = 43 * 32                                                   # 64x48 scaled to 43x32, centred
            self.assertAlmostEqual(lit[1], contained, delta=64)                   # black source second -> inverted -> lit
            self.assertAlmostEqual(lit[3], contained, delta=64)
            self.assertEqual((lit[0], lit[2]), (0, 0))                            # white source second -> inverted -> dark
            plain = os.path.join(folder, "plain")
            film.extract_frames(video, plain, invert=False)
            lit_plain = [sum(map(sum, image.png_to_pixels(open(f, "rb").read()))) for f in film.frame_files(plain)[:2]]
            self.assertGreater(lit_plain[0], 1000)
            self.assertEqual(lit_plain[1], 0)
            sheets = film.write_sheets(out, os.path.join(folder, "preview"))
            self.assertEqual(len(sheets), 1)
            self.assertTrue(os.path.getsize(sheets[0]) > 100)

    def test_the_frames_command_refuses_to_overwrite_and_writes_outside_the_given_folder_only(self):
        with tempfile.TemporaryDirectory() as folder:
            video = os.path.join(folder, "clip.mp4")
            self.make_video(video, seconds=2)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(["film", "frames", video, "--dir", folder])
            self.assertIn("pictures (1 per second) written", out.getvalue())
            self.assertIn("Nothing was sent", out.getvalue())
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()):
                cli.main(["film", "frames", video, "--dir", folder])
            self.assertIn("--overwrite", str(ctx.exception))
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["film", "frames", video, "--dir", folder, "--overwrite"])


class PostTests(unittest.TestCase):
    def events(self):
        """A log of 3 uploads of 23.3 s with 22 s of waiting after each (virtual clock), in the format `film run` writes."""
        rows, t = [], 0.0
        for seg in (1, 2, 3):
            rows.append({"event": "upload_start", "segment": seg, "t": t, "wall": "w"})
            t += 23.3
            rows.append({"event": "upload_end", "segment": seg, "t": round(t, 3), "wall": "w", "ok": True})
            t += 22.0
        return rows

    def test_windows_follow_the_log_and_the_anchor(self):
        wins = film_post.windows(self.events(), anchor=10.0)
        self.assertEqual(wins, [(1, 33.3, 53.3), (2, 78.6, 98.6), (3, 123.9, 143.9)])
        self.assertTrue(all(abs((e - b) - 20.0) < 1e-6 for _, b, e in wins))              # the play of 20 pictures, speed untouched
        lead = film_post.windows(self.events(), anchor=10.0, lead=0.5, trim=0.5)
        self.assertEqual(lead[0], (1, 33.8, 52.8))
        with self.assertRaises(ValueError):
            film_post.windows(self.events(), anchor=0, lead=10, trim=10)

    def test_a_failed_upload_gets_no_window_and_overlap_is_refused(self):
        events = self.events()
        events[3]["ok"] = False
        self.assertEqual([w[0] for w in film_post.windows(events, anchor=0)], [1, 3])
        overlapping = [{"event": "upload_start", "segment": 1, "t": 0.0, "wall": "w"}, {"event": "upload_end", "segment": 1, "t": 20.0, "wall": "w", "ok": True},
                       {"event": "upload_start", "segment": 2, "t": 25.0, "wall": "w"}, {"event": "upload_end", "segment": 2, "t": 30.0, "wall": "w", "ok": True}]
        with self.assertRaises(ValueError):
            film_post.windows(overlapping, anchor=0)

    def test_problems_when_the_windows_do_not_fit_the_recording(self):
        wins = film_post.windows(self.events(), anchor=10.0)
        self.assertEqual(film_post.check_windows(wins, duration=200.0), [])
        problems = film_post.check_windows(wins, duration=100.0)
        self.assertEqual(len(problems), 1)
        self.assertIn("segment 3", problems[0])
        self.assertTrue(film_post.check_windows([(1, -2.0, 18.0)], duration=100.0))

    def test_the_anchor_guess_is_the_first_big_change_after_a_quiet_start(self):
        times = [i / 30 for i in range(300)]
        vectors = [[10.0] * 64 for _ in times]
        for i in range(120, 300):
            vectors[i] = [200.0] * 64                                                     # the screen changes at 4.0 s
        anchor = film_post.guess_anchor(times, vectors)
        self.assertAlmostEqual(anchor, 4.0, delta=0.1)
        self.assertIsNone(film_post.guess_anchor(times[:100], vectors[:100]))

    @unittest.skipIf(av is None, "PyAV or Pillow is not installed")
    def test_concat_keeps_the_speed_of_a_generated_video(self):
        with tempfile.TemporaryDirectory() as folder:
            src, dst = os.path.join(folder, "in.mp4"), os.path.join(folder, "out.mp4")
            with av.open(src, "w", format="mp4") as container:
                stream = container.add_stream("mpeg4", rate=20)
                stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
                for i in range(200):                                                      # 10 s, brightness grows with time
                    grey = min(255, i + 20)
                    for packet in stream.encode(av.VideoFrame.from_image(Image.new("RGB", (64, 48), (grey, grey, grey)))):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            count = film_post.concat(src, [(1, 1.0, 3.0), (2, 6.0, 8.0)], dst)
            self.assertAlmostEqual(count, 80, delta=3)                                    # 2 x 2 s at 20 fps
            with av.open(dst) as out:
                self.assertAlmostEqual(float(out.duration) / 1e6, 4.0, delta=0.2)         # 4 s: nothing sped up or slowed down
                greys = [f.to_image().convert("L").getpixel((32, 24)) for f in out.decode(out.streams.video[0])]
            self.assertLess(greys[0], 60)                                                 # starts at the first window (t = 1 s: grey ~40)
            self.assertGreater(greys[-1], 130)                                            # ends in the second window (t = 8 s: grey ~180)


if __name__ == "__main__":
    unittest.main()
