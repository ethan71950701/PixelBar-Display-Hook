"""python -m pixelbar text "Hello" --color 336699 --send
python -m pixelbar image picture.jpg            (preview and dry run; needs Pillow)
python -m pixelbar prepare picture.jpg out.png  (only convert to a 256x32 PNG)

Default is a dry run: frames are built, printed and compared with frames
captured from TempoHub; nothing touches the device. --send writes them, after
you confirm TempoHub is closed. Text must fit one frame (hard error otherwise).
`image --send` uploads a picture as capture 20's scene, within the upload limits
(see pixelbar/limits.py), once, with the bounded exchange of stage 2.
"""
import argparse
import json
import os
import sys

from . import device, frames, image, image_send, limits, prepare, testanim, upload, upload_replay

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "captured_frames.json")
PREVIEW_DIR = r"C:\pixelbar-raw\preview"   # outside the repository


def captured_matches(frame):
    try:
        with open(FIXTURE, encoding="utf-8") as fh:
            known = json.load(fh)
    except OSError:
        return []
    return [k["capture"] for k in known if bytes.fromhex(k["hex"]) == frame]


def show(label, frame):
    used = frame.rstrip(b"\x00").hex()
    print(f"{label}: {used} (+ zero padding to {len(frame)} bytes)")
    hits = captured_matches(frame)
    print("  identical to a captured TempoHub frame: " + (", ".join(hits) if hits else "no (not captured, built from the documented format)"))


def confirm_tempohub_closed(args):
    print("Reminder: TempoHub must be fully closed (also in the system tray) while sending.")
    if args.yes_i_closed_tempohub:
        return
    if sys.stdin.isatty():
        try:
            if input("Is TempoHub closed? [y/N] ").strip().lower() == "y":
                return
        except EOFError:
            pass
    sys.exit("not confirmed (use --yes-i-closed-tempohub when not interactive); nothing was sent")


def transmit(label, frame, expected):
    reply = device.send_frame(frame)
    if not reply:
        sys.exit(f"{label}: no reply within {device.READ_TIMEOUT_MS} ms; stopping, nothing further was sent")
    print(f"{label} reply: " + reply.rstrip(b"\x00").hex())
    if reply != expected:
        sys.exit(f"{label}: UNEXPECTED reply (differs from what TempoHub got); stopping, nothing further was sent")
    print("  as expected (same as TempoHub got)")


def add_send_options(p):
    p.add_argument("--dry-run", action="store_true", help="default; nothing is sent without --send")
    p.add_argument("--send", action="store_true", help="really write to the device")
    p.add_argument("--yes-i-closed-tempohub", action="store_true",
                   help="skip the interactive 'TempoHub closed?' question")


def add_prepare_options(p):
    p.add_argument("--fit", choices=prepare.FITS, default="cover",
                   help="cover (default) fills the screen and crops the excess; contain keeps the whole picture with black bars; stretch distorts")
    p.add_argument("--focus-x", type=float, default=0.5, help="0..1, which part is kept horizontally when cover crops (default 0.5)")
    p.add_argument("--focus-y", type=float, default=0.5, help="0..1, which part is kept vertically when cover crops (default 0.5)")
    p.add_argument("--mode", choices=prepare.MODES, default="dither", help="dither (Floyd-Steinberg, default) or threshold")
    p.add_argument("--threshold", type=int, default=prepare.DEFAULT_THRESHOLD, help="0..255, a pixel is lit from this brightness (default 128)")
    p.add_argument("--invert", action="store_true", help="swap light and dark (transparent stays black)")
    p.add_argument("--autocontrast", action="store_true", help="stretch the contrast before the black and white step")


def prepared_rows(args, picture=None):
    try:
        return prepare.prepare_file(picture or args.picture, fit=args.fit, focus_x=args.focus_x, focus_y=args.focus_y, mode=args.mode,
                                    threshold=args.threshold, invert=args.invert, autocontrast=args.autocontrast)
    except prepare.PrepareError as err:
        sys.exit(f"error: {err}")


def cmd_prepare(args):
    rows = prepared_rows(args)
    png = image.rows_to_png(rows, 1)
    if image.png_to_pixels(png) != rows:
        sys.exit("internal error: the saved PNG would not match the prepared image; nothing was written")
    if os.path.exists(args.output) and not args.overwrite:
        sys.exit(f"error: {args.output} exists; use --overwrite to replace it")
    with open(args.output, "wb") as fh:
        fh.write(png)
    print(image.preview(rows))
    print(f"wrote {args.output}: 256x32, black and white, {sum(map(sum, rows))} lit pixels. Keep it outside the repository. Nothing was sent.")


