"""python -m pixelbar text "你好" --color ff0000 --dry-run

Phase A: only prints the bytes it would send and compares them with frames
captured from TempoHub. There is no code path that opens or writes a device.
"""
import argparse
import json
import os
import sys

from . import frames

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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pixelbar", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    t = sub.add_parser("text", help="build the set-text frame (e8), optionally a colour frame (ef)")
    t.add_argument("text")
    t.add_argument("--color", help="RRGGBB hex, sent as a separate ef frame")
    c = sub.add_parser("color", help="build the colour frame (ef)")
    c.add_argument("color", help="RRGGBB hex")
    for p in (t, c):
        p.add_argument("--dry-run", action="store_true", help="required: print the bytes, send nothing")
    args = ap.parse_args(argv)

    if not args.dry_run:
        sys.exit("sending to the device is not implemented yet; use --dry-run")

    try:
        if args.command == "text":
            if args.color:
                show("colour (ef)", frames.color_frame(*frames.parse_hex_color(args.color)))
                print("  note: ef and e8 were captured as separate actions; their order together is untested")
            show("text (e8)", frames.text_frame(args.text))
        else:
            show("colour (ef)", frames.color_frame(*frames.parse_hex_color(args.color)))
    except ValueError as err:
        sys.exit(f"error: {err}")


if __name__ == "__main__":
    main()
