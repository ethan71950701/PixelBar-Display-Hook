"""List every device-to-host frame in the captures, by command, to look for fields that could mean a size or a limit (read-only, offline).

Usage: python tools/analyze_replies.py DIR_OR_PCAP... [--hex]
Requires: Wireshark's tshark (see analyze_capture.py). Use the FULL captures (the IN replies are in them).

For every device->host frame (report id 2f, tag bb or cc) it prints, per command byte:
  * how many frames, in which captures, the payload lengths seen,
  * every distinct payload with its count (acknowledgement counters `00 03 NN NN` are folded into one line),
  * which host command came right before it (the query it answered),
  * for each payload byte offset the set of values seen, so that a field that changes (a counter, a length, a size)
    can be told from one that never changes.
Nothing here talks to the device.
"""
import argparse
import collections
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analyze_capture as ac  # noqa: E402


def captures(paths):
    found = []
    for p in paths:
        if os.path.isdir(p):
            found += sorted(glob.glob(os.path.join(p, "*.pcap")) + glob.glob(os.path.join(p, "*.pcapng")))
        else:
            found.append(p)
    return found


def frames_of(path, tshark):
    """[(direction, cmd, payload, checksum_ok)] in capture order; direction 'OUT' or 'IN'."""
    out = []
    for _num, _t, _addr, data in ac.read_frames(tshark, path, None):
        direction = ac.MAGIC[data[:3]].strip()
        cmd, length, payload, checksum, expected = ac.parse(data)
        out.append((direction, cmd, bytes(payload), checksum == expected))
    return out


def fold(cmd, payload):
    """Group acknowledgement counters of the scene upload (17: 00 03 NN NN) into one key."""
    if cmd == 0x17 and payload[:2] == b"\x00\x03" and len(payload) == 4:
        return "00 03 <chunk number>"
    return payload.hex(" ")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args(argv)
    tshark = ac.find_tshark()
    seen = collections.defaultdict(lambda: {"count": 0, "caps": set(), "lens": set(), "payloads": collections.Counter(),
                                            "after": collections.Counter(), "bytes": collections.defaultdict(set), "bad": 0})
    totals = collections.Counter()
    for path in captures(args.paths):
        name = os.path.basename(path)
        last_out = None
        for direction, cmd, payload, ok in frames_of(path, tshark):
            totals[direction] += 1
            if direction == "OUT":
                last_out = cmd
                continue
            entry = seen[cmd]
            entry["count"] += 1
            entry["caps"].add(name.split("-")[1] if name.startswith("usb-") and "-" in name else name)
            entry["lens"].add(len(payload))
            entry["payloads"][fold(cmd, payload)] += 1
            entry["after"][last_out] += 1
            entry["bad"] += 0 if ok else 1
            for i, b in enumerate(payload):
                entry["bytes"][i].add(b)
    print(f"device->host frames: {totals['IN']}, host->device frames: {totals['OUT']}")
    for cmd in sorted(seen):
        e = seen[cmd]
        print(f"\ncommand 0x{cmd:02x}: {e['count']} frames, captures {sorted(e['caps'])}, payload lengths {sorted(e['lens'])}, bad checksums {e['bad']}")
        print("  follows host command: " + ", ".join(f"{'none' if k is None else format(k, '#04x')} x{v}" for k, v in e["after"].most_common()))
        for payload, n in e["payloads"].most_common(12):
            print(f"  payload [{payload}] x{n}")
        if len(e["payloads"]) > 12:
            print(f"  ... {len(e['payloads']) - 12} more distinct payloads")
        for i in sorted(e["bytes"]):
            values = sorted(e["bytes"][i])
            shown = " ".join(f"{v:02x}" for v in values[:10]) + (" ..." if len(values) > 10 else "")
            print(f"  byte {i}: {len(values)} value(s): {shown}")


if __name__ == "__main__":
    main()
