"""Replay ONE captured frame to the PixelBar: the e8 "set text A" frame, unmodified.

Stage B of the plan in CLAUDE.md. The frame is a fixed constant taken from
capture usb-02-text-A; there is no way to pass other bytes. Nothing else is
sent: no handshake, no clock sync, no ee poll.

Usage:
    python tools/replay_frame.py                  # print only, sends nothing
    python tools/replay_frame.py --send --yes-i-closed-tempohub

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

# Captured OUT frame from usb-02-text-A: Report ID 2e, aa ec, cmd e8, len 3,
# payload 00 01 41 ("A"), checksum c3, zero padded to 64 bytes.
CAPTURED_A = bytes.fromhex("2eaaece80003000141c3").ljust(64, b"\x00")
FIXTURE = os.path.join(REPO, "tests", "fixtures", "captured_frames.json")


def verify_frame():
    """Refuse to continue unless the frame equals the capture and the builder."""
    checks = {}
    with open(FIXTURE, encoding="utf-8") as fh:
        in_fixture = [bytes.fromhex(k["hex"]) for k in json.load(fh) if k["capture"] == "usb-02-text-A"]
    checks["equals the frame in tests/fixtures (capture usb-02-text-A)"] = in_fixture == [CAPTURED_A]
    checks["equals frames.text_frame('A')"] = frames.text_frame("A") == CAPTURED_A
    checks["is 64 bytes, Report ID 0x2e"] = len(CAPTURED_A) == 64 and CAPTURED_A[0] == 0x2E
    checks["checksum is valid"] = (sum(CAPTURED_A[1:9]) & 0xFF) == CAPTURED_A[9]
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    if not all(checks.values()):
        sys.exit("frame verification failed; nothing was sent")


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
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--send", action="store_true", help="really write the frame to the device")
    ap.add_argument("--yes-i-closed-tempohub", action="store_true",
                    help="required together with --send: TempoHub is closed")
    args = ap.parse_args()

    print("frame to replay (e8 set text 'A', 64 bytes):")
    print(hex_rows(CAPTURED_A))
    verify_frame()

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
        written = dev.write(CAPTURED_A)
        print(f"write() returned {written} (expected 64)")
        if written != len(CAPTURED_A):
            print("write error:", dev.error())
            return
        reply = bytes(dev.read(64, READ_TIMEOUT_MS))  # one read, no loop, no retry
    finally:
        dev.close()

    if not reply:
        print(f"no reply within {READ_TIMEOUT_MS} ms (the capture also showed no direct reply to e8)")
        return
    print(f"reply ({len(reply)} bytes):")
    print(hex_rows(reply))
    if reply[:3] == b"\x2f\xbb\xec":
        ln = int.from_bytes(reply[4:6], "big")
        print(f"  = device frame: cmd={reply[3]:02x} len={ln} payload={reply[6:6 + ln].hex() or '-'}")


if __name__ == "__main__":
    main()
