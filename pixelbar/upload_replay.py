"""Stage 2: the plan and the exchange logic to replay the upload of capture 20, once.

Only the scene of capture 20 can be planned (there is no way to name another
one, and no value can be changed). `load_plan` checks the frames against the
capture fixture and the stage-1 builder; `run` writes them through a device
object with `write(frame)` and `read(size, timeout_ms)` (hidapi's interface),
and stops at the first problem. The real device is opened by
tools/replay_upload.py; this module imports no hidapi and opens nothing.

Rules (user-approved, 2026-10-25):
  - after each write that has an expected reply, read until the matching reply
    arrives or 2 s pass; unsolicited ee / 66 frames are recorded and ignored;
  - the first mismatching frame or a timeout stops everything: no retry, no
    resend, no abort command;
  - the whole exchange has a time limit (30 s);
  - the last data chunk is not acknowledged by the speaker (as in the capture),
    so nothing is waited for after it.
"""
import json
import os
import time

from . import frames, upload

KEY = "20"  # the only scene that can be replayed: 兼职成年人, plain text
REPLY_TIMEOUT_S = 2.0
TOTAL_LIMIT_S = 30.0
UNSOLICITED_COMMANDS = (0xEE, 0x66)
FIXTURE = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "captured_uploads.json")


MIN_BORDER = 4          # stage 3: the changed pixel is at least this far from every border
UPLOAD_LIMIT = 5        # stage 3: at most this many uploads in total


def pixel_problems(image, x, y):
    """Why (x, y) is not an allowed stage-3 pixel in this image (empty list = allowed)."""
    rows = upload.unpack_pixels(image[:upload.IMAGE_BYTES])
    top = min((r for r in range(upload.HEIGHT) if any(rows[r])), default=upload.HEIGHT)
    problems = []
    if not (MIN_BORDER <= x <= upload.WIDTH - 1 - MIN_BORDER and MIN_BORDER <= y <= upload.HEIGHT - 1 - MIN_BORDER):
        problems.append(f"closer than {MIN_BORDER} pixels to a border")
    elif y >= top:
        problems.append(f"not above the text (the first lit row of the image is {top})")
    elif rows[y][x]:
        problems.append("already lit")
    return problems


