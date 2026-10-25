"""Summarise PixelBar HID frames in a USBPcap capture (read-only, offline).

Usage: python tools/analyze_capture.py captures/usb-01-open-app.pcap [--dev 9] [--timeline]
       python tools/analyze_capture.py captures/usb-06-replug.pcap --descriptors
Requires: Wireshark's tshark (found on PATH or in C:\\Program Files\\Wireshark),
except --descriptors, which reads classic .pcap files directly (a replug
capture is needed: the HID report descriptors are sent during enumeration).

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
import struct
import subprocess
import sys

MAGIC = {b"\x2e\xaa\xec": "OUT", b"\x2f\xbb\xec": "IN "}

USAGE_PAGES = {0x01: "Generic Desktop", 0x0C: "Consumer"}
MAIN_ITEMS = {0x8: "Input", 0x9: "Output", 0xB: "Feature", 0xA: "Collection", 0xC: "End Collection"}


def read_pcap(path):
    """Yield raw packets of a classic little-endian .pcap (USBPcap, linktype 249)."""
    with open(path, "rb") as fh:
        head = fh.read(24)
        if head[:4] != b"\xd4\xc3\xb2\xa1":
            sys.exit("--descriptors needs a classic little-endian .pcap (not pcapng)")
        while True:
            rec = fh.read(16)
            if len(rec) < 16:
                return
            incl = struct.unpack("<IIII", rec)[2]
            yield fh.read(incl)


def hid_report_descriptors(path):
    """Return {(device, interface): bytes} for GET_DESCRIPTOR(HID report) replies."""
    found = {}
    pending = {}
    for pkt in read_pcap(path):
        if len(pkt) < 28:
            continue
        hdr_len, _irp, _status, _func, info, _bus, dev, endpoint, transfer = struct.unpack(
            "<H8sIHBHHBB", pkt[:22 + 1])[:9]
        if transfer != 2:
            continue
        stage, body = pkt[27], pkt[hdr_len:]
        if stage == 0 and not info & 1 and len(body) == 8:
            bm, req, value, index, _length = struct.unpack("<BBHHH", body)
            if (bm, req, value >> 8) == (0x81, 6, 0x22):
                pending[dev] = index
        elif stage == 3 and info & 1 and dev in pending and body:
            found[(dev, pending.pop(dev))] = bytes(body)
    return found


def decode_report_descriptor(desc):
    """Print a flat, indented decode of a HID report descriptor."""
    i, depth = 0, 0
    while i < len(desc):
        prefix = desc[i]
        size = (0, 1, 2, 4)[prefix & 3]
        tag, kind = prefix >> 4, (prefix >> 2) & 3
        value = int.from_bytes(desc[i + 1:i + 1 + size], "little")
        i += 1 + size
        if kind == 0:
            name = MAIN_ITEMS.get(tag, f"Main(0x{tag:x})")
            if name == "End Collection":
                depth -= 1
            print("    " + "  " * depth + f"{name} 0x{value:02x}" if size else "    " + "  " * depth + name)
            if name == "Collection":
                depth += 1
        elif kind == 1:
            names = {0: "Usage Page", 1: "Logical Min", 2: "Logical Max", 7: "Report Size",
                     8: "Report ID", 9: "Report Count"}
            label = names.get(tag, f"Global(0x{tag:x})")
            extra = f" ({USAGE_PAGES[value]})" if tag == 0 and value in USAGE_PAGES else ""
            print("    " + "  " * depth + f"{label} 0x{value:x}{extra}")
        else:
            print("    " + "  " * depth + f"Usage 0x{value:x}" if tag == 0 else "    " + "  " * depth + f"Local(0x{tag:x}) 0x{value:x}")


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
    ap.add_argument("--descriptors", action="store_true",
                    help="decode HID report descriptors (replug capture, .pcap only)")
    args = ap.parse_args()

    if args.descriptors:
        found = hid_report_descriptors(args.pcap)
        if not found:
            sys.exit("no HID report descriptors found (was the device replugged during the capture?)")
        for (dev, iface), desc in sorted(found.items()):
            print(f"device {dev} interface {iface}: {len(desc)} bytes\n  {desc.hex()}")
            decode_report_descriptor(desc)
        return

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
