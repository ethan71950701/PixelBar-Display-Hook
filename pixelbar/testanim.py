"""A generated test animation of up to 20 pictures (256x32): no picture file, no Pillow.

Picture k (counting from 0): a 14x14 white block at rows 16-29 whose left edge is at
block_x(k), moving from the far left (picture 1) to the far right (picture 20) in equal
steps, and the picture number 1..20 drawn at the top left (6x10 pixel digits). The first N
pictures are the same whatever N is, so a 2-picture, a 7-picture and a 20-picture upload
show the same beginning. Whether the speaker plays them in order, how fast and whether it
shows all of them is what the upload test is meant to find out.
"""
WIDTH, HEIGHT = 256, 32
FRAMES = 20
BLOCK_W = BLOCK_H = 14
BLOCK_TOP = 16
DIGITS = {"0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"), "2": ("111", "001", "111", "100", "111"),
          "3": ("111", "001", "111", "001", "111"), "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
          "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "001", "001"), "8": ("111", "101", "111", "101", "111"),
          "9": ("111", "101", "111", "001", "111")}
SCALE = 2          # a digit is 3x5 font pixels of 2x2 screen pixels
NUMBER_X, NUMBER_Y = 2, 2


def block_x(k):
    return round(k * (WIDTH - BLOCK_W) / (FRAMES - 1))


def frame_rows(k):
    """Picture number k + 1 as 32 rows of 256 values (0 or 1)."""
    if not 0 <= k < FRAMES:
        raise ValueError(f"picture numbers are 1 to {FRAMES}")
    rows = [[0] * WIDTH for _ in range(HEIGHT)]
    x = block_x(k)
    for y in range(BLOCK_TOP, BLOCK_TOP + BLOCK_H):
        for dx in range(BLOCK_W):
            rows[y][x + dx] = 1
    left = NUMBER_X
    for ch in str(k + 1):
        for gy, line in enumerate(DIGITS[ch]):
            for gx, bit in enumerate(line):
                if bit == "1":
                    for dy in range(SCALE):
                        for dx in range(SCALE):
                            rows[NUMBER_Y + gy * SCALE + dy][left + gx * SCALE + dx] = 1
        left += (3 + 1) * SCALE
    return rows


def frames(n):
    """The first n pictures."""
    if not 1 <= n <= FRAMES:
        raise ValueError(f"1 to {FRAMES} pictures")
    return [frame_rows(k) for k in range(n)]
