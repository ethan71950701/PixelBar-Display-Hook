"""Replay the upload of capture 20 ("兼职成年人") to the PixelBar, once. Stage 2.

Print-only by default. It can replay nothing but the scene of capture 20, with
every byte as TempoHub sent it (no unknown field is changed). With --send and
--yes-i-closed-tempohub it writes the 25 frames one by one, reads the speaker's
replies and stops at the first reply that is not the expected one, at a
timeout (2 s per reply) or after 30 s in total. No retry, no loop.

Usage:
    python tools/replay_upload.py                                   # print the plan, send nothing
    python tools/replay_upload.py --send --yes-i-closed-tempohub

Close TempoHub first and watch the speaker's screen. The upload replaces what the
scene shows; clicking the scene in TempoHub uploads it again (that is the way back).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pixelbar import device, upload_replay  # noqa: E402


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--send", action="store_true", help="really write the frames to the device")
    ap.add_argument("--yes-i-closed-tempohub", action="store_true", help="required with --send")
    args = ap.parse_args()

    steps, checks = upload_replay.load_plan()
    print(f"plan: {len(steps)} frames of 64 bytes (zero padding not shown), capture 20")
    for i, (label, frame, expected) in enumerate(steps):
        print(f"{i:3d} {label}")
        print(f"      OUT {upload_replay.hx(frame)}")
        print(f"      expect " + (upload_replay.hx(expected) if expected else "no reply (as in the capture)"))
    print("checks:")
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    if not all(checks.values()):
        sys.exit("plan verification failed; nothing was sent")
    print(f"limits: {upload_replay.REPLY_TIMEOUT_S:.0f} s per reply, {upload_replay.TOTAL_LIMIT_S:.0f} s in total; "
          "unsolicited ee / 66 frames are recorded and ignored; the first other reply stops the run.")

    if not args.send:
        print("print-only: nothing was sent. Add --send --yes-i-closed-tempohub to send.")
        return
    if not args.yes_i_closed_tempohub:
        sys.exit("--send also needs --yes-i-closed-tempohub; nothing was sent")
    if device.tempohub_running():
        sys.exit("a TempoHub process is running; close it first. Nothing was sent")
    try:
        import hid
    except ImportError:
        sys.exit("hidapi missing: pip install hidapi")
    try:
        path = device.find_path(hid)
    except device.DeviceError as err:
        sys.exit(f"error: {err}")

    dev = hid.device()
    dev.open_path(path)
    try:
        result = upload_replay.run(dev, steps, log=lambda line: print(line, flush=True))
    finally:
        dev.close()
    print(f"summary: written {result['written']}/{len(steps)}, replies matched {result['matched']}, "
          f"unsolicited {result['unsolicited']}, {result['elapsed']:.2f} s")
    if not result["ok"]:
        print(f"FAILED at step {result['stopped_at']}: {result['reason']}")
        sys.exit(1)
    print("all replies matched; the sequence is complete")


if __name__ == "__main__":
    main()
