"""DRAFT, not run on any video yet: cut the "playing" passes out of a phone recording of a filming run, in order.

Usage (nothing is written unless --write is given):
    python tools/film_post.py RECORDING.mp4 film_log.jsonl [--anchor SECONDS] [--lead 0] [--trim 0] [--write OUT.mp4]

Input: the recording (normal video mode; NOT slow motion) and the log of `pixelbar film run`, which has the
wall-clock time of every upload start and end. The log gives the order and the length of everything; the video has to be
tied to it with one known moment, the ANCHOR: the second of the recording at which the first upload started. The
speaker shows an "uploading" picture then (what it looks like is not known yet); --anchor gives that second by hand, and
without it the script prints a GUESS (the first big change on the screen after the recording starts) which has to be
checked by looking at the video at that second before anything is cut.

What the first trial showed (docs/protocol.md): the speaker shows the text 「图片上传中...」 (the dots move) during an upload.
The first upload, which replaced a plain text, showed it about 0.45 s AFTER the log's upload start, the later ones (scene
replaced by scene) at the log's time within 0.1 s; so the anchor read from the first appearance of that text is about 0.45 s
too late. The passes are the better reference: each lasts exactly 20 pictures x 1.0 s from the end of its upload, and the
picture changes of a pass fall on a 1-second grid that tells the true start to about 0.02 s (see the trial in the docs).

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


def read_logs(paths):
    """The events of several logs (a run that was stopped and continued), one after the other."""
    events = []
    for path in paths:
        events += read_log(path)
    return events


def seconds_of(event):
    """The wall-clock time of an event in seconds (the computer's clock, the same in every log); the log's own offset if the
    wall clock text cannot be read (synthetic data)."""
    try:
        return datetime.datetime.fromisoformat(event["wall"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return event["t"]


def uploads(events):
    """[{segment, start, end, ok}] sorted by start; start and end are seconds on the wall clock (see seconds_of)."""
    starts, out = {}, []
    for e in events:
        if e["event"] == "upload_start":
            starts[e["segment"]] = e
        elif e["event"] == "upload_end" and e["segment"] in starts:
            out.append({"segment": e["segment"], "start": seconds_of(starts[e["segment"]]), "end": seconds_of(e), "ok": e["ok"],
                        "start_wall": starts[e["segment"]]["wall"], "end_wall": e["wall"]})
    return sorted(out, key=lambda u: u["start"])


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


def mux_music(dst, out, music_path, start, seconds, lead=0.0):
    """Encode `lead` seconds of silence, then the audio of `music_path` from `start` for `seconds`, into the audio stream `out`."""
    import av
    from fractions import Fraction
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=out.rate)
    quiet, written = int(round(lead * out.rate)), 0
    while written < quiet:                                            # the opening (before the first picture): no sound
        piece = av.AudioFrame(format="fltp", layout="stereo", samples=1024)
        piece.sample_rate = out.rate
        for plane in piece.planes:
            plane.update(bytes(plane.buffer_size))
        piece.pts, piece.time_base = written, Fraction(1, out.rate)
        written += 1024
        for packet in out.encode(piece):
            dst.mux(packet)
    needed = written + int(round(seconds * out.rate))
    with av.open(music_path) as msrc:
        a = msrc.streams.audio[0]
        if start > 0:
            msrc.seek(int(start / float(a.time_base)), stream=a, backward=True)
        for frame in msrc.decode(a):
            if frame.pts is not None and float(frame.pts * a.time_base) + frame.samples / frame.sample_rate <= start:
                continue
            for piece in resampler.resample(frame):
                piece.pts, piece.time_base = written, Fraction(1, out.rate)
                written += piece.samples
                for packet in out.encode(piece):
                    dst.mux(packet)
            if written >= needed:
                break
        for packet in out.encode():
            dst.mux(packet)
    return written / out.rate


def compose(top, mv_image, size):
    """The recording picture on top and the original video's picture below it (fitted into the same size, black bars)."""
    from PIL import Image
    w, h = size
    out = Image.new("RGB", (w, 2 * h), (0, 0, 0))
    out.paste(top if top.size == size else top.resize(size), (0, 0))
    scale = min(w / mv_image.width, h / mv_image.height)
    fitted = mv_image.resize((max(1, round(mv_image.width * scale)), max(1, round(mv_image.height * scale))))
    out.paste(fitted, ((w - fitted.width) // 2, h + (h - fitted.height) // 2))
    return out


def mv_picture_source(mv_path, start):
    """A function t -> PIL image of the original video at second `start + t` (the last frame at or before it; t must not go back)."""
    import av
    container = av.open(mv_path)
    stream = container.streams.video[0]
    if start > 0:
        container.seek(int(start / float(stream.time_base)), stream=stream, backward=True)
    frames = container.decode(stream)
    state = {"current": None, "next": None, "image": None}

    def stamp(frame):
        return float(frame.pts * stream.time_base) - start

    def at(t):
        if state["next"] is None:
            state["next"] = next(frames, None)
        moved = False
        while state["next"] is not None and stamp(state["next"]) <= t + 1e-6:
            state["current"], state["next"] = state["next"], next(frames, None)
            moved = True
        if state["current"] is None:                          # t before the first frame (a keyframe seek lands a little early or late)
            state["current"] = state["next"]
            moved = True
        if moved and state["current"] is not None:
            state["image"] = state["current"].to_image().convert("RGB")
        return state["image"]

    at.close = container.close
    return at


def concat(video_path, wins, out_path, music=None, music_start=0.0, mv_below=None, lead=0.0):
    """Write the windows one after the other into out_path (frames re-encoded, their spacing kept, nothing sped up).

    The sound of the recording is never copied. With `music` (a video or audio file) the sound of that file from `music_start`
    seconds on, as long as the joined windows, is put under the pictures: for 1 picture per second of a video, segment 1..n in
    order, that is the original music of the same seconds. With `mv_below` (a video file) its picture is placed under the
    recording's, playing continuously from `music_start` in step with that sound (so the picture, the original video and the music
    are one clock: the picture of every second is the original's frame at the start of that second).
    `lead` seconds at the beginning of the output (an opening before the first picture) have no music and show the first picture of the
    original video, which then plays from the first window on.
    Each window is reached by seeking to the keyframe before it, so a long recording is not decoded from its start every time.
    """
    import av
    with av.open(video_path) as src, av.open(out_path, "w") as dst:
        stream = src.streams.video[0]
        rate = stream.average_rate or 30
        width, height = stream.codec_context.width, stream.codec_context.height
        try:
            out_stream = dst.add_stream("libx264", rate=rate)            # preferred: good quality at a sane size
        except Exception:
            out_stream = dst.add_stream("mpeg4", rate=rate)
            out_stream.bit_rate = 8_000_000                               # mpeg4 refuses to open at 1280x720 without one
        out_stream.width, out_stream.height, out_stream.pix_fmt = width, height * (2 if mv_below else 1), "yuv420p"
        audio_out = None
        if music:
            audio_out = dst.add_stream("aac", rate=44100)
            audio_out.layout = "stereo"
        mv_at = mv_picture_source(mv_below, music_start) if mv_below else None
        index = 0
        for _, b, e in wins:
            src.seek(max(0, int((b - 2.0) / float(stream.time_base))), stream=stream, backward=True)
            for frame in src.decode(stream):
                if frame.pts is None:
                    continue
                t = float(frame.pts * stream.time_base)
                if t < b:
                    continue
                if t >= e:
                    break
                picture = frame.to_image()
                if mv_at is not None:
                    picture = compose(picture.convert("RGB"), mv_at(index / float(rate) - lead), (width, height))
                new = av.VideoFrame.from_image(picture)
                new.pts = index
                new.time_base = 1 / rate
                index += 1
                for packet in out_stream.encode(new):
                    dst.mux(packet)
        for packet in out_stream.encode():
            dst.mux(packet)
        if mv_at is not None:
            mv_at.close()
        if audio_out is not None:
            mux_music(dst, audio_out, music, music_start, sum(e - b for _, b, e in wins) - lead, lead)
    return index


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("logs", nargs="+", help="the film log(s): several when a run was stopped and continued")
    ap.add_argument("--anchor", type=float, default=None, help="second of the recording at which the first upload started")
    ap.add_argument("--lead", type=float, default=0.0)
    ap.add_argument("--trim", type=float, default=0.0)
    ap.add_argument("--write", metavar="OUT.mp4", default=None)
    ap.add_argument("--music", metavar="MV.mp4", default=None,
                    help="put the sound of this file under the pictures (the sound of the recording is always dropped)")
    ap.add_argument("--music-start", type=float, default=0.0, help="second of the music file where the first picture is (default 0)")
    ap.add_argument("--intro-start", type=float, default=None, metavar="SECONDS",
                    help="keep the recording from this second up to the first picture as an opening (silent, the original video on its first picture)")
    ap.add_argument("--mv-below", metavar="MV.mp4", default=None,
                    help="put the original video under the recording's picture, playing in step with the music (from --music-start)")
    args = ap.parse_args(argv)
    events = read_logs(args.logs)
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
    lead = 0.0
    if args.intro_start is not None and wins:
        if not 0 <= args.intro_start < wins[0][1]:
            sys.exit("--intro-start must be before the first picture")
        wins = [(0, args.intro_start, wins[0][1])] + wins
        lead = wins[0][2] - wins[0][1]
        print(f"opening: video {wins[0][1]:8.2f} s - {wins[0][2]:8.2f} s ({lead:.1f} s), no music, the original video on its first picture")
    problems = check_windows(wins, duration)
    for p in problems:
        print("PROBLEM: " + p)
    if args.write:
        if guessed or problems:
            sys.exit("not writing: confirm the anchor with --anchor and fix the problems first")
        print(f"wrote {concat(args.video, wins, args.write, args.music, args.music_start, args.mv_below, lead)} frames to {args.write}")
    else:
        print("plan only: nothing was written (add --write OUT.mp4 with a confirmed --anchor)")


if __name__ == "__main__":
    main()
