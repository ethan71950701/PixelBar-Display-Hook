"""DRAFT, not run on any video yet: cut the "playing" passes out of a phone recording of a filming run, in order.

Usage (nothing is written unless --write is given):
    python tools/film_post.py RECORDING.mp4 film_log.jsonl [--anchor SECONDS] [--lead 0] [--trim 0] [--write OUT.mp4]

Input: the recording (normal video mode; NOT slow motion) and the log of `pixelbar film run`, which has the
wall-clock time of every upload start and end. The log gives the order and the length of everything; the video has to be
tied to it with one known moment, the ANCHOR: the second of the recording at which the first upload started. The
speaker shows an "uploading" picture then (what it looks like is not known yet); --anchor gives that second by hand, and
without it the script prints a GUESS (the first big change on the screen after the recording starts) which has to be
checked by looking at the video at that second before anything is cut.

Output: for every upload the window of the recording in which the segment was played (from the end of the upload to the
end of the play, 20 s = 20 pictures at 1 s each), --lead seconds later at the start and --trim seconds earlier at the end
if the edges need it. The windows are joined in order. The speed is never changed: frames keep their time spacing inside a
window and the windows follow each other.
"""
import argparse
import datetime
import json
import os
import sys

PLAY_SECONDS = 20.0

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def read_log(path):
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def uploads(events):
    """[{segment, start, end, ok}] in order; start and end are seconds on the log's own clock ("t")."""
    starts, out = {}, []
    for e in events:
        if e["event"] == "upload_start":
            starts[e["segment"]] = e
        elif e["event"] == "upload_end" and e["segment"] in starts:
            out.append({"segment": e["segment"], "start": starts[e["segment"]]["t"], "end": e["t"], "ok": e["ok"],
                        "start_wall": starts[e["segment"]]["wall"], "end_wall": e["wall"]})
    return out


def windows(events, anchor, play=PLAY_SECONDS, lead=0.0, trim=0.0):
    """[(segment, video_start, video_end)] of the played passes.

    Video time = anchor + (log time - log time of the first upload start), so the anchor is the video second of that start.
    """
    every = uploads(events)
    ups = [u for u in every if u["ok"]]
    if not ups:
        return []
    zero = every[0]["start"]
    out = []
    for u in ups:
        begin = anchor + (u["end"] - zero) + lead
        end = anchor + (u["end"] - zero) + play - trim
        if end <= begin:
            raise ValueError("--lead and --trim leave nothing of a pass")
        out.append((u["segment"], round(begin, 3), round(end, 3)))
    for (_, _, e1), (_, b2, _) in zip(out, out[1:]):
        if b2 < e1:
            raise ValueError("two passes overlap: the wait between the uploads is shorter than the play")
    return out


def guess_anchor(times, vectors, quiet=1.0):
    """GUESS: the first big jump of the screen picture after the recording has been quiet for `quiet` seconds."""
    import screen_timing as st
    diffs = st.frame_differences(vectors)
    for t in st.change_events(times, diffs):
        if t >= times[0] + quiet:
            return t
    return None


def check_windows(wins, duration):
    problems = []
    for seg, b, e in wins:
        if b < 0:
            problems.append(f"segment {seg}: starts before the recording ({b:.1f} s)")
        if duration is not None and e > duration + 1e-6:
            problems.append(f"segment {seg}: ends after the recording ({e:.1f} s of {duration:.1f} s)")
    return problems


def concat(video_path, wins, out_path):
    """Write the windows one after the other into out_path (frames re-encoded, their spacing kept, nothing sped up)."""
    import av
    with av.open(video_path) as src, av.open(out_path, "w") as dst:
        stream = src.streams.video[0]
        rate = stream.average_rate or 30
        out_stream = dst.add_stream("mpeg4", rate=rate)
        out_stream.width, out_stream.height, out_stream.pix_fmt = stream.codec_context.width, stream.codec_context.height, "yuv420p"
        index = 0
        for frame in src.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base)
            if not any(b <= t < e for _, b, e in wins):
                continue
            new = av.VideoFrame.from_image(frame.to_image())
            new.pts = index
            new.time_base = 1 / rate
            index += 1
            for packet in out_stream.encode(new):
                dst.mux(packet)
        for packet in out_stream.encode():
            dst.mux(packet)
    return index


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("log")
    ap.add_argument("--anchor", type=float, default=None, help="second of the recording at which the first upload started")
    ap.add_argument("--lead", type=float, default=0.0)
    ap.add_argument("--trim", type=float, default=0.0)
    ap.add_argument("--write", metavar="OUT.mp4", default=None)
    args = ap.parse_args(argv)
    events = read_log(args.log)
    import av
    with av.open(args.video) as c:
        duration = float(c.duration) / 1e6 if c.duration else None
    anchor, guessed = args.anchor, False
    if anchor is None:
        import screen_timing as st
        times, vectors = st.read_features(args.video, 0, None, None, None)
        anchor, guessed = guess_anchor(times, vectors), True
        if anchor is None:
            sys.exit("no anchor found: give --anchor SECONDS")
        print(f"GUESSED anchor {anchor:.2f} s (first big change on the screen); look at the video there and give --anchor to confirm")
    wins = windows(events, anchor, lead=args.lead, trim=args.trim)
    for seg, b, e in wins:
        print(f"segment {seg}: video {b:8.2f} s - {e:8.2f} s ({e - b:.1f} s)")
    problems = check_windows(wins, duration)
    for p in problems:
        print("PROBLEM: " + p)
    if args.write:
        if guessed or problems:
            sys.exit("not writing: confirm the anchor with --anchor and fix the problems first")
        print(f"wrote {concat(args.video, wins, args.write)} frames to {args.write}")
    else:
        print("plan only: nothing was written (add --write OUT.mp4 with a confirmed --anchor)")


if __name__ == "__main__":
    main()
