"""Measure how fast the speaker's screen changes pictures, from a phone video of the screen (read-only).

Film the screen for 10-20 seconds (steady, the screen filling most of the picture, 60 fps or slow
motion is better than 30 fps), save the video outside the repository and run

    python tools/screen_timing.py video.mp4 [--start 1.0] [--length 15]

It decodes the video (PyAV), shrinks every frame, and reports
  * the moments the picture changes (jumps in the difference between neighbouring frames),
    their intervals, and
  * the loop length: the lag at which the picture sequence best repeats itself.
The camera's frame rate limits the precision (one frame = 1/fps). Nothing here talks to the device.
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

GRID = (16, 4)       # features per frame: the mean brightness of each cell of a 16x4 grid
MIN_LOOP_SECONDS = 0.3


def features(gray, grid=GRID):
    """Mean brightness of each cell of `grid` (columns, rows) of a small greyscale Pillow image."""
    cols, rows = grid
    small = gray.resize((cols, rows))
    return list(small.getdata())


def frame_differences(vectors):
    return [0.0] + [sum(abs(a - b) for a, b in zip(v, w)) / len(v) for v, w in zip(vectors, vectors[1:])]


def change_events(times, diffs, floor=1.0):
    """Times at which the picture changes: runs of frames whose difference is far above the usual noise."""
    body = diffs[1:]
    if not body:
        return []
    noise = statistics.median(body)
    spread = statistics.median(abs(d - noise) for d in body) or 0.0
    threshold = max(floor, noise + 6 * spread, 3 * noise)
    events, inside = [], False
    for t, d in zip(times, diffs):
        if d > threshold and not inside:
            events.append(t)
        inside = d > threshold
    return events


def loop_seconds(times, vectors, min_seconds=MIN_LOOP_SECONDS):
    """The lag (seconds) at which frame i looks most like frame i + lag, over the whole video; None if too short."""
    n = len(vectors)
    if n < 8 or times[-1] - times[0] < 2 * min_seconds:
        return None, None
    fps = (n - 1) / (times[-1] - times[0])
    lo, hi = max(2, int(min_seconds * fps)), n // 2
    best = None
    for lag in range(lo, hi + 1):
        total = 0.0
        for i in range(0, n - lag, 2):
            v, w = vectors[i], vectors[i + lag]
            total += sum(abs(a - b) for a, b in zip(v, w)) / len(v)
        score = total / len(range(0, n - lag, 2))
        if best is None or score < best[0]:
            best = (score, lag)
    return best[1] / fps, best[0]


def lit_only(gray, bright):
    """Keep only the very bright points (the screen's lit pixels); reflections and room light are darker."""
    return gray.point(lambda v: 255 if v >= bright else 0) if bright else gray


def read_features(path, start=0.0, length=None, crop=None, bright=None):
    """crop = (x0, y0, x1, y1) as fractions of the picture: only that part (the screen) is looked at."""
    import av
    times, vectors = [], []
    with av.open(path) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base)
            if t < start:
                continue
            if length is not None and t > start + length:
                break
            times.append(t)
            img = frame.to_image().convert("L")
            if crop:
                w, h = img.size
                img = img.crop((int(crop[0] * w), int(crop[1] * h), int(crop[2] * w), int(crop[3] * h)))
            vectors.append(features(lit_only(img, bright)))
    return times, vectors


def report(times, vectors):
    lines = []
    if len(times) < 3:
        return ["the video is too short"]
    fps = (len(times) - 1) / (times[-1] - times[0])
    diffs = frame_differences(vectors)
    events = change_events(times, diffs)
    lines.append(f"{len(times)} frames, {times[-1] - times[0]:.2f} s, camera about {fps:.1f} fps (precision about {1000 / fps:.0f} ms)")
    if len(events) >= 2:
        gaps = [b - a for a, b in zip(events, events[1:])]
        lines.append(f"{len(events)} picture changes; interval median {statistics.median(gaps) * 1000:.0f} ms, "
                     f"min {min(gaps) * 1000:.0f} ms, max {max(gaps) * 1000:.0f} ms")
    else:
        lines.append(f"{len(events)} picture changes found: the screen may not be changing, or it is not clear in the video")
    loop, score = loop_seconds(times, vectors)
    if loop is None:
        lines.append("loop length: video too short to tell")
    else:
        lines.append(f"loop length: about {loop:.2f} s (best repeat, remaining difference {score:.1f} of 255; a small number means a clear loop)")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0.0, help="seconds to skip at the beginning")
    ap.add_argument("--length", type=float, default=None, help="seconds to analyse (default: to the end)")
    ap.add_argument("--crop", default=None, help="x0,y0,x1,y1 as fractions 0..1 of the picture: only the screen part (recommended)")
    ap.add_argument("--bright", type=int, default=None, help="0..255: look only at points at least this bright (the lit pixels)")
    ap.add_argument("--slow", type=float, default=1.0, help="the video was filmed in slow motion and plays this many times slower than real "
                    "time (e.g. 4 for 120 fps shown at 30 fps): all times are divided by it, so they are real seconds")
    args = ap.parse_args(argv)
    if args.slow <= 0:
        sys.exit("--slow must be positive")
    crop = None
    if args.crop:
        try:
            crop = tuple(float(v) for v in args.crop.split(","))
            assert len(crop) == 4 and 0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1
        except (ValueError, AssertionError):
            sys.exit("--crop must be x0,y0,x1,y1 as fractions between 0 and 1, e.g. 0.3,0.25,0.7,0.45")
    try:
        times, vectors = read_features(args.video, args.start, args.length, crop, args.bright)
    except ImportError:
        sys.exit("PyAV is not installed: pip install av")
    except Exception as err:
        sys.exit(f"cannot read the video: {err}")
    print("\n".join(report(times, vectors)))


if __name__ == "__main__":
    main()
