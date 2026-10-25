"""Send ONE known e8 "set text" frame to the PixelBar, nothing else.

Stages B and C of the plan in CLAUDE.md. Only three fixed frames can be chosen
(see ALLOWED): "A" replayed unmodified from capture usb-02-text-A (stage B),
and "AB" / "ABC" built by pixelbar.frames and only sent if they equal captures
usb-03-text-AB / usb-04-text-ABC byte for byte (stage C). There is no way to
pass other bytes.
Nothing else is sent: no handshake, no clock sync, no ee poll.

Usage:
    python tools/replay_frame.py [--frame A|AB]   # print only, sends nothing
    python tools/replay_frame.py --frame AB --send --yes-i-closed-tempohub

Requires: pip install hidapi (only imported when needed to find / open the device).
Close TempoHub first, and watch the speaker's screen while this runs.
"""
import argparse
import json
import os
import subprocess
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)
from pixelbar import frames  # noqa: E402

VID, PID, USAGE_PAGE = 0x2D99, 0xA106, 0xFF14
READ_TIMEOUT_MS = 2000

# Allowed frames. Each is a literal captured OUT frame (Report ID 2e, aa ec,
# cmd e8, len, payload 00 <count> <text>, checksum, zero padded to 64 bytes).
#   A  : stage B, replayed unmodified from the capture.
#   AB : stage C, built by pixelbar.frames.text_frame("AB"); the build is only
#        sent if it equals the captured frame byte for byte.
ALLOWED = {
    "A": ("usb-02-text-A", "2eaaece80003000141c3", "A"),
    "AB": ("usb-03-text-AB", "2eaaece800040002414207", "AB"),
    "ABC": ("usb-04-text-ABC", "2eaaece8000500034142434c", "ABC"),  # stage C, same rule as AB
    # first non-ASCII frame: 2 characters, 6 UTF-8 bytes (count byte = 06)
    "NIHAO": ("usb-07-text-nihao", "2eaaece800080006e4bda0e5a5bd14", "你好"),
}
FIXTURE = os.path.join(REPO, "tests", "fixtures", "captured_frames.json")


def verify_frame(name):
    """Refuse to continue unless the frame equals the capture and the builder."""
    capture, literal, text = ALLOWED[name]
    frame = bytes.fromhex(literal).ljust(64, b"\x00")
    checks = {}
    with open(FIXTURE, encoding="utf-8") as fh:
        in_fixture = [bytes.fromhex(k["hex"]) for k in json.load(fh) if k["capture"] == capture]
    checks[f"equals the frame in tests/fixtures (capture {capture})"] = in_fixture == [frame]
    checks[f"equals frames.text_frame({text!r})"] = frames.text_frame(text) == frame
    checks[f"count byte {frame[7]} == UTF-8 byte length {len(text.encode('utf-8'))}"] = frame[7] == len(text.encode("utf-8"))
    checks["is 64 bytes, Report ID 0x2e"] = len(frame) == 64 and frame[0] == 0x2E
    checks["checksum is valid"] = (sum(frame[1:len(literal) // 2 - 1]) & 0xFF) == frame[len(literal) // 2 - 1]
    for label, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
    if not all(checks.values()):
        sys.exit("frame verification failed; nothing was sent")
    return frame


def hex_rows(data):
    return "\n".join(f"  {i:02x}: {data[i:i + 16].hex(' ')}" for i in range(0, len(data), 16))


def tempohub_running():
    if os.name != "nt":
        return False
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return "tempohub" in out


def find_device(hid):
    candidates = hid.enumerate(VID, PID)
    matches = [d for d in candidates if d["usage_page"] == USAGE_PAGE]
    if len(matches) != 1:
        print(f"expected exactly one HID path with usage_page 0x{USAGE_PAGE:04x}, found {len(matches)}.")
        print(f"all candidates for {VID:04x}:{PID:04x}:")
        for d in candidates:
            print(f"  usage_page=0x{d['usage_page']:04x} usage=0x{d['usage']:x} "
                  f"iface={d['interface_number']} path={d['path']!r}")
        sys.exit("not guessing; nothing was sent")
    return matches[0]


def main():
    sys.stdout.reconfigure(errors="replace")  # the 你好 label must not crash a legacy console
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frame", choices=sorted(ALLOWED), default="A",
                    help="which fixed frame: A (stage B), AB, ABC, NIHAO (= 你好, stage C); default A")
    ap.add_argument("--send", action="store_true", help="really write the frame to the device")
    ap.add_argument("--yes-i-closed-tempohub", action="store_true",
                    help="required together with --send: TempoHub is closed")
    args = ap.parse_args()

    print(f"frame to send (e8 set text {args.frame!r}, 64 bytes):")
    frame = bytes.fromhex(ALLOWED[args.frame][1]).ljust(64, b"\x00")
    print(hex_rows(frame))
    verify_frame(args.frame)

    try:
        import hid
    except ImportError:
        sys.exit("hidapi missing: pip install hidapi")
    info = find_device(hid)
    print(f"device path with usage_page 0x{USAGE_PAGE:04x}: {info['path']!r} "
          f"(iface {info['interface_number']}, {info['manufacturer_string']!r})")

    if not args.send:
        print("print-only: nothing was sent. Add --send --yes-i-closed-tempohub to send.")
        return
    if not args.yes_i_closed_tempohub:
        sys.exit("--send also needs --yes-i-closed-tempohub; nothing was sent")
    if tempohub_running():
        sys.exit("a TempoHub process is still running; close it first. Nothing was sent")

    dev = hid.device()
    dev.open_path(info["path"])
    try:
        written = dev.write(frame)
        print(f"write() returned {written} (expected 64)")
        if written != len(frame):
            print("write error:", dev.error())
            return
        reply = bytes(dev.read(64, READ_TIMEOUT_MS))  # one read, no loop, no retry
    finally:
        dev.close()

    if not reply:
        print(f"no reply within {READ_TIMEOUT_MS} ms (TempoHub got a reply to every e8 in the captures)")
        return
    print(f"reply ({len(reply)} bytes):")
    print(hex_rows(reply))
    if reply[0] == 0x2F and reply[2] == 0xEC:  # tag is bb (answers) or cc (reply to e8)
        ln = int.from_bytes(reply[4:6], "big")
        print(f"  = device frame: cmd={reply[3]:02x} len={ln} payload={reply[6:6 + ln].hex() or '-'}")


if __name__ == "__main__":
    main()
