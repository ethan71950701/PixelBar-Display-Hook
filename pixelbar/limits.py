"""Upload limits that the command line cannot raise, and the log used to refuse a repeat.

State lives outside the repository in ~/.pixelbar/:
  stage3_uploads.txt  how many uploads were started in total (the budget counter)
  upload_log.jsonl    one line per upload start / end: date, bitmap SHA-256, status
  limits.json         OPTIONAL {"total": N, "per_day": M}; the defaults are 5 and 10.
                      The only way to raise a limit is to edit this file on purpose;
                      there is no command line option for it.
An upload is refused when: the total budget is used up, the daily cap is reached, or
the bitmap is the same as that of the last completed upload (the picture would not
change). A failed attempt still counts against the budget and the day, but does not
count as "the picture is already there". If the screen was changed in TempoHub since
the last upload and the same bitmap really is to be sent again, edit the log file
(remove its last "ok" line) on purpose.
"""
import datetime
import json
import os

from .upload_replay import UploadBudget

STATE_DIR = os.path.join(os.path.expanduser("~"), ".pixelbar")
BUDGET_FILE = "stage3_uploads.txt"
LIMITS_FILE = "limits.json"
LOG_FILE = "upload_log.jsonl"
DEFAULT_TOTAL = 5
DEFAULT_PER_DAY = 10


class LimitsError(Exception):
    pass


def state_path(name, state_dir=None):
    return os.path.join(state_dir or STATE_DIR, name)


def load_limits(state_dir=None):
    """(total, per_day); a damaged limits file stops everything instead of being ignored."""
    path = state_path(LIMITS_FILE, state_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return DEFAULT_TOTAL, DEFAULT_PER_DAY
    except (OSError, ValueError) as err:
        raise LimitsError(f"{path} cannot be read ({err}); fix or delete it") from None
    try:
        total, per_day = int(data.get("total", DEFAULT_TOTAL)), int(data.get("per_day", DEFAULT_PER_DAY))
    except (AttributeError, TypeError, ValueError):
        raise LimitsError(f'{path} must look like {{"total": 5, "per_day": 10}}') from None
    if total < 0 or per_day < 0:
        raise LimitsError(f"{path}: the limits cannot be negative")
    return total, per_day


class UploadLog:
    def __init__(self, path):
        self.path = path

    def entries(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as err:
            raise LimitsError(f"{self.path} cannot be read ({err}); fix or delete it") from None

    def append(self, **entry):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def started_on(self, day):
        return sum(1 for e in self.entries() if e.get("status") == "started" and e.get("date") == day)

    def last_ok_sha(self):
        for e in reversed(self.entries()):
            if e.get("status") == "ok":
                return e.get("sha256")
        return None


def today():
    return datetime.date.today().isoformat()


def problems_for_upload(sha256, state_dir=None, day=None):
    """Reasons to refuse this upload (empty list = allowed)."""
    total, per_day = load_limits(state_dir)
    used = UploadBudget(state_path(BUDGET_FILE, state_dir), total).used()
    log = UploadLog(state_path(LOG_FILE, state_dir))
    problems = []
    if used >= total:
        problems.append(f"the upload budget is used up ({used} of {total}); it can only be raised by editing {state_path(LIMITS_FILE, state_dir)}")
    n_today = log.started_on(day or today())
    if n_today >= per_day:
        problems.append(f"the daily cap is reached ({n_today} of {per_day} today); it can only be raised by editing {state_path(LIMITS_FILE, state_dir)}")
    if log.last_ok_sha() == sha256:
        problems.append("this bitmap is the same as the last completed upload; the picture would not change")
    return problems


def status_line(state_dir=None, day=None):
    total, per_day = load_limits(state_dir)
    used = UploadBudget(state_path(BUDGET_FILE, state_dir), total).used()
    n_today = UploadLog(state_path(LOG_FILE, state_dir)).started_on(day or today())
    return f"uploads used {used} of {total} in total, {n_today} of {per_day} today"
