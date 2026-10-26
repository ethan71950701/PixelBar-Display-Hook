"""Filming a long video on the speaker, 20 pictures at a time: frames, plan, capture run (sending is NOT enabled yet).

The speaker shows each picture for a time set by the record header XX XX (about 2 ms per unit from 100 up, measured up
to 500 = 1.0 s per picture), so a video played at 1 picture per second is built from the device itself, not from
editing. One upload holds at most 20 pictures (the most tried so far), so a long video is cut into segments of 20; each
segment is one upload, played for 20 s while a phone films the screen in the normal video mode; tools/film_post.py cuts
the upload and buffer parts out of the recording afterwards.

Commands (python -m pixelbar film ...):
  frames VIDEO   decode the video at 1 picture per second, convert every picture to 256x32 black and white (fit, invert,
                 threshold are options), write PNG files and a contact sheet per segment to a folder OUTSIDE the repository
  plan           print only: segments, frame ranges, time per segment, the upload quota needed and what is missing
  run            the capture sequence: --rehearse prints the timeline on a virtual clock and sends nothing;
                 --send is refused in this version (SEND_ENABLED is False) until the user approves a 3-segment trial

The pure parts (segments, plan, run_film, confirm_filming) take everything they touch as arguments, so the tests use fake
uploads and fake clocks. Nothing here sends anything unless SEND_ENABLED is changed in the code.
"""
import datetime
import glob
import json
import math
import os
import sys
import time

from . import device, image, image_send, limits, prepare, upload, upload_replay, video

SEND_ENABLED = False                  # sending needs the user's approval of the plan and the previews first
DEFAULT_DIR = r"C:\pixelbar-raw\badapple"          # outside the repository: video, frames, sheets, the log
SEGMENT = 20                          # pictures per upload (the most tried on the speaker)
XX = 500                              # 0x01f4: 1.0 s per picture (measured once, upload 22)
SECONDS_PER_PICTURE = 1.0
UPLOAD_SECONDS_FULL = 23.3            # measured for 20 pictures (upload 22: 23.27 s)
BUFFER_SECONDS = 2.0
DEFAULT_MAX_UPLOADS = 3
FRAME_NAME = "f_{:04d}.png"


class FilmError(Exception):
    pass


# ---- plan (pure)
def segments(n_frames, size=SEGMENT):
    """[{index, first, last, pictures}] with 1-based inclusive picture numbers; the last one may be shorter."""
    if n_frames < 1 or size < 1:
        raise FilmError("there are no pictures to plan")
    out = []
    for i, first in enumerate(range(1, n_frames + 1, size), 1):
        last = min(first + size - 1, n_frames)
        out.append({"index": i, "first": first, "last": last, "pictures": last - first + 1})
    return out


def segment_times(seg, xx=XX):
    """Expected seconds of one segment: the upload (scaled from 23.3 s per 20 pictures), the play (one pass) and the buffer."""
    pictures = seg["pictures"]
    upload_s = UPLOAD_SECONDS_FULL * pictures / SEGMENT
    play_s = pictures * xx * 2 / 1000
    return {"upload": upload_s, "play": play_s, "buffer": BUFFER_SECONDS, "total": upload_s + play_s + BUFFER_SECONDS}


def plan(n_frames, used, total_limit, max_uploads=DEFAULT_MAX_UPLOADS, size=SEGMENT, xx=XX):
    """Everything the plan command prints, as data (nothing is read from or written to disk)."""
    segs = segments(n_frames, size)
    rows = []
    clock = 0.0
    for seg in segs:
        t = segment_times(seg, xx)
        rows.append({**seg, **t, "starts_at": clock})
        clock += t["total"]
    available = max(0, total_limit - used)
    needed = len(segs)
    excess = max(0, needed - available)
    trial = min(max_uploads, needed)
    return {"frames": n_frames, "segments": rows, "total_seconds": clock, "used": used, "total_limit": total_limit,
            "available": available, "needed": needed, "excess": excess, "raise_to": used + needed,
            "max_uploads": max_uploads, "trial_uploads": trial, "trial_fits": trial <= available,
            "trial_seconds": sum(r["total"] for r in rows[:trial]), "one_picture_tail": segs[-1]["pictures"] == 1 and len(segs) > 1}


