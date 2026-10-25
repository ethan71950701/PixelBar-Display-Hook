"""tools/screen_timing.py on synthetic data and on a small generated video (no device, no real recording)."""
import os
import statistics
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import screen_timing as st  # noqa: E402

try:
    import av
    from PIL import Image
except ImportError:
    av = None


def synthetic(fps=30.0, seconds=8.0, pictures=10, hold=0.2):
    """Feature vectors of a screen that shows `pictures` different pictures in turn, each for `hold` seconds."""
    n = int(seconds * fps)
    times = [i / fps for i in range(n)]
    vectors = []
    for t in times:
        k = int(t / hold) % pictures
        vectors.append([200.0 if c == k else 10.0 for c in range(16 * 4)])
    return times, vectors


class SyntheticTests(unittest.TestCase):
    def test_changes_and_loop_of_a_cycling_screen(self):
        times, vectors = synthetic()
        events = st.change_events(times, st.frame_differences(vectors))
        gaps = [b - a for a, b in zip(events, events[1:])]
        self.assertAlmostEqual(statistics.median(gaps), 0.2, delta=0.04)
        loop, score = st.loop_seconds(times, vectors)
        self.assertAlmostEqual(loop, 2.0, delta=0.07)
        self.assertLess(score, 1.0)

    def test_a_still_screen_has_no_changes(self):
        times = [i / 30 for i in range(120)]
        vectors = [[50.0] * 64 for _ in times]
        self.assertEqual(st.change_events(times, st.frame_differences(vectors)), [])
        self.assertIn("0 picture changes", "\n".join(st.report(times, vectors)))

    def test_too_short_videos_do_not_crash(self):
        self.assertEqual(st.report([0.0, 0.1], [[0] * 64] * 2), ["the video is too short"])
        self.assertEqual(st.loop_seconds([0.0, 0.1, 0.2], [[0] * 64] * 3), (None, None))

    def test_a_slower_screen_gives_longer_intervals(self):
        for hold in (0.1, 0.4):
            times, vectors = synthetic(fps=60, seconds=10, pictures=8, hold=hold)
            events = st.change_events(times, st.frame_differences(vectors))
            gaps = [b - a for a, b in zip(events, events[1:])]
            self.assertAlmostEqual(statistics.median(gaps), hold, delta=0.03, msg=hold)


@unittest.skipIf(av is None, "PyAV or Pillow is not installed")
class VideoTests(unittest.TestCase):
    def test_a_generated_video_is_measured(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "screen.mp4")
            fps = 30
            with av.open(path, "w", format="mp4") as container:
                stream = container.add_stream("mpeg4", rate=fps)
                stream.width, stream.height, stream.pix_fmt = 160, 90, "yuv420p"
                for i in range(fps * 6):
                    k = int(i / fps / 0.25) % 8                      # eight pictures, 0.25 s each: a loop of 2 s
                    img = Image.new("L", (160, 90), 20)
                    for x in range(k * 20, k * 20 + 20):
                        for y in range(90):
                            img.putpixel((x, y), 230)
                    for packet in stream.encode(av.VideoFrame.from_image(img.convert("RGB"))):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            times, vectors = st.read_features(path)
            text = "\n".join(st.report(times, vectors))
            events = st.change_events(times, st.frame_differences(vectors))
            gaps = [b - a for a, b in zip(events, events[1:])]
            self.assertAlmostEqual(statistics.median(gaps), 0.25, delta=0.05, msg=text)
            loop, _ = st.loop_seconds(times, vectors)
            self.assertAlmostEqual(loop, 2.0, delta=0.1, msg=text)


if __name__ == "__main__":
    unittest.main()
