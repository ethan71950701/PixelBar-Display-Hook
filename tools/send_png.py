"""Send ONE previewed 256x32 PNG as capture 20's scene (stage 3). At most 5 uploads in total.

Print-only by default. The upload is capture 20's frames with only the image data
replaced; every unknown field keeps the captured value (see tools/png_upload.py, which
shows the preview and the differences). To send, the bitmap's SHA-256 prefix from that
preview must be given with --expect-sha: if it does not match this PNG, nothing is
sent, so what is sent is what was previewed. Only the confirmed bit order (lsb-top) can
be sent. The exchange is the bounded one of stage 2: each write waits for its expected
reply (2 s), unsolicited ee / 66 frames are recorded and ignored, the first other reply
or a timeout stops everything, no retry, 30 s in total. Each send counts against the
budget of 5, whether it succeeds or not.

Usage:
    python tools/send_png.py IMAGE.png --expect-sha 037720be605a                  # print only
    python tools/send_png.py IMAGE.png --expect-sha 037720be605a --send --yes-i-closed-tempohub

Before sending: close TempoHub, make the screen show `Hello`, watch the screen. To go back,
click another scene in TempoHub and then this scene (「兼职成年人」) again.
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pixelbar import device, image, upload_replay  # noqa: E402

BUDGET_FILE = os.path.join(os.path.expanduser("~"), ".pixelbar", "stage3_uploads.txt")


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("png")
    ap.add_argument("--threshold", type=int, default=image.DEFAULT_THRESHOLD)
    ap.add_argument("--bit-order", choices=image.BIT_ORDERS, default="lsb-top")
    ap.add_argument("--expect-sha", required=True, metavar="HEX", help="at least 12 hex digits of the bitmap's SHA-256 shown by png_upload.py")
    ap.add_argument("--send", action="store_true", help="really write the frames to the device")
    ap.add_argument("--yes-i-closed-tempohub", action="store_true", help="required with --send")
    args = ap.parse_args()
    budget = upload_replay.UploadBudget(BUDGET_FILE)

    try:
        with open(args.png, "rb") as fh:
            bitmap = image.png_to_bitmap(fh.read(), args.threshold, args.bit_order)
        steps, checks = upload_replay.load_plan(image=bitmap)
    except (OSError, ValueError) as err:
        sys.exit(f"error: {err}")
    digest = hashlib.sha256(bitmap).hexdigest()
    print(f"{args.png}: threshold {args.threshold}, bit order {args.bit_order}, bitmap sha256 {digest[:16]}; "
          f"upload budget used {budget.used()} of {budget.limit}")
    with open(upload_replay.FIXTURE, encoding="utf-8") as fh:
        captured = [bytes.fromhex(h) for h in json.load(fh)[upload_replay.KEY]["out"]]
    diffs = upload_replay.frame_differences(steps, captured)
    print(f"{len(steps)} frames; {len(diffs)} differ from capture 20 (data frames only); expected replies are those of capture 20")
    checks["the bitmap is the one previewed (--expect-sha matches)"] = image.sha_matches(bitmap, args.expect_sha)
    checks["bit order is the confirmed lsb-top"] = args.bit_order == "lsb-top"
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    if not all(checks.values()):
        sys.exit("plan verification failed; nothing was sent")
    print(f"limits: {upload_replay.REPLY_TIMEOUT_S:.0f} s per reply, {upload_replay.TOTAL_LIMIT_S:.0f} s in total, "
          "the first unexpected reply or a timeout stops the run")

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
        count = budget.take()
    except (device.DeviceError, RuntimeError) as err:
        sys.exit(f"error: {err}; nothing was sent")
    print(f"upload {count} of {budget.limit} starts now")

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
        if result["raw"]:
            print(f"raw reply: {result['raw']}")
        sys.exit(1)
    print("all replies matched; the sequence is complete")


if __name__ == "__main__":
    main()
