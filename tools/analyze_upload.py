"""Analyse the scene upload (cmd 0x17) in USBPcap captures of TempoHub (read-only, offline).

Usage: python tools/analyze_upload.py CAPTURE.pcap [CAPTURE.pcap ...] [--png-dir DIR]
Requires: Wireshark's tshark (see analyze_capture.py). Use the FULL captures.

Upload protocol seen in docs/protocol.md ("Scene uploads"), all frames cmd 0x17:
    OUT 00 00 <u32 size>      start; IN 00 01 <u16 chunk size = 50>
    OUT 00 02 <u16 seq> <=50 data bytes   (IN 00 03 <u16 seq>, except for the last chunk)
    OUT 00 04                 end;   IN 00 05
The reassembled data is n records of 133 bytes: a 5-byte header (T 00 NN XX XX)
and 128 bytes of bitmap. The bitmaps are 256x32 1-bit images, 1024 bytes each
(4 pages of 256 columns, one byte = 8 vertical pixels, least significant bit on
top). --png-dir writes them as PNG; keep that directory outside the repository.
"""
import argparse
import collections
import hashlib
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_capture as ac  # noqa: E402

CMD_UPLOAD = 0x17
RECORD_HEADER, RECORD_DATA = 5, 128
WIDTH, HEIGHT = 256, 32
IMAGE_BYTES = WIDTH * HEIGHT // 8


