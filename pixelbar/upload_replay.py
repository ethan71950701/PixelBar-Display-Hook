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
import math
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
    return {i: [k for k in range(64) if s[1][k] != captured[i][k]] for i, s in enumerate(steps) if i < len(captured) and s[1] != captured[i]}


def _multi_image_checks(steps, built, image, c, ef, captured_out):
    """Checks for an upload of several images, written from the format and not from the builder's code:
    the start frame carries the new total size, the records are numbered 0.., the data frames carry exactly
    the image data, and the speaker is expected to answer like it did for TempoHub's multi-image uploads."""
    count = len(image) // upload.IMAGE_BYTES
    records = count * (upload.IMAGE_BYTES // upload.RECORD_DATA)       # 8 records of 128 bytes per image
    stream, seqs, bad_payload = bytearray(), [], False
    for frame in built[2:-1]:
        payload = frame[6:6 + int.from_bytes(frame[4:6], "big")]
        if payload[:2] != b"\x00\x02":
            bad_payload = True
            break
        seqs.append(int.from_bytes(payload[2:4], "big"))
        stream += payload[4:]
    record = upload.RECORD_HEADER + upload.RECORD_DATA
    headers_ok = len(stream) == records * record and all(
        bytes(stream[i * record:i * record + 5]) == bytes([c["type"]]) + i.to_bytes(2, "big") + c["xx"].to_bytes(2, "big") for i in range(records))
    data = b"".join(bytes(stream[i * record + upload.RECORD_HEADER:(i + 1) * record]) for i in range(records))
    start_diff = [k for k in range(64) if built[1][k] != captured_out[1][k]]
    chunks = len(seqs)

    def reply(cmd, payload):
        return frames.build_frame(cmd, payload, report_id=frames.REPORT_ID_IN, tag=frames.TAG_REPLY)

    wanted = [reply(frames.CMD_SET_COLOR, ef), reply(upload.CMD_UPLOAD, b"\x00\x01\x00\x32")]
    wanted += [reply(upload.CMD_UPLOAD, b"\x00\x03" + s.to_bytes(2, "big")) for s in range(chunks - 1)]
    wanted.append(reply(upload.CMD_UPLOAD, b"\x00\x05"))
    return {
        f"{count} images of 256x32 ({count * upload.IMAGE_BYTES} bytes of image data), {records} records, {chunks} data chunks": not bad_payload and chunks > 0,
        "the ef frame and the end frame equal the TempoHub frames of capture 20": built[0] == captured_out[0] and built[-1] == captured_out[-1],
        f"the start frame carries the new total size {records * record} and differs from capture 20's start frame in nothing else": (
            int.from_bytes(built[1][8:12], "big") == records * record and all(k in (8, 9, 10, 11, 12) for k in start_diff)),
        ("record headers: T and XX XX are capture 20's, the index runs 0.. without gaps" if c["xx"] == 0x0064 else
         f"record headers: T is capture 20's, XX XX is the requested 0x{c['xx']:04x}, the index runs 0.. without gaps"): headers_ok,
        "the data frames carry exactly the image data, chunk numbers run 0.. without gaps": data == image and seqs == list(range(chunks)),
        "expected replies: ef echo, ready (chunk size 50), one acknowledgement per chunk but the last, done": [s[2] for s in steps if s[2] is not None] == wanted,
    }


ALLOWED_XX = (0x0030, 0x0064)          # the values TempoHub itself used for scenes with pictures (0xffff, one picture, not allowed)


def load_plan(fixture=FIXTURE, pixel=None, image=None, xx=None):
    """Return (steps, checks): steps = [(label, OUT frame, expected reply or None)].

    pixel=(x, y) switches that one pixel on in the image data (stage 3); image=<1024 bytes>
    replaces the whole image data. Everything else, including every unknown field, stays as
    captured. xx=<0x0030 or 0x0064> (several images only) is the one exception: it replaces the
    record header XX XX, which changes how long the speaker shows each picture (measured).
    """
    if pixel is not None and image is not None:
        raise ValueError("give either a pixel or a whole image, not both")
    with open(fixture, encoding="utf-8") as fh:
        c = json.load(fh)[KEY]
    if xx is not None:
        if xx not in ALLOWED_XX:
            raise ValueError(f"XX XX may only be one of {[hex(v) for v in ALLOWED_XX]} (values TempoHub used)")
        if image is None or len(image) <= upload.IMAGE_BYTES:
            raise ValueError("a different XX XX is only for uploads of several pictures")
        c = {**c, "xx": xx}
    ef = bytes.fromhex(c["ef_payload"])
    captured_image = bytes.fromhex(c["image_hex"])
    if image is not None:
        if len(image) < upload.IMAGE_BYTES or len(image) % upload.IMAGE_BYTES:
            raise ValueError(f"the image data must be a whole number of {upload.IMAGE_BYTES}-byte images (256x32 each)")
    else:
        image = captured_image if pixel is None else upload.set_pixel(captured_image, *pixel)
    built = upload.upload_sequence(image, c["type"], c["xx"], tuple(ef[1:4]), ef[7])
    expected = upload.expected_replies(built)
    n = len(built)
    steps = []
    for i, frame in enumerate(built):
        if i == 0:
            label, reply = SCENE_SELECT_LABEL, expected[0]
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
    many = len(image) > upload.IMAGE_BYTES
    if many:
        change_checks = _multi_image_checks(steps, built, image, c, ef, captured_out)
    elif pixel is None and image == captured_image:
        change_checks = {"OUT frames equal the TempoHub frames of capture 20 (fixture)": built == captured_out}
    elif pixel is None:
        diffs = frame_differences(steps, captured_out)
        old_stream = upload.record_stream(captured_image, c["type"], c["xx"])
        new_stream = upload.record_stream(image, c["type"], c["xx"])
        header_bytes = [k for k in range(len(old_stream)) if k % (upload.RECORD_HEADER + upload.RECORD_DATA) < upload.RECORD_HEADER]

        def data_or_checksum_only(i, offsets):
            checksum_at = 6 + int.from_bytes(steps[i][1][4:6], "big")
            return 2 <= i <= len(steps) - 2 and all(k == checksum_at or 10 <= k < checksum_at for k in offsets)

        change_checks = {
            "ef, start and end frames equal the TempoHub frames of capture 20": all(steps[i][1] == captured_out[i] for i in (0, 1, n - 1)),
            "record headers (T, index, XX XX) equal capture 20's": all(old_stream[k] == new_stream[k] for k in header_bytes),
            "every differing frame is a data frame and differs only in image bytes and its own checksum": all(data_or_checksum_only(i, o) for i, o in diffs.items()),
        }
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
        **({} if many else {"expected replies equal the speaker's replies in capture 20": expected == [bytes.fromhex(h) for h in c["in"]]}),
        "record header T is the captured 0x02": c["type"] == 0x02,
        **({"record header XX XX is the captured 0x0064": c["xx"] == 0x0064} if xx is None else
           {f"record header XX XX is the requested 0x{xx:04x}, the only unknown field that differs from capture 20 (T, ef frame: captured)": c["xx"] == xx}),
        "ef frame payload is the captured 01 33 66 99 00 01 02 21 ff": ef.hex() == "0133669900010221ff",
        "every reply expectation is used once": sum(1 for s in steps if s[2] is not None) == len(expected),
        "all frames are 64 bytes": all(len(s[1]) == 64 for s in steps),
    }
    return steps, checks


