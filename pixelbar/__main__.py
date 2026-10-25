"""python -m pixelbar text "Hello" --color 336699 --send

Default is a dry run: frames are built, printed and compared with frames
captured from TempoHub; nothing touches the device. --send writes them, after
you confirm TempoHub is closed. Text must fit one frame (hard error otherwise).
"""
import argparse
import json
import os
import sys

from . import device, frames

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "captured_frames.json")


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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pixelbar", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
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
