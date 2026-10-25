"""Summarise PixelBar HID frames in a USBPcap capture (read-only, offline).

Usage: python tools/analyze_capture.py captures/usb-01-open-app.pcap [--dev 9] [--timeline]
Requires: Wireshark's tshark (found on PATH or in C:\\Program Files\\Wireshark).

Frame layout seen so far (docs/protocol.md), 64-byte HID reports:
    magic(3) cmd(1) len(2, big endian) payload(len) checksum(1) zero padding
    host->device magic 2e aa ec, device->host magic 2f bb ec
    checksum = sum of bytes 1..end of payload, mod 256

Use the FULL capture. A capture pre-filtered to host->device URBs drops the
device's IN replies, which Wireshark shows as "completion" packets.
"""
import argparse
import collections
import os
import shutil
import subprocess
import sys

MAGIC = {b"\x2e\xaa\xec": "OUT", b"\x2f\xbb\xec": "IN "}


def find_tshark():
    path = shutil.which("tshark")
    if path:
        return path
    default = r"C:\Program Files\Wireshark\tshark.exe"
    if os.path.exists(default):
        return default
    sys.exit("tshark not found (install Wireshark or add it to PATH)")


def read_frames(tshark, pcap, dev):
    display = "usb.transfer_type==1 && usb.data_len>0"
    if dev is not None:
        display += f" && usb.device_address=={dev}"
    run = subprocess.run(
        [tshark, "-r", pcap, "-Y", display, "-T", "fields",
         "-e", "frame.number", "-e", "frame.time_relative",
         "-e", "usb.device_address", "-e", "usb.capdata", "-e", "usbhid.data",
         "-E", "separator=|"],
        capture_output=True, text=True)
    if run.returncode:
        sys.exit(f"tshark failed: {run.stderr.strip()}")
    out = run.stdout
    frames = []
    for line in out.splitlines():
        num, t, addr, cap, hid = line.split("|")
        data = bytes.fromhex(cap or hid)
        if data[:3] in MAGIC:
            frames.append((int(num), float(t), int(addr), data))
    return frames


def parse(data):
    cmd = data[3]
    length = int.from_bytes(data[4:6], "big")
    payload = data[6:6 + length]
    checksum = data[6 + length]
    expected = sum(data[1:6 + length]) & 0xFF
    return cmd, length, payload, checksum, expected


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pcap")
    ap.add_argument("--dev", type=int, help="USB device address (default: any)")
    ap.add_argument("--timeline", action="store_true", help="print every frame in order")
    args = ap.parse_args()

    frames = read_frames(find_tshark(), args.pcap, args.dev)
    if not frames:
        sys.exit("no PixelBar frames found (wrong file, or IN replies filtered out?)")

    print(f"frames: {len(frames)}  devices: {sorted({f[2] for f in frames})}")
    print("direction:", collections.Counter(MAGIC[f[3][:3]] for f in frames))
    if not any(f[3][:3] == b"\x2f\xbb\xec" for f in frames):
        print("WARNING: no device->host (IN) frames; the capture may have been "
              "filtered to host->device URBs only.")
    print("frame lengths:", collections.Counter(len(f[3]) for f in frames))

    bad = 0
    print("\nunique frames (count, direction, cmd, len, payload):")
    counts = collections.Counter((MAGIC[f[3][:3]], f[3]) for f in frames)
    for (direction, data), n in sorted(counts.items(), key=lambda kv: (kv[0][0], -kv[1])):
        cmd, length, payload, checksum, expected = parse(data)
        flag = "" if checksum == expected else f"  CHECKSUM MISMATCH (got {checksum:02x}, want {expected:02x})"
        bad += checksum != expected
        print(f"  x{n:<3} {direction} cmd={cmd:02x} len={length:<3} {payload.hex() or '-'}{flag}")
    print("checksum mismatches:", bad)

    if args.timeline:
        print("\ntimeline:")
        for num, t, addr, data in frames:
            cmd, length, payload, *_ = parse(data)
            print(f"  #{num:<5} t={t:8.3f}s {MAGIC[data[:3]]} cmd={cmd:02x} len={length:<3} {payload.hex() or '-'}")


if __name__ == "__main__":
    main()