def write_png(path, rows, scale=4):
    """Minimal 8-bit grayscale PNG writer (no dependencies)."""
    raw = b"".join((b"\x00" + bytes(v for v in r for _ in range(scale))) * scale for r in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", len(rows[0]) * scale, len(rows) * scale, 8, 0, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def to_rows(image):
    rows = [[0] * WIDTH for _ in range(HEIGHT)]
    for page in range(HEIGHT // 8):
        for col in range(WIDTH):
            byte = image[page * WIDTH + col]
            for bit in range(8):
                rows[page * 8 + bit][col] = 255 if (byte >> bit) & 1 else 0
    return rows


def entropy(data):
    counts = collections.Counter(data)
    return -sum(n / len(data) * math.log2(n / len(data)) for n in counts.values())


def read_upload(frames):
    up = {"chunks": {}, "acked": set(), "t": {}, "wrote": {}, "ack_at": {}, "starts": 0}
    for num, t, addr, data in frames:
        cmd, ln, payload, ck, exp = ac.parse(data)
        if cmd != CMD_UPLOAD:
            continue
        payload = bytes(payload)
        if data[0] == 0x2E:
            if len(payload) == 6 and payload[:2] == b"\x00\x00":
                up["size"], up["t"]["start"] = int.from_bytes(payload[2:], "big"), t
                up["starts"] += 1
            elif payload[:2] == b"\x00\x02":
                seq = int.from_bytes(payload[2:4], "big")
                up["chunks"][seq] = payload[4:]
                up["wrote"][seq] = t
                up["t"].setdefault("first_chunk", t)
            elif payload == b"\x00\x04":
                up["t"]["end"] = t
        elif payload[:2] == b"\x00\x01":
            up["ready"], up["t"]["ready"] = payload, t
        elif payload[:2] == b"\x00\x03":
            seq = int.from_bytes(payload[2:4], "big")
            up["acked"].add(seq)
            up["ack_at"][seq] = t
        elif payload == b"\x00\x05":
            up["t"]["done"] = t
    return up


DIGITS = {"0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"), "2": ("111", "001", "111", "100", "111"),
          "3": ("111", "001", "111", "001", "111"), "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
          "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "001", "001"), "8": ("111", "101", "111", "101", "111"),
          "9": ("111", "101", "111", "001", "111")}
MARGIN = 10


def contact_sheet(images):
    """All images stacked top to bottom (frame numbers from 1 in the left margin), separated by grey bars."""
    rows = []
    for k, image in enumerate(images):
        frame = to_rows(image)
        for y in range(HEIGHT):
            rows.append([0] * MARGIN + frame[y])
        for d, ch in enumerate(str(k + 1)):          # frame number, 3x5 digits, in the margin
            for gy, line in enumerate(DIGITS[ch]):
                for gx, bit in enumerate(line):
                    if bit == "1":
                        rows[len(rows) - HEIGHT + 2 + gy][1 + d * 4 + gx] = 200
        rows += [[90] * (MARGIN + WIDTH) for _ in range(3)]
    return rows


def ms(seconds):
    return f"{seconds * 1000:.0f}"


def report(path, png_dir, sheet_dir=None):
    name = os.path.basename(path)
    frames = ac.read_frames(ac.find_tshark(), path, None)
    out = [f for f in frames if f[3][0] == 0x2E]
    inn = [f for f in frames if f[3][0] == 0x2F]
    cmds = collections.Counter(ac.parse(f[3])[0] for f in frames)
    span = frames[-1][1] - frames[0][1] if frames else 0
    print(f"== {name}: {len(frames)} frames ({len(out)} OUT, {len(inn)} IN, {64 * len(frames)} bytes on the wire), "
          f"first to last frame {span:.2f}s")
    print("   commands:", ", ".join(f"{c:02x} x{n}" for c, n in sorted(cmds.items())))
    for num, t, addr, data in out:
        cmd, ln, payload, ck, exp = ac.parse(data)
        if cmd == 0xEF and ln == 9:
            p = bytes(payload)
            print(f"   ef (scene select?) t={t:.2f}s payload {p.hex(' ')} (sel {p[0]:02x}, p5 {p[5]:02x}, p6 {p[6]:02x}, p7 {p[7]:02x})")
    if CMD_UPLOAD not in cmds:
        print("   no cmd 0x17 upload frames")
        return None
    up = read_upload(frames)
    if up["starts"] != 1:
        print(f"   WARNING: {up['starts']} upload start frames in this capture; the numbers below mix them")
    seqs = sorted(up["chunks"])
    blob = b"".join(up["chunks"][s] for s in seqs)
    last = seqs[-1] if seqs else None
    t = up["t"]
    print(f"   upload: declared {up.get('size')} bytes, {len(seqs)} chunks (sizes {dict(collections.Counter(len(c) for c in up['chunks'].values()))}), "
          f"reassembled {len(blob)} bytes; ready reply {up.get('ready', b'').hex()}")
    print(f"   checks: size matches {len(blob) == up.get('size')}; seq contiguous {seqs == list(range(len(seqs)))}; "
          f"every chunk but the last acked {up['acked'] == set(seqs) - {last}}; end acked {'done' in t}")
    print(f"   timing: start {t.get('start', 0):.3f}s, ready {t.get('ready', 0):.3f}s, chunks {t.get('first_chunk', 0):.3f}s .. end {t.get('end', 0):.3f}s, "
          f"done {t.get('done', 0):.3f}s -> {t.get('done', 0) - t.get('start', 0):.2f}s total")
    wrote, ack_at = up["wrote"], up["ack_at"]
    latency = sorted(ack_at[s] - wrote[s] for s in ack_at if s in wrote)
    times = sorted(wrote.values())
    gaps = sorted(b - a for a, b in zip(times, times[1:]))
    if latency and gaps:
        print(f"   device acknowledgement delay per chunk (ms): min {ms(latency[0])}, median {ms(latency[len(latency) // 2])}, max {ms(latency[-1])}; "
              f"host spacing between chunks (ms): min {ms(gaps[0])}, median {ms(gaps[len(gaps) // 2])}, max {ms(gaps[-1])}")
        print(f"   start -> ready {ms(t['ready'] - t['start'])} ms, end -> done {ms(t['done'] - t['end'])} ms; "
              f"{len(blob) / (t['done'] - t['start']):.0f} bytes/s over the whole upload")
    n = len(blob) // (RECORD_HEADER + RECORD_DATA)
    recs = [blob[i * 133:(i + 1) * 133] for i in range(n)]
    nn = [int.from_bytes(r[1:3], "big") for r in recs]
    print(f"   records: {n} x 133 bytes (+{len(blob) - n * 133} left over), first header {recs[0][:5].hex(' ')}")
    print(f"   record header T/XX: T={sorted({r[0] for r in recs})} XX={sorted({r[3:5].hex() for r in recs})} NN {nn[0]}..{nn[-1]} "
          f"{'contiguous' if nn == list(range(n)) else 'NOT contiguous'}")
    data = b"".join(r[RECORD_HEADER:] for r in recs)
    zero = data.count(0)
    print(f"   data: {len(data)} bytes = {len(data) / IMAGE_BYTES:g} image(s) of {WIDTH}x{HEIGHT}; zero bytes {100 * zero / len(data):.0f}%, "
          f"entropy {entropy(data):.2f} bits/byte, sha256 {hashlib.sha256(data).hexdigest()[:16]}")
    if len(data) % IMAGE_BYTES == 0:
        images = [data[k * IMAGE_BYTES:(k + 1) * IMAGE_BYTES] for k in range(len(data) // IMAGE_BYTES)]
        lit = [sum(bin(b).count("1") for b in im) for im in images]
        print(f"   images: {len(images)}; lit pixels per image {lit}")
        if len(images) > 1:
            step = [sum(bin(a ^ b).count("1") for a, b in zip(images[k], images[k + 1])) for k in range(len(images) - 1)]
            print(f"   pixels that differ between image k and k+1 (k = 1..): {step}; identical images: "
                  f"{[(a + 1, b + 1) for a in range(len(images)) for b in range(a + 1, len(images)) if images[a] == images[b]] or 'none'}")
        if png_dir:
            os.makedirs(png_dir, exist_ok=True)
            for k, im in enumerate(images):
                f = os.path.join(png_dir, f"{name[:-5]}_img{k}.png")
                write_png(f, to_rows(im))
                print("   wrote", f)
        if sheet_dir:
            os.makedirs(sheet_dir, exist_ok=True)
            f = os.path.join(sheet_dir, f"{name[:-5]}_sheet.png")
            write_png(f, contact_sheet(images), 3)
            print("   wrote contact sheet", f, f"({len(images)} frame(s), numbered from 1 from the top)")
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--png-dir", help="write the images here (keep it outside the repository)")
    ap.add_argument("--sheet-dir", help="write one contact sheet per capture here, all frames stacked and numbered (outside the repository)")
    args = ap.parse_args()
    digests = {}
    for path in args.captures:
        digests[os.path.basename(path)] = report(path, args.png_dir, args.sheet_dir)
    same = collections.defaultdict(list)
    for name, d in digests.items():
        if d:
            same[d].append(name)
    for names in same.values():
        if len(names) > 1:
            print("identical uploaded data:", ", ".join(names))


if __name__ == "__main__":
    main()
