"""The only code path that can upload a picture: checks the limits, writes once, records the result.

`open_device` is passed in (pixelbar.device.open_device for real use, a fake in the
tests), so nothing in this module opens hardware by itself. There is no retry and
no loop: the exchange is upload_replay.run (bounded reads, stop at the first
unexpected reply or timeout, 30 s limit).
"""
import datetime
import hashlib

from . import limits, upload_replay


class UploadRefused(Exception):
    pass


def bitmap_sha256(bitmap):
    return hashlib.sha256(bitmap).hexdigest()


def identity_sha256(bitmap, xx=None):
    """What the limits and the log call "the same upload": the bitmap, plus XX XX when it is not the captured one."""
    return bitmap_sha256(bitmap if xx is None else bitmap + b"XX" + xx.to_bytes(2, "big"))


def send_upload(steps, bitmap, open_device, state_dir=None, day=None, log=print, xx=None):
    sha = identity_sha256(bitmap, xx)
    problems = limits.problems_for_upload(sha, state_dir, day)
    if problems:
        raise UploadRefused("; ".join(problems))
    total, _ = limits.load_limits(state_dir)
    budget = upload_replay.UploadBudget(limits.state_path(limits.BUDGET_FILE, state_dir), total)
    ulog = limits.UploadLog(limits.state_path(limits.LOG_FILE, state_dir))
    when = day or limits.today()
    dev = open_device()                       # a missing device stops here, before anything is counted
    try:
        count = budget.take()                 # the attempt counts, whatever happens next
        ulog.append(date=when, time=datetime.datetime.now().isoformat(timespec="seconds"), sha256=sha, status="started", upload=count)
        log(f"upload {count} of {total} starts now")
        try:
            result = upload_replay.run(dev, steps, log=log)
        except BaseException as err:
            ulog.append(date=when, sha256=sha, status="failed", reason=f"exception: {err!r}")
            raise
    finally:
        dev.close()
    ulog.append(date=when, sha256=sha, status="ok" if result["ok"] else "failed", reason=result["reason"])
    return result