def cmd_image(args):
    if args.send and args.dry_run:
        sys.exit("--send and --dry-run contradict each other")
    rows = prepared_rows(args)
    bitmap = image.pack_pixels(rows)             # the confirmed layout (lsb-top)
    shown = upload.unpack_pixels(bitmap)         # decoded back from the bytes: exactly what would be uploaded
    sha = image_send.bitmap_sha256(bitmap)
    print(f"{args.picture}: fit {args.fit}, mode {args.mode}, threshold {args.threshold}"
          f"{', inverted' if args.invert else ''}{', autocontrast' if args.autocontrast else ''}; "
          f"{sum(map(sum, shown))} lit pixels of {256 * 32}; bitmap sha256 {sha[:16]}")
    print("preview of the bitmap that would be uploaded ('#' lit, '.' dark):")
    print(image.preview(shown))
    os.makedirs(args.preview_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.picture))[0]
    out = os.path.join(args.preview_dir, f"{stem}_{sha[:8]}_x4.png")
    with open(out, "wb") as fh:
        fh.write(image.rows_to_png(shown, 4))
    print(f"wrote the 4x preview of that bitmap: {out}")

    steps, checks = upload_replay.load_plan(image=bitmap)
    with open(upload_replay.FIXTURE, encoding="utf-8") as fh:
        captured = [bytes.fromhex(h) for h in json.load(fh)[upload_replay.KEY]["out"]]
    diffs = upload_replay.frame_differences(steps, captured)
    print(f"\nupload sequence: {len(steps)} frames (1 ef, 1 start, {len(steps) - 3} data, 1 end); expected replies are those of capture 20")
    print(f"frames that differ from capture 20: {len(diffs)} of {len(steps)}; identical frames: steps "
          f"{[i for i in range(len(steps)) if i not in diffs]}")
    finish_upload(args, steps, checks, bitmap, sha)


def finish_upload(args, steps, checks, bitmap, sha):
    """Shared by `image` and `animation`: print the checks and the limits, then stop (dry run) or upload once."""
    if args.expect_sha is not None:
        checks["--expect-sha matches the bitmap that would be uploaded"] = image.sha_matches(bitmap, args.expect_sha)
    print("checks (the unknown fields keep capture 20's values):")
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    print("reply rule: every reply must equal the expected one; only the 3 colour bytes of the scene-select (ef) reply may be the "
          "speaker's current colour (its checksum must still be right); the first other reply or a timeout stops the run")
    print(f"time: about {0.4 + 0.048 * (len(steps) - 3):.0f} s expected at the speaker's pace; the exchange is stopped after "
          f"{upload_replay.total_limit_for(steps):.0f} s at the latest")
    try:
        print("limits: " + limits.status_line())
        problems = limits.problems_for_upload(sha)
    except limits.LimitsError as err:
        sys.exit(f"error: {err}; nothing was sent")
    for problem in problems:
        print(f"  an upload would be refused: {problem}")
    if not args.send:
        print("dry run: nothing was sent.")
        if problems:
            print("This bitmap could not be uploaded now (see above).")
        elif not all(checks.values()):
            print("A check above says FAIL: this would not be uploaded.")
        else:
            print(f"To upload it: add --send --expect-sha {sha[:12]} (the hash of exactly this bitmap).")
        return
    if problems:
        sys.exit("refused; nothing was sent")
    if args.expect_sha is None:
        sys.exit(f"--send needs --expect-sha: copy at least 12 digits of the bitmap sha256 shown above ({sha[:16]} for this one), "
                 "so that what is sent is what you looked at; nothing was sent")
    if not all(checks.values()):
        sys.exit("plan verification failed (a check above says FAIL); nothing was sent")
    confirm_tempohub_closed(args)
    try:
        result = image_send.send_upload(steps, bitmap, device.open_device)
    except (image_send.UploadRefused, device.DeviceError, limits.LimitsError, RuntimeError) as err:
        sys.exit(f"error: {err}; nothing was sent")
    print(f"summary: written {result['written']}/{len(steps)}, replies matched {result['matched']}, "
          f"unsolicited {result['unsolicited']}, {result['elapsed']:.2f} s")
    if not result["ok"]:
        print(f"FAILED at step {result['stopped_at']}: {result['reason']}" + (f"; raw reply {result['raw']}" if result["raw"] else ""))
        sys.exit(1)
    print("all replies matched; the sequence is complete")