def format_plan(p):
    lines = [f"pictures: {p['frames']}, segments: {len(p['segments'])} (up to {SEGMENT} pictures each), XX XX = 0x{XX:04x} "
             f"({SECONDS_PER_PICTURE:.1f} s per picture)",
             f"{'seg':>3} {'pictures':>12} {'upload':>8} {'play':>6} {'buffer':>7} {'segment':>8} {'starts at':>10}"]
    for r in p["segments"]:
        lines.append(f"{r['index']:>3} {r['first']:>5}-{r['last']:<6} {r['upload']:>7.1f}s {r['play']:>5.0f}s {r['buffer']:>6.1f}s {r['total']:>7.1f}s {r['starts_at']:>9.1f}s")
    lines.append(f"total filming time: {p['total_seconds']:.1f} s ({p['total_seconds'] / 60:.1f} min), the upload times as measured "
                 f"({UPLOAD_SECONDS_FULL} s for 20 pictures), play {SECONDS_PER_PICTURE:.0f} s per picture, buffer {BUFFER_SECONDS:.0f} s")
    lines.append(f"upload quota: {p['used']} of {p['total_limit']} used, {p['available']} left; this plan needs {p['needed']} uploads")
    if p["excess"]:
        lines.append(f"  -> {p['excess']} more than the quota allows. To run everything the total limit in ~/.pixelbar/limits.json has to be "
                     f"at least {p['raise_to']}; only you change that file, this program never does.")
    else:
        lines.append("  -> the quota is enough for the whole plan")
    lines.append(f"a trial of {p['trial_uploads']} segments (--max-uploads {p['max_uploads']}) needs {p['trial_uploads']} uploads "
                 f"({'fits' if p['trial_fits'] else 'DOES NOT fit'} the quota) and takes about {p['trial_seconds']:.0f} s")
    if p["one_picture_tail"]:
        lines.append("note: the last segment has a single picture; XX XX does not apply to one picture (the speaker shows it still)")
    return "\n".join(lines)


# ---- frames (needs PyAV and Pillow)
def frame_files(frames_dir):
    return sorted(glob.glob(os.path.join(frames_dir, "f_*.png")))


def sample_count(duration, fps=1.0):
    return int(math.floor(duration * fps + 1e-9)) + 1


def extract_frames(video_path, out_dir, fps=1.0, fit="contain", invert=True, mode="threshold", threshold=128,
                   progress=lambda text: None):
    """One picture per 1/fps seconds (t = 0, 1, 2, ...), converted to 256x32 black and white and written as PNG files.

    Returns the number of pictures written. Files go to `out_dir`; the video itself is only read.
    """
    info = video.probe(video_path)
    if info["duration"] is None:
        raise FilmError("the video has no known length")
    wanted = sample_count(info["duration"], fps)
    os.makedirs(out_dir, exist_ok=True)
    av = video._av()
    written, k = 0, 0
    with av.open(video_path) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base)
            if t + 1e-6 < k / fps:
                continue
            img = frame.to_image().convert("RGB")
            if max(img.size) > video.MAX_SIDE:
                scale = video.MAX_SIDE / max(img.size)
                img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
            rows = prepare.prepare_image(img, fit=fit, mode=mode, threshold=threshold, invert=invert)
            with open(os.path.join(out_dir, FRAME_NAME.format(k + 1)), "wb") as fh:
                fh.write(image.rows_to_png(rows, 1))
            written, k = written + 1, k + 1
            if written % 20 == 0:
                progress(f"{written} of {wanted} pictures")
            if k >= wanted:
                break
    return written


def write_sheets(frames_dir, sheet_dir, size=SEGMENT, scale=2):
    """One contact sheet (PNG) per segment, to look at before anything is uploaded."""
    files = frame_files(frames_dir)
    os.makedirs(sheet_dir, exist_ok=True)
    out = []
    for seg in segments(len(files), size):
        pictures = [image.png_to_pixels(open(files[i - 1], "rb").read()) for i in range(seg["first"], seg["last"] + 1)]
        path = os.path.join(sheet_dir, f"sheet_seg{seg['index']:02d}_{seg['first']:04d}-{seg['last']:04d}.png")
        with open(path, "wb") as fh:
            fh.write(image.stack_png(pictures, scale))
        out.append(path)
    return out


