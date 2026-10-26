"""How many pictures fit under each boundary a field or a size could impose (arithmetic only, nothing is sent).

Usage: python tools/boundaries.py

Format (docs/protocol.md): a picture is 1024 bytes = 8 records of 128 bytes; every record has a 5-byte
header (`T`, 2-byte index `00 NN`, `XX XX`), so a picture costs 8 x 133 = 1064 bytes in the upload stream;
the stream is cut into 50-byte chunks (the last one shorter); the upload is 1 `ef` frame + 1 start frame +
one frame per chunk + 1 end frame. Each line says which field or size the boundary belongs to and how many whole
pictures stay below it; whether the speaker enforces any of them is NOT known (see the documentation).
"""
import math

IMAGE_BYTES, RECORD_DATA, HEADER, CHUNK = 1024, 128, 5, 50
RECORDS_PER_IMAGE = IMAGE_BYTES // RECORD_DATA
STREAM_PER_IMAGE = RECORDS_PER_IMAGE * (RECORD_DATA + HEADER)


def chunks(pictures):
    return math.ceil(pictures * STREAM_PER_IMAGE / CHUNK)


def pictures_within(limit_bytes):
    return limit_bytes // STREAM_PER_IMAGE


def rows():
    out = []
    for label, limit in (("total size 16384 (16 KiB)", 16384), ("total size 32767 (0x7fff)", 32767), ("total size 32768 (32 KiB)", 32768),
                         ("total size 65535 (0xffff)", 65535), ("total size 65536 (64 KiB)", 65536)):
        n = pictures_within(limit)
        out.append((label, f"{n} pictures ({n * STREAM_PER_IMAGE} bytes); the next one needs {(n + 1) * STREAM_PER_IMAGE}"))
    n = 256 // RECORDS_PER_IMAGE
    out.append(("record index 255 (if the index were one byte)", f"{n} pictures use indexes 0..255 exactly ({n * STREAM_PER_IMAGE} bytes); picture {n + 1} needs index 256"))
    n = 128 // RECORDS_PER_IMAGE
    out.append(("record index 127 (if a signed byte)", f"{n} pictures use indexes 0..127 exactly; picture {n + 1} needs index 128"))
    for limit, label in ((256, "chunk number 255 (one byte)"), (65536, "chunk number 65535 (two bytes)")):
        n = max(p for p in range(1, 5000) if chunks(p) <= limit)
        out.append((label, f"{n} pictures ({chunks(n)} chunks, numbers 0..{chunks(n) - 1}); {n + 1} would need {chunks(n + 1)} chunks"))
    out.append(("the code's own limit (MAX_SLIDES)", "20 pictures (the most tried on the speaker: 426 chunks, 21280 bytes)"))
    return out


def main():
    print(f"one picture: {IMAGE_BYTES} image bytes = {RECORDS_PER_IMAGE} records, {STREAM_PER_IMAGE} bytes in the upload stream, "
          f"{STREAM_PER_IMAGE / CHUNK:.2f} chunks of {CHUNK} bytes")
    for n in (1, 2, 7, 20, 30, 31, 32, 33, 61, 62):
        print(f"  {n:>2} pictures: {n * RECORDS_PER_IMAGE:>3} records (last index {n * RECORDS_PER_IMAGE - 1}), {n * STREAM_PER_IMAGE:>5} bytes, "
              f"{chunks(n):>3} chunks (last number {chunks(n) - 1}), {chunks(n) + 3} frames")
    print()
    for label, text in rows():
        print(f"{label}: {text}")


if __name__ == "__main__":
    main()
