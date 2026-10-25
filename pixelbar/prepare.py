"""Prepare any picture as a 256x32 black-and-white image for a scene upload.

Needs Pillow (pip install pillow). Pillow is imported only here, and only when a
picture is opened: the frame builder and every other command work without it.

Pipeline (the order matters):
  1. open; refuse pictures larger than 8000 pixels on a side; turn phone photos
     upright by their EXIF orientation
  2. optionally invert the colours (the alpha channel is kept), then put the
     picture on a black background: transparent pixels become black
  3. to greyscale; scale to 256x32 with --fit:
       cover    fills the screen, the excess is cropped (--focus-x / --focus-y, 0..1,
                choose which part of the picture is kept); a 8:1 screen usually
                keeps only a horizontal band across the middle of the picture
       contain  scaled to fit, centred, black bars (a small picture is enlarged)
       stretch  stretched to 256x32, the proportions change
  4. optionally stretch the contrast (--autocontrast)
  5. black and white: --mode dither (Floyd-Steinberg error diffusion, default) or
     --mode threshold; a pixel is lit when its brightness (with the diffused error
     in dither mode) is at or above --threshold (default 128)
Nothing here talks to the device.
"""
import math

WIDTH, HEIGHT = 256, 32
MAX_SIDE = 8000
FITS = ("cover", "contain", "stretch")
MODES = ("dither", "threshold")
DEFAULT_THRESHOLD = 128


class PrepareError(Exception):
    pass


def _pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise PrepareError("Pillow is not installed. The image and prepare commands need it: pip install pillow "
                           "(all other commands work without it)") from None
    return Image, ImageOps


def _check_size(img):
    w, h = img.size
    if w < 1 or h < 1:
        raise PrepareError("the picture is empty")
    if w > MAX_SIDE or h > MAX_SIDE:
        raise PrepareError(f"the picture is {w}x{h}; more than {MAX_SIDE} pixels on a side is refused")


def open_bytes(data):
    """Open a picture from bytes already in memory (the browser window); same size checks as open_image."""
    import io
    return open_image(io.BytesIO(data), "the uploaded picture")


def open_image(path, label=None):
    Image, _ = _pillow()
    Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE
    img = None
    try:
        img = Image.open(path)
        _check_size(img)          # the size is known before the picture is decoded
        img.load()
    except PrepareError:
        if img is not None:
            img.close()
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as err:
        if img is not None:
            img.close()
        raise PrepareError(f"cannot read {label or path}: {err}") from None
    return img


def _binarize(gray, mode, threshold):
    pixels = list(gray.getdata())
    rows = [pixels[y * WIDTH:(y + 1) * WIDTH] for y in range(HEIGHT)]
    if mode == "threshold":
        return [[1 if v >= threshold else 0 for v in row] for row in rows]
    work = [[float(v) for v in row] for row in rows]       # Floyd-Steinberg, left to right, top to bottom
    out = [[0] * WIDTH for _ in range(HEIGHT)]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            old = work[y][x]
            new = 255.0 if old >= threshold else 0.0
            out[y][x] = 1 if new else 0
            err = old - new
            if x + 1 < WIDTH:
                work[y][x + 1] += err * 7 / 16
            if y + 1 < HEIGHT:
                if x > 0:
                    work[y + 1][x - 1] += err * 3 / 16
                work[y + 1][x] += err * 5 / 16
                if x + 1 < WIDTH:
                    work[y + 1][x + 1] += err * 1 / 16
    return out


def prepare_image(img, fit="cover", focus_x=0.5, focus_y=0.5, mode="dither", threshold=DEFAULT_THRESHOLD,
                  invert=False, autocontrast=False):
    """Return 32 rows of 256 values (0 = dark, 1 = lit) from an opened Pillow image."""
    if fit not in FITS or mode not in MODES:
        raise PrepareError(f"--fit is one of {FITS}, --mode one of {MODES}")
    if not (0 <= focus_x <= 1 and 0 <= focus_y <= 1):
        raise PrepareError("--focus-x and --focus-y are between 0 and 1")
    if not 0 <= threshold <= 255:
        raise PrepareError("--threshold is between 0 and 255")
    Image, ImageOps = _pillow()
    _check_size(img)
    try:
        rgba = ImageOps.exif_transpose(img).convert("RGBA")
    except (OSError, ValueError) as err:
        raise PrepareError(f"cannot convert the picture: {err}") from None
    if invert:                                              # colours only: transparent stays transparent, so black
        r, g, b, a = rgba.split()
        inverted = ImageOps.invert(Image.merge("RGB", (r, g, b))).split()
        rgba = Image.merge("RGBA", (*inverted, a))
    base = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    base.alpha_composite(rgba)
    gray = base.convert("L")
    resample = getattr(Image, "Resampling", Image).LANCZOS

    def contrast(picture):
        return ImageOps.autocontrast(picture) if autocontrast else picture

    w, h = gray.size
    if fit == "stretch":
        final = contrast(gray.resize((WIDTH, HEIGHT), resample))
    elif fit == "contain":
        scale = min(WIDTH / w, HEIGHT / h)
        size = (min(WIDTH, max(1, round(w * scale))), min(HEIGHT, max(1, round(h * scale))))
        final = Image.new("L", (WIDTH, HEIGHT), 0)
        final.paste(contrast(gray.resize(size, resample)), ((WIDTH - size[0]) // 2, (HEIGHT - size[1]) // 2))
    else:
        scale = max(WIDTH / w, HEIGHT / h)
        size = (max(WIDTH, math.ceil(w * scale)), max(HEIGHT, math.ceil(h * scale)))
        big = gray.resize(size, resample)
        left, top = round((size[0] - WIDTH) * focus_x), round((size[1] - HEIGHT) * focus_y)
        final = contrast(big.crop((left, top, left + WIDTH, top + HEIGHT)))
    return _binarize(final, mode, threshold)


def prepare_file(path, **options):
    return prepare_image(open_image(path), **options)
