"""Stage 3: upload capture 20's scene with ONE pixel switched on. At most 5 uploads in total.

Print-only by default. The scene is the one of capture 20 ("兼职成年人"); every
frame is as TempoHub sent it except the one data frame that holds the changed
pixel (and its own checksum byte); every unknown field keeps the captured value.
The pixel must lie in the blank area above the text, 4+ pixels from every border;
the default is x=128, y=4. Sending uses the same bounded exchange as stage 2: each
write waits for its expected reply (2 s), unsolicited ee / 66 frames are recorded
and ignored, the first other reply or a timeout stops everything, no retry, 30 s
limit. Every send counts against the budget of 5, whether it succeeds or not.

Usage:
    python tools/upload_pixel.py                                  # print only
    python tools/upload_pixel.py --send --yes-i-closed-tempohub

Before sending: close TempoHub, make the screen show something else (for example
`python -m pixelbar text Hello`), and watch the screen. To go back, click the scene in TempoHub.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pixelbar import device, upload, upload_replay  # noqa: E402

BUDGET_FILE = os.path.join(os.path.expanduser("~"), ".pixelbar", "stage3_uploads.txt")


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--x", type=int, default=128)
    ap.add_argument("--y", type=int, default=4)
    ap.add_argument("--send", action="store_true", help="really write the frames to the device")
    ap.add_argument("--yes-i-closed-tempohub", action="store_true", help="required with --send")
    args = ap.parse_args()
    budget = upload_replay.UploadBudget(BUDGET_FILE)

    steps, checks = upload_replay.load_plan(pixel=(args.x, args.y))
    index, bit = upload.pixel_location(args.x, args.y)
    print(f"pixel ({args.x}, {args.y}) of the 256x32 image: image byte {index} (page {index // upload.WIDTH}, column {index % upload.WIDTH}), "
          f"bit {bit} (value 0x{1 << bit:02x}); upload budget used {budget.used()} of {budget.limit}")
    with open(upload_replay.FIXTURE, encoding="utf-8") as fh:
        captured = [bytes.fromhex(h) for h in json.load(fh)[upload_replay.KEY]["out"]]
    for i, offsets in upload_replay.frame_differences(steps, captured).items():
        label = steps[i][0]
        print(f"frames that differ from capture 20: step {i} ({label}), byte offsets {offsets}")
        print(f"  captured {upload_replay.hx(captured[i])}")
        print(f"  planned  {upload_replay.hx(steps[i][1])}")
    print(f"plan: {len(steps)} frames; expected replies are those of capture 20")
    print("checks:")
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
