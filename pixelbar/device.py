"""Write one 64-byte report to the PixelBar and read one reply.

Opens the vendor HID collection with usage page 0xFF14 (found with hidapi, no
hardcoded path). One write, one read of up to 2 s; no loop, no retry. The only
caller is the CLI, which builds frames in pixelbar.frames.
"""
import os
import subprocess

VID, PID, USAGE_PAGE = 0x2D99, 0xA106, 0xFF14
READ_TIMEOUT_MS = 2000


class DeviceError(Exception):
    pass


def tempohub_running():
    if os.name != "nt":
        return False
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return "tempohub" in out


def find_path(hid):
    candidates = hid.enumerate(VID, PID)
    matches = [d for d in candidates if d["usage_page"] == USAGE_PAGE]
    if len(matches) != 1:
        listing = "\n".join(
            f"  usage_page=0x{d['usage_page']:04x} usage=0x{d['usage']:x} "
            f"iface={d['interface_number']} path={d['path']!r}" for d in candidates)
        raise DeviceError(f"expected exactly one HID path with usage_page 0x{USAGE_PAGE:04x}, "
                          f"found {len(matches)}; all candidates for {VID:04x}:{PID:04x}:\n{listing}")
    return matches[0]["path"]


def send_frame(frame):
    """Write `frame` (64 bytes, Report ID first) and return the first reply (may be empty)."""
    if len(frame) != 64:
        raise DeviceError("frame must be exactly 64 bytes")
    if tempohub_running():
        raise DeviceError("a TempoHub process is running; close it first (nothing was sent)")
    try:
        import hid
    except ImportError:
        raise DeviceError("hidapi missing: pip install hidapi")
    path = find_path(hid)
    dev = hid.device()
    dev.open_path(path)
    try:
        written = dev.write(frame)
        if written != len(frame):
            raise DeviceError(f"write() returned {written}, expected 64: {dev.error()}")
        return bytes(dev.read(64, READ_TIMEOUT_MS))
    finally:
        dev.close()