def cmd_animation(args):
    if args.send and args.dry_run:
        sys.exit("--send and --dry-run contradict each other")
    if not 1 <= args.frames <= testanim.FRAMES:
        sys.exit(f"error: --frames is between 1 and {testanim.FRAMES}")
    pictures = testanim.frames(args.frames)
    bitmap = b"".join(image.pack_pixels(rows) for rows in pictures)
    sha = image_send.bitmap_sha256(bitmap)
    print(f"generated test animation, the first {args.frames} of {testanim.FRAMES} pictures (a {testanim.BLOCK_W}x{testanim.BLOCK_H} block moves "
          f"from the left to the right, the picture number is drawn at the top left); {args.frames * upload.IMAGE_BYTES} bytes of image data; "
          f"bitmap sha256 {sha[:16]}")
    print(f"{'no.':>4} {'block x':>8} {'lit pixels':>11}")
    for k, rows in enumerate(pictures):
        print(f"{k + 1:>4} {testanim.block_x(k):>8} {sum(map(sum, rows)):>11}")
    print("picture 1 as it would be uploaded ('#' lit, '.' dark):")
    print(image.preview(pictures[0]))
    os.makedirs(args.preview_dir, exist_ok=True)
    out = os.path.join(args.preview_dir, f"testanim_{args.frames}pictures_{sha[:8]}_x3.png")
    with open(out, "wb") as fh:
        fh.write(image.stack_png(pictures, 3))
    print(f"wrote the 3x contact sheet of all {args.frames} pictures (top to bottom, in the order they are uploaded): {out}")

    steps, checks = upload_replay.load_plan(image=bitmap)
    chunks = len(steps) - 3
    print(f"\nupload sequence: {len(steps)} frames (1 ef, 1 start, {chunks} data, 1 end), declared size {args.frames * 8 * 133} bytes, "
          f"{args.frames * 8} records of 133 bytes; ef, end and the unknown fields are capture 20's, the start frame differs from capture 20's only in the size")
    finish_upload(args, steps, checks, bitmap, sha)