def write_overview(frames_dir, path, every=4, cols=5):
    """One PNG with every `every`-th picture of the whole video (1 pixel per screen pixel, grey gaps), to see the look at a glance."""
    files = frame_files(frames_dir)[::every]
    if not files:
        raise FilmError("there are no pictures to show")
    pictures = [image.png_to_pixels(open(f, "rb").read()) for f in files]
    rows_n = (len(pictures) + cols - 1) // cols
    gap, w, h = 3, 256, 32
    canvas = [[90] * (cols * (w + gap) + gap) for _ in range(rows_n * (h + gap) + gap)]
    for i, pic in enumerate(pictures):
        top, left = gap + (i // cols) * (h + gap), gap + (i % cols) * (w + gap)
        for y in range(h):
            canvas[top + y][left:left + w] = [255 if v else 0 for v in pic[y]]
    with open(path, "wb") as fh:
        fh.write(image.encode_png_gray(canvas))
    return len(pictures)


def lit_statistics(frames_dir):
    """Share of lit pixels of every picture (0..1), read from the PNG files; changes nothing."""
    out = []
    for path in frame_files(frames_dir):
        rows = image.png_to_pixels(open(path, "rb").read())
        out.append(sum(map(sum, rows)) / (prepare.WIDTH * prepare.HEIGHT))
    return out


def format_statistics(shares, size=SEGMENT):
    if not shares:
        return "no pictures"
    n = len(shares)
    peak = max(range(n), key=lambda i: shares[i])
    over = [i + 1 for i, v in enumerate(shares) if v > 0.25]
    lines = [f"{n} pictures: lit share max {shares[peak] * 100:.1f}% (picture {peak + 1}, {shares[peak] * prepare.WIDTH * prepare.HEIGHT:.0f} of "
             f"{prepare.WIDTH * prepare.HEIGHT} pixels), mean {sum(shares) / n * 100:.1f}%, median {sorted(shares)[n // 2] * 100:.1f}%, "
             f"pictures above 25%: {len(over)}{'' if not over else ' (' + ', '.join(map(str, over[:20])) + ')'}",
             f"completely dark: {sum(1 for v in shares if v == 0)}, above 10%: {sum(1 for v in shares if v > 0.10)}, above 15%: {sum(1 for v in shares if v > 0.15)}",
             f"{'seg':>3} {'pictures':>10} {'max':>7} {'mean':>7}"]
    for seg in segments(n, size):
        part = shares[seg["first"] - 1:seg["last"]]
        lines.append(f"{seg['index']:>3} {seg['first']:>4}-{seg['last']:<5} {max(part) * 100:>6.1f}% {sum(part) / len(part) * 100:>6.1f}%")
    return "\n".join(lines)


def segment_bitmap(files, seg):
    """The bitmap (1024 bytes per picture) of one segment from its PNG files; every file must be a 256x32 black-and-white PNG."""
    data = b""
    for i in range(seg["first"], seg["last"] + 1):
        try:
            with open(files[i - 1], "rb") as fh:
                rows = image.png_to_pixels(fh.read())
        except (OSError, ValueError, IndexError) as err:
            raise FilmError(f"picture {i} cannot be read: {err}") from None
        data += image.pack_pixels(rows)
    return data


# ---- capture run
class Log:
    """JSON lines with the wall-clock time (to the millisecond) and a monotonic offset of every event."""

    def __init__(self, path, now, echo=print):
        self.path, self.now, self.echo = path, now, echo
        self.start = None

    def __call__(self, event, **fields):
        wall, mono = self.now()
        if self.start is None:
            self.start = mono
        record = {"event": event, "wall": wall.isoformat(timespec="milliseconds"), "t": round(mono - self.start, 3), **fields}
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self.echo(f"[{record['t']:8.3f}s] {event} " + " ".join(f"{k}={v}" for k, v in fields.items()))
        return record


def run_film(segs, do_upload, sleep, log, max_uploads=DEFAULT_MAX_UPLOADS, wait_seconds=SEGMENT * SECONDS_PER_PICTURE + BUFFER_SECONDS):
    """Upload the segments one after the other: upload, check every reply, wait for the play and the buffer, next.

    `do_upload(seg)` must return the result dict of upload_replay.run (ok, written, matched, elapsed, reason, ...).
    The first mismatch, timeout or exception stops everything (no retry); at most `max_uploads` uploads are made.
    """
    log("run_start", segments=len(segs), max_uploads=max_uploads, wait_seconds=wait_seconds)
    done = 0
    for seg in segs:
        if done >= max_uploads:
            log("stopped", reason=f"--max-uploads {max_uploads} reached")
            return {"done": done, "stopped": "max-uploads", "ok": True}
        log("upload_start", segment=seg["index"], first=seg["first"], last=seg["last"])
        try:
            result = do_upload(seg)
        except Exception as err:                               # a broken device must not leave the loop running
            log("upload_error", segment=seg["index"], error=repr(err))
            return {"done": done, "stopped": f"error: {err!r}", "ok": False}
        done += 1
        log("upload_end", segment=seg["index"], ok=result["ok"], written=result["written"], matched=result["matched"],
            elapsed=round(result["elapsed"], 3), reason=result["reason"], unsolicited=len(result.get("unsolicited", [])))
        if not result["ok"]:
            log("stopped", reason=f"upload {seg['index']} failed: {result['reason']}")
            return {"done": done, "stopped": result["reason"], "ok": False}
        log("wait_start", segment=seg["index"], seconds=wait_seconds)
        sleep(wait_seconds)
        log("wait_end", segment=seg["index"])
    log("run_end", uploads=done)
    return {"done": done, "stopped": None, "ok": True}


def make_uploader(files, xx=XX, open_device=device.open_device, log=print):
    """do_upload(seg) for run_film: build the plan of one segment, refuse unless every check passes, send it once.

    Not used anywhere while SEND_ENABLED is False; it is the same single bounded upload as `slideshow --send`
    (image_send.send_upload: limits, log, budget, no retry).
    """
    def do_upload(seg):
        bitmap = segment_bitmap(files, seg)
        use = xx if seg["pictures"] > 1 else None
        steps, checks = upload_replay.load_plan(image=bitmap, xx=use)
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise FilmError(f"plan check failed for segment {seg['index']}: {failed}")
        return image_send.send_upload(steps, bitmap, open_device, xx=use, log=log)
    return do_upload


def confirm_filming(ask=input):
    """Both questions must be answered with y before anything is sent."""
    questions = ("Is TempoHub fully closed (also in the system tray)? [y/N] ",
                 "Is the phone recording in the NORMAL video mode (not slow motion) and already running? [y/N] ")
    for question in questions:
        try:
            if ask(question).strip().lower() != "y":
                return False
        except EOFError:
            return False
    return True


# ---- command line
def _state(args):
    total, _ = limits.load_limits()
    used = upload_replay.UploadBudget(limits.state_path(limits.BUDGET_FILE), total).used()
    return used, total


def cmd_frames(args):
    if not os.path.isfile(args.video):
        raise SystemExit(f"error: video not found: {args.video}")
    out = os.path.join(args.dir, "frames")
    existing = frame_files(out)
    if existing and not args.overwrite:
        raise SystemExit(f"error: {len(existing)} pictures are already in {out}; use --overwrite to replace them")
    for path in existing:
        os.remove(path)
    try:
        n = extract_frames(args.video, out, fps=1.0, fit=args.fit, invert=not args.no_invert, mode=args.mode,
                           threshold=args.threshold, progress=print)
        sheets = write_sheets(out, os.path.join(args.dir, "preview"))
        write_overview(out, os.path.join(args.dir, "preview", "overview_every4th.png"))
    except (video.VideoError, prepare.PrepareError, FilmError) as err:
        raise SystemExit(f"error: {err}")
    print(f"{n} pictures (1 per second) written to {out}; {len(sheets)} contact sheets (one per segment of {SEGMENT}) and "
          f"overview_every4th.png in {os.path.join(args.dir, 'preview')}. Look at them before anything is uploaded. Nothing was sent.")


def cmd_stats(args):
    shares = lit_statistics(os.path.join(args.dir, "frames"))
    if not shares:
        raise SystemExit("error: no pictures found; run `film frames` first")
    print(format_statistics(shares))
    print("statistics only: nothing was changed or sent")


def cmd_plan(args):
    n = args.pictures if args.pictures else len(frame_files(os.path.join(args.dir, "frames")))
    if not n:
        raise SystemExit("error: no pictures found; run `film frames` first or give --pictures N")
    used, total = _state(args)
    print(format_plan(plan(n, used, total, args.max_uploads)))
    print("this is a plan only: nothing was sent and no limit was changed")


def cmd_run(args):
    if args.send and not SEND_ENABLED:
        raise SystemExit("sending is not enabled in this version: confirm the plan and the previews first, then the 3-segment trial is "
                         "enabled in a separate step. Nothing was sent. Use --rehearse to see the timeline.")
    if args.send and args.rehearse:
        raise SystemExit("--send and --rehearse contradict each other")
    files = frame_files(os.path.join(args.dir, "frames"))
    n = args.pictures or len(files)
    if n < 1:
        raise SystemExit("error: no pictures: run `film frames` first or give --pictures N for a rehearsal")
    used, total = _state(args)
    p = plan(n, used, total, args.max_uploads)
    print(format_plan(p))
    segs = segments(n)                      # run_film itself stops at --max-uploads
    if not args.rehearse:
        raise SystemExit("choose --rehearse (prints the timeline, sends nothing)")
    if not p["trial_fits"]:
        print(f"note: the trial of {p['trial_uploads']} uploads does not fit the quota ({p['available']} left); a real run would be refused")
    clock = {"t": 0.0}
    start = datetime.datetime(2000, 1, 1)

    def now():
        return start + datetime.timedelta(seconds=clock["t"]), clock["t"]

    def fake_upload(seg):
        clock["t"] += segment_times(seg)["upload"]
        return {"ok": True, "written": 0, "matched": 0, "elapsed": segment_times(seg)["upload"], "reason": None, "unsolicited": []}

    def fake_sleep(seconds):
        clock["t"] += seconds

    print("\nREHEARSAL on a virtual clock (no device, no waiting, no log file); the real run would ask: " + " / ".join(
        q.rstrip(" [y/N]") for q in ("Is TempoHub fully closed?", "Is the phone recording in the normal video mode?")))
    run_film(segs, fake_upload, fake_sleep, Log(None, now), args.max_uploads)
    print("rehearsal done: nothing was sent")


def add_arguments(sub):
    film = sub.add_parser("film", help="Bad Apple style filming: frames, plan, capture run (sending not enabled yet)")
    fs = film.add_subparsers(dest="film_command", required=True)
    f = fs.add_parser("frames", help="decode a video at 1 picture per second into 256x32 black-and-white PNG files")
    f.add_argument("video")
    f.add_argument("--dir", default=DEFAULT_DIR, help=f"folder for frames, previews and the log (default {DEFAULT_DIR}; keep it outside the repository)")
    f.add_argument("--fit", choices=prepare.FITS, default="contain")
    f.add_argument("--no-invert", action="store_true", help="the pictures are inverted by default (dark picture parts become lit)")
    f.add_argument("--mode", choices=prepare.MODES, default="threshold")
    f.add_argument("--threshold", type=int, default=prepare.DEFAULT_THRESHOLD)
    f.add_argument("--overwrite", action="store_true")
    st = fs.add_parser("stats", help="share of lit pixels per picture (max, mean, how many above 25%); changes nothing")
    st.add_argument("--dir", default=DEFAULT_DIR)
    p = fs.add_parser("plan", help="print the segments, times and the upload quota needed (changes nothing)")
    r = fs.add_parser("run", help="the capture sequence (--rehearse only in this version)")
    for q in (p, r):
        q.add_argument("--dir", default=DEFAULT_DIR)
        q.add_argument("--pictures", type=int, default=0, help="plan for N pictures instead of counting the PNG files")
        q.add_argument("--max-uploads", type=int, default=DEFAULT_MAX_UPLOADS, help=f"hard limit of uploads in one run (default {DEFAULT_MAX_UPLOADS})")
    r.add_argument("--rehearse", action="store_true", help="print the timeline on a virtual clock; sends nothing")
    r.add_argument("--send", action="store_true", help="NOT ENABLED in this version")


def cli(args):
    if args.film_command == "frames":
        return cmd_frames(args)
    if args.film_command == "stats":
        return cmd_stats(args)
    if args.film_command == "plan":
        return cmd_plan(args)
    return cmd_run(args)