def hx(data):
    return bytes(data).rstrip(b"\x00").hex()


SCENE_SELECT_LABEL = "ef scene select"
_EF_PAYLOAD = 6                      # offset of the 9-byte payload in a 64-byte frame
_EF_COLOUR = (7, 8, 9)               # payload bytes 1-3: R G B
_EF_CHECKSUM = _EF_PAYLOAD + 9


def same_but_colour(data, expected):
    """True if `data` is `expected` except for the three colour bytes of an ef frame.

    The speaker answers the scene-select frame with the text colour in force, not with the colour
    bytes that were sent (seen in upload attempt 4). Everything else has to be identical, and the
    checksum of what was received has to be right for the bytes received.
    """
    if len(data) != len(expected) or len(data) <= _EF_CHECKSUM:
        return False
    if any(data[i] != expected[i] for i in range(len(expected)) if i not in _EF_COLOUR and i != _EF_CHECKSUM):
        return False
    return data[_EF_CHECKSUM] == sum(data[1:_EF_CHECKSUM]) & 0xFF


def classify(data, expected, colour_may_differ=False):
    """'matched', 'unsolicited' (an ee or 66 push from the speaker) or 'mismatch'."""
    if data == expected or (colour_may_differ and same_but_colour(data, expected)):
        return "matched"
    if len(data) >= 4 and data[0] == frames.REPORT_ID_IN and data[2] == frames.SECOND_MAGIC and data[3] in UNSOLICITED_COMMANDS:
        return "unsolicited"
    return "mismatch"


def total_limit_for(steps):
    """Seconds allowed for the whole exchange: 30 s, more for long uploads (each chunk takes the
    speaker about 46-52 ms; the limit is 5 s plus 0.12 s per chunk, so well above the pace seen)."""
    return max(TOTAL_LIMIT_S, float(math.ceil(5 + 0.12 * (len(steps) - 3))))


def run(dev, steps, clock=time.monotonic, log=print, total_limit=None):
    """Write the steps in order. Returns a dict; ok is True only if every expected reply matched."""
    result = {"ok": False, "written": 0, "matched": 0, "unsolicited": [], "stopped_at": None, "reason": None, "raw": None, "elapsed": 0.0,
              "colour_reported": None}
    limit_s = total_limit if total_limit else total_limit_for(steps)
    t0 = clock()

    def stop(i, reason, raw=None):
        result.update(stopped_at=i, reason=reason, raw=raw.hex() if raw else None, elapsed=clock() - t0)
        log(f"STOP at step {i}: {reason}" + (f"; raw reply {raw.hex()}" if raw else ""))
        return result

    for i, (label, frame, expected) in enumerate(steps):
        if clock() - t0 > limit_s:
            return stop(i, f"total time limit of {limit_s:.0f} s reached before this write")
        written = dev.write(frame)
        if written != len(frame):
            return stop(i, f"write returned {written}, expected {len(frame)}")
        result["written"] += 1
        stamp = f"[{clock() - t0:6.3f}s] step {i:2d} {label}: written"
        if expected is None:
            log(stamp + "; no reply expected")
            continue
        deadline = min(clock() + REPLY_TIMEOUT_S, t0 + limit_s)
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                why = "total time limit" if clock() - t0 >= limit_s else f"no matching reply within {REPLY_TIMEOUT_S:.0f} s"
                return stop(i, why)
            data = bytes(dev.read(64, max(1, int(remaining * 1000))))
            if not data:
                continue
            kind = classify(data, expected, colour_may_differ=(label == SCENE_SELECT_LABEL))
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
            if data != expected:                  # only possible for the scene-select frame: the colour in force
                result["colour_reported"] = data[_EF_COLOUR[0]:_EF_COLOUR[-1] + 1].hex()
                log(stamp + f"; reply matched except the colour bytes, which are the speaker's current colour {result['colour_reported']} ({hx(data)})")
            else:
                log(stamp + f"; reply matched ({hx(data)})")
            break
    result.update(ok=True, elapsed=clock() - t0)
    return result