def cmd_slideshow(args):
    """Several of your own pictures (2 to 20, in the order given) in one upload; dry run by default."""
    if args.send and args.dry_run:
        sys.exit("--send and --dry-run contradict each other")
    if not 1 <= len(args.pictures) <= testanim.FRAMES:
        sys.exit(f"error: give between 1 and {testanim.FRAMES} pictures")
    pictures = [prepared_rows(args, path) for path in args.pictures]
    bitmap = b"".join(image.pack_pixels(rows) for rows in pictures)
    sha = image_send.bitmap_sha256(bitmap)
    n = len(pictures)
    print(f"slideshow of {n} pictures, fit {args.fit}, mode {args.mode}, threshold {args.threshold}"
          f"{', inverted' if args.invert else ''}{', autocontrast' if args.autocontrast else ''}; "
          f"{n * upload.IMAGE_BYTES} bytes of image data; bitmap sha256 {sha[:16]}")
    print(f"{'no.':>4} {'lit pixels':>11}  file")
    for k, (rows, path) in enumerate(zip(pictures, args.pictures)):
        print(f"{k + 1:>4} {sum(map(sum, rows)):>11}  {os.path.basename(path)}")
    os.makedirs(args.preview_dir, exist_ok=True)
    out = os.path.join(args.preview_dir, f"slideshow_{n}pictures_{sha[:8]}_x3.png")
    with open(out, "wb") as fh:
        fh.write(image.stack_png(pictures, 3))
    print(f"wrote the 3x contact sheet of all {n} pictures (top to bottom, in the order they are uploaded): {out}")

    steps, checks = upload_replay.load_plan(image=bitmap)
    print(f"\nupload sequence: {len(steps)} frames (1 ef, 1 start, {len(steps) - 3} data, 1 end), declared size {n * 8 * 133} bytes, "
          f"{n * 8} records of 133 bytes; ef, end and the unknown fields are capture 20's, the start frame differs from capture 20's only in the size")
    finish_upload(args, steps, checks, bitmap, sha)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pixelbar", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    im = sub.add_parser("image", help="convert a picture to 256x32 black and white, preview it and (only with --send) upload it")
    im.add_argument("picture")
    add_prepare_options(im)
    im.add_argument("--preview-dir", default=PREVIEW_DIR, help=f"where the 4x preview PNG goes (default {PREVIEW_DIR}; keep it outside the repository)")
    im.add_argument("--expect-sha", metavar="HEX", help="at least 12 hex digits of the bitmap sha256 shown by the dry run; "
                    "required with --send, so that exactly the bitmap you looked at is sent (optional in a dry run: checks it)")
    add_send_options(im)
    an = sub.add_parser("animation", help="a generated test animation of N pictures in one upload (dry run by default)")
    an.add_argument("--frames", type=int, required=True, help=f"how many pictures, 1 to {testanim.FRAMES} (the first N of the 20)")
    an.add_argument("--preview-dir", default=PREVIEW_DIR, help=f"where the contact sheet PNG goes (default {PREVIEW_DIR}; keep it outside the repository)")
    an.add_argument("--expect-sha", metavar="HEX", help="at least 12 hex digits of the bitmap sha256 shown by the dry run; required with --send")
    add_send_options(an)
    sl = sub.add_parser("slideshow", help="1 to 20 of your own pictures in one upload, in the order given (dry run by default)")
    sl.add_argument("pictures", nargs="+")
    add_prepare_options(sl)
    sl.add_argument("--preview-dir", default=PREVIEW_DIR, help=f"where the contact sheet PNG goes (default {PREVIEW_DIR}; keep it outside the repository)")
    sl.add_argument("--expect-sha", metavar="HEX", help="at least 12 hex digits of the bitmap sha256 shown by the dry run; required with --send")
    add_send_options(sl)
    gu = sub.add_parser("gui", help="open a browser window to add pictures, crop them and upload them with a click (needs Pillow)")
    gu.add_argument("--no-browser", action="store_true", help="only print the address")
    gu.add_argument("--port", type=int, default=0, help="fixed port on 127.0.0.1 (default: a free one)")
    pr = sub.add_parser("prepare", help="only convert a picture to a 256x32 black-and-white PNG for you to check")
    pr.add_argument("picture")
    pr.add_argument("output")
    add_prepare_options(pr)
    pr.add_argument("--overwrite", action="store_true", help="replace the output file if it exists")
    t = sub.add_parser("text", help="set the screen text (e8), optionally the text colour (ef) first")
    t.add_argument("text")
    t.add_argument("--color", metavar="RRGGBB", help="text colour, sent as its own frame before the text")
    t.add_argument("--max-bytes", type=int, default=frames.VERIFIED_TEXT_BYTES,
                   help=f"UTF-8 byte limit (default {frames.VERIFIED_TEXT_BYTES}, the longest verified; "
                        f"hard limit {frames.MAX_TEXT_BYTES} = one frame). Raise only step by step.")
    c = sub.add_parser("color", help="set the text colour (ef)")
    c.add_argument("color", metavar="RRGGBB")
    for p in (t, c):
        add_send_options(p)
    args = ap.parse_args(argv)

    if args.command == "gui":
        from . import gui
        return gui.serve(open_browser=not args.no_browser, port=args.port)
    if args.command == "image":
        return cmd_image(args)
    if args.command == "animation":
        return cmd_animation(args)
    if args.command == "slideshow":
        return cmd_slideshow(args)
    if args.command == "prepare":
        return cmd_prepare(args)
    if args.send and args.dry_run:
        sys.exit("--send and --dry-run contradict each other")

    plan = []  # (label, frame, expected reply), in the order they would be sent
    try:
        if args.command == "text" and args.color:
            rgb = frames.parse_hex_color(args.color)
            plan.append(("colour (ef)", frames.color_frame(*rgb), frames.expected_color_reply(*rgb)))
        elif args.command == "color":
            rgb = frames.parse_hex_color(args.color)
            plan.append(("colour (ef)", frames.color_frame(*rgb), frames.expected_color_reply(*rgb)))
        if args.command == "text":
            nbytes = len(args.text.encode("utf-8"))
            if nbytes > frames.MAX_TEXT_BYTES:
                sys.exit(f"error: text is {nbytes} UTF-8 bytes; one frame holds at most {frames.MAX_TEXT_BYTES}")
            if nbytes > args.max_bytes:
                sys.exit(f"error: text is {nbytes} UTF-8 bytes; the limit is {args.max_bytes} (only "
                         f"{frames.VERIFIED_TEXT_BYTES} bytes were verified on the device; raise --max-bytes "
                         f"step by step, hard limit {frames.MAX_TEXT_BYTES})")
            plan.append(("text (e8)", frames.text_frame(args.text), frames.expected_text_reply()))
    except ValueError as err:
        sys.exit(f"error: {err}")

    for label, frame, _ in plan:
        show(label, frame)
    if len(plan) > 1:
        print("note: colour then text in one run was never captured from TempoHub; each frame is verified separately")

    if not args.send:
        print("dry run: nothing was sent. Add --send to send" + (" these frames." if len(plan) > 1 else " this frame."))
        return

    confirm_tempohub_closed(args)
    try:
        for label, frame, expected in plan:
            transmit(label, frame, expected)
    except device.DeviceError as err:
        sys.exit(f"error: {err}")


if __name__ == "__main__":
    main()
