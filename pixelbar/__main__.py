"""python -m pixelbar text "你好" --send --yes-i-closed-tempohub

Without --send nothing touches the device: the frame is built, printed and
compared with frames captured from TempoHub. One frame per invocation: a text
frame (e8) or a colour frame (ef); sending both together was never captured.
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


def transmit(frame, expected):
    reply = device.send_frame(frame)
    if not reply:
        sys.exit(f"no reply within {device.READ_TIMEOUT_MS} ms")
    print("reply: " + reply.rstrip(b"\x00").hex())
    print("  as expected (same as TempoHub got)" if reply == expected else "  UNEXPECTED reply, differs from what TempoHub got")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pixelbar", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    t = sub.add_parser("text", help="set the screen text (e8)")
    t.add_argument("text")
    t.add_argument("--color", help="RRGGBB hex; with text it is only shown, never sent together")
    t.add_argument("--max-bytes", type=int, default=frames.VERIFIED_TEXT_BYTES,
                   help=f"UTF-8 byte limit (default {frames.VERIFIED_TEXT_BYTES}, the longest verified; "
                        f"hard limit {frames.MAX_TEXT_BYTES}). Raise only step by step.")
    c = sub.add_parser("color", help="set the text colour (ef)")
    c.add_argument("color", help="RRGGBB hex")
    for p in (t, c):
        p.add_argument("--dry-run", action="store_true", help="default; kept for clarity")
        p.add_argument("--send", action="store_true", help="really write the frame to the device")
        p.add_argument("--yes-i-closed-tempohub", action="store_true", help="required with --send")
    args = ap.parse_args(argv)

    if args.send and not args.yes_i_closed_tempohub:
        sys.exit("--send also needs --yes-i-closed-tempohub; nothing was sent")
    if args.send and args.dry_run:
        sys.exit("--send and --dry-run contradict each other")

    try:
        if args.command == "text":
            if args.send and args.color:
                sys.exit("send the text and the colour in two separate invocations")
            nbytes = len(args.text.encode("utf-8"))
            if nbytes > min(args.max_bytes, frames.MAX_TEXT_BYTES):
                sys.exit(f"text is {nbytes} UTF-8 bytes; the limit is {min(args.max_bytes, frames.MAX_TEXT_BYTES)} "
                         f"(only {frames.VERIFIED_TEXT_BYTES} bytes were verified on the device; "
                         f"raise --max-bytes step by step, hard limit {frames.MAX_TEXT_BYTES})")
            if args.color:
                show("colour (ef)", frames.color_frame(*frames.parse_hex_color(args.color)))
                print("  note: not sent with the text; use the `color` command")
            frame = frames.text_frame(args.text)
            show("text (e8)", frame)
            expected = frames.expected_text_reply()
        else:
            rgb = frames.parse_hex_color(args.color)
            frame = frames.color_frame(*rgb)
            show("colour (ef)", frame)
            expected = frames.expected_color_reply(*rgb)

        if not args.send:
            print("dry run: nothing was sent. Add --send --yes-i-closed-tempohub to send this frame.")
            return
        transmit(frame, expected)
    except (ValueError, device.DeviceError) as err:
        sys.exit(f"error: {err}")


if __name__ == "__main__":
    main()