class UploadBudget:
    """Counts upload attempts in a small file outside the repository; refuses more than `limit`."""

    def __init__(self, path, limit=UPLOAD_LIMIT):
        self.path, self.limit = path, limit

    def used(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return int(fh.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def take(self):
        used = self.used()
        if used >= self.limit:
            raise RuntimeError(f"upload budget used up ({used} of {self.limit})")
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(str(used + 1))
        return used + 1


def frame_differences(steps, captured):
    """{step index: [byte offsets that differ]} between the planned OUT frames and the captured ones."""
    return {i: [k for k in range(64) if s[1][k] != captured[i][k]] for i, s in enumerate(steps) if s[1] != captured[i]}


def load_plan(fixture=FIXTURE, pixel=None):
    """Return (steps, checks): steps = [(label, OUT frame, expected reply or None)].

    pixel=(x, y) switches that one pixel on in the image data (stage 3); everything else,
    including every unknown field, stays as captured.
    """
    with open(fixture, encoding="utf-8") as fh:
        c = json.load(fh)[KEY]
    ef = bytes.fromhex(c["ef_payload"])
    captured_image = bytes.fromhex(c["image_hex"])
    image = captured_image if pixel is None else upload.set_pixel(captured_image, *pixel)
    built = upload.upload_sequence(image, c["type"], c["xx"], tuple(ef[1:4]), ef[7])
    expected = upload.expected_replies(built)
    n = len(built)
    steps = []
    for i, frame in enumerate(built):
        if i == 0:
            label, reply = "ef scene select", expected[0]
        elif i == 1:
            label, reply = "17 start", expected[1]
        elif i == n - 1:
            label, reply = "17 end", expected[-1]
        elif i == n - 2:
            label, reply = f"17 data seq {i - 2} (last, not acknowledged)", None
        else:
            label, reply = f"17 data seq {i - 2}", expected[i]
        steps.append((label, frame, reply))
    captured_out = [bytes.fromhex(h) for h in c["out"]]
    if pixel is None:
        change_checks = {"OUT frames equal the TempoHub frames of capture 20 (fixture)": built == captured_out}
    else:
        diffs = frame_differences(steps, captured_out)
        changed = next(iter(diffs)) if len(diffs) == 1 else None
        only = diffs[changed] if changed is not None else []
        checksum_at = None if changed is None else 6 + int.from_bytes(steps[changed][1][4:6], "big")
        data_offsets = [k for k in only if k != checksum_at]
        bits = sum(bin(a ^ b).count("1") for a, b in zip(image, captured_image))
        change_checks = {
            f"pixel {pixel} is allowed (blank area above the text, {MIN_BORDER}+ pixels from every border)": not pixel_problems(captured_image, *pixel),
            "exactly one bit of the image data differs from capture 20": bits == 1,
            "exactly one OUT frame differs from capture 20": len(diffs) == 1,
            "in that frame only the image byte and the frame's own checksum differ": len(only) == 2 and checksum_at in only and len(data_offsets) == 1,
            "every other OUT frame equals the TempoHub frame of capture 20": len(diffs) == 1,
        }
    checks = {
        **change_checks,
        "expected replies equal the speaker's replies in capture 20": expected == [bytes.fromhex(h) for h in c["in"]],
        "record header T is the captured 0x02": c["type"] == 0x02,
        "record header XX XX is the captured 0x0064": c["xx"] == 0x0064,
        "ef frame payload is the captured 01 33 66 99 00 01 02 21 ff": ef.hex() == "0133669900010221ff",
        "every reply expectation is used once": sum(1 for s in steps if s[2] is not None) == len(expected),
        "all frames are 64 bytes": all(len(s[1]) == 64 for s in steps),
    }
    return steps, checks


def hx(data):
    return bytes(data).rstrip(b"\x00").hex()


def classify(data, expected):
    """'matched', 'unsolicited' (an ee or 66 push from the speaker) or 'mismatch'."""
    if data == expected:
        return "matched"
    if len(data) >= 4 and data[0] == frames.REPORT_ID_IN and data[2] == frames.SECOND_MAGIC and data[3] in UNSOLICITED_COMMANDS:
        return "unsolicited"
    return "mismatch"


def run(dev, steps, clock=time.monotonic, log=print):
    """Write the steps in order. Returns a dict; ok is True only if every expected reply matched."""
    result = {"ok": False, "written": 0, "matched": 0, "unsolicited": [], "stopped_at": None, "reason": None, "raw": None, "elapsed": 0.0}
    t0 = clock()

    def stop(i, reason, raw=None):
        result.update(stopped_at=i, reason=reason, raw=raw.hex() if raw else None, elapsed=clock() - t0)
        log(f"STOP at step {i}: {reason}" + (f"; raw reply {raw.hex()}" if raw else ""))
        return result

    for i, (label, frame, expected) in enumerate(steps):
        if clock() - t0 > TOTAL_LIMIT_S:
            return stop(i, f"total time limit of {TOTAL_LIMIT_S:.0f} s reached before this write")
        written = dev.write(frame)
        if written != len(frame):
            return stop(i, f"write returned {written}, expected {len(frame)}")
        result["written"] += 1
        stamp = f"[{clock() - t0:6.3f}s] step {i:2d} {label}: written"
        if expected is None:
            log(stamp + "; no reply expected")
            continue
        deadline = min(clock() + REPLY_TIMEOUT_S, t0 + TOTAL_LIMIT_S)
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                limit = "total time limit" if clock() - t0 >= TOTAL_LIMIT_S else f"no matching reply within {REPLY_TIMEOUT_S:.0f} s"
                return stop(i, limit)
            data = bytes(dev.read(64, max(1, int(remaining * 1000))))
            if not data:
                continue
            kind = classify(data, expected)
            if kind == "unsolicited":
                result["unsolicited"].append((i, hx(data)))
                log(f"[{clock() - t0:6.3f}s]   unsolicited from the speaker (recorded, ignored): {hx(data)}")
                continue
            if kind == "mismatch":
                reason = "reply differs from the expected one"
                if label == "17 start" and data[3] == upload.CMD_UPLOAD and data[6:8] == b"\x00\x01":
                    reason = f"the speaker announces chunk size {int.from_bytes(data[8:10], 'big')}, not {upload.CHUNK_BYTES}"
                return stop(i, reason, data.rstrip(b"\x00"))
            result["matched"] += 1
            log(stamp + f"; reply matched ({hx(data)})")
            break
    result.update(ok=True, elapsed=clock() - t0)
    return result
