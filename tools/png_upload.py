"""Dry run: turn a 256x32 PNG into a scene bitmap and show what its upload would send. Sends nothing.

Usage:
    python tools/png_upload.py IMAGE.png --dry-run [--threshold 128] [--bit-order lsb-top] [--out FILE.bin]

The PNG must be exactly 256x32, white pixels on a black background (a pixel is lit
when its brightness is at or above --threshold); any other size is an error, nothing
is scaled or cropped. The bitmap is 1024 bytes: vertical bytes, one byte = 8 pixels
of a column, 4 pages of 256, least significant bit on top (--bit-order lsb-top, the
layout confirmed on the screen; msb-top is only an option to test the alternative).
Keep the PNG files outside the repository (for example in C:\\pixelbar-raw\\).

The tool prints a '#' / '.' picture of the image, then the whole upload sequence it
would produce (capture 20's frames with only the image data replaced; every unknown
field keeps the captured value) and how it differs from capture 20. There is no
code here that opens or writes the device; --dry-run is required.
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pixelbar import image, upload, upload_replay  # noqa: E402

BUDGET_FILE = os.path.join(os.path.expanduser("~"), ".pixelbar", "stage3_uploads.txt")


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("png")
    ap.add_argument("--dry-run", action="store_true", help="required: nothing is sent")
    ap.add_argument("--threshold", type=int, default=image.DEFAULT_THRESHOLD, help="brightness 0-255 from which a pixel is lit (default 128)")
    ap.add_argument("--bit-order", choices=image.BIT_ORDERS, default="lsb-top", help="default lsb-top, the confirmed layout")
    ap.add_argument("--out", help="also write the 1024-byte bitmap to this file (keep it outside the repository)")
    args = ap.parse_args()
    if not args.dry_run:
        sys.exit("refused: sending a converted image is not implemented; use --dry-run")

    try:
        with open(args.png, "rb") as fh:
            rows = image.png_to_pixels(fh.read(), args.threshold)
        bitmap = image.pack_pixels(rows, args.bit_order)
        steps, checks = upload_replay.load_plan(image=bitmap)
    except (OSError, ValueError) as err:
        sys.exit(f"error: {err}")

    lit = sum(map(sum, rows))
    print(f"{args.png}: 256x32, threshold {args.threshold}, bit order {args.bit_order}; {lit} lit pixels of {256 * 32}; "
          f"{sum(1 for b in bitmap if b)} non-zero bytes of {len(bitmap)}; sha256 {hashlib.sha256(bitmap).hexdigest()[:16]}")
    if args.bit_order != "lsb-top":
        print("WARNING: msb-top is not the confirmed layout; it is only for testing the alternative.")
    print("preview ('#' lit, '.' dark; columns 0-255 left to right, rows 0-31 top to bottom):")
    print(image.preview(rows))
    if args.out:
        with open(args.out, "wb") as fh:
            fh.write(bitmap)
        print(f"wrote the bitmap to {args.out}")

    with open(upload_replay.FIXTURE, encoding="utf-8") as fh:
        captured = [bytes.fromhex(h) for h in json.load(fh)[upload_replay.KEY]["out"]]
    diffs = upload_replay.frame_differences(steps, captured)
    print(f"\nupload sequence: {len(steps)} frames (1 ef, 1 start, {len(steps) - 3} data, 1 end); expected replies are those of capture 20")
    print(f"frames that differ from capture 20: {len(diffs)} of {len(steps)}")
    for i, offsets in diffs.items():
        checksum_at = 6 + int.from_bytes(steps[i][1][4:6], "big")
        data = [k for k in offsets if k != checksum_at]
        note = "and the frame's own checksum" if checksum_at in offsets else "the frame's own checksum happens to be unchanged"
        print(f"  step {i:2d} {steps[i][0]}: {len(data)} image byte(s) differ ({data[0]}..{data[-1]}), {note}")
    same = [i for i in range(len(steps)) if i not in diffs]
    print(f"frames identical to capture 20: steps {same}")
    print("checks:")
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    used = upload_replay.UploadBudget(BUDGET_FILE).used()
    print(f"upload budget: {used} of {upload_replay.UPLOAD_LIMIT} used; the next upload would be number {used + 1}")
    if not all(checks.values()):
        sys.exit("plan verification failed; this image must not be sent")
    print("dry run: nothing was sent.")


if __name__ == "__main__":
    main()
