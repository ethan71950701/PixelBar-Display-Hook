"""Take still pictures out of a video file (for the picture window). Needs PyAV (pip install av).

PyAV is imported only here, and only when a video is opened; everything else works without it.
Nothing here talks to the device.
"""
import time

MAX_FRAMES = 20
MAX_SIDE = 960              # extracted pictures are shrunk to this many pixels on the long side
MIN_FPS, MAX_FPS = 0.05, 60.0
DECODE_SECONDS = 60         # give up after this long and return what was found


class VideoError(Exception):
    pass


def _av():
    try:
        import av
    except ImportError:
        raise VideoError("PyAV is not installed. Videos need it: pip install av (everything else works without it)") from None
    return av


def probe(path):
    """{duration, fps, width, height} of the first video stream; refuses files that are not videos."""
    av = _av()
    try:
        with av.open(path) as container:
            if not container.streams.video:
                raise VideoError("the file has no video stream")
            stream = container.streams.video[0]
            duration = float(container.duration) / 1e6 if container.duration else None
            if duration is None and stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            fps = float(stream.average_rate) if stream.average_rate else None
            width, height = stream.codec_context.width, stream.codec_context.height
    except VideoError:
        raise
    except Exception as err:               # PyAV raises its own error classes for broken or unsupported files
        raise VideoError(f"cannot read the video: {err}") from None
    if not width or not height or width > 16384 or height > 16384:
        raise VideoError("the video has an unusable picture size")
    return {"duration": duration, "fps": fps, "width": width, "height": height}


def check_request(start, fps, count, duration):
    if not (isinstance(start, (int, float)) and not isinstance(start, bool) and 0 <= start <= (duration if duration else 1e9)):
        raise VideoError("the start time must be inside the video")
    if not (isinstance(fps, (int, float)) and not isinstance(fps, bool) and MIN_FPS <= fps <= MAX_FPS):
        raise VideoError(f"pictures per second must be between {MIN_FPS} and {MAX_FPS}")
    if not (isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= MAX_FRAMES):
        raise VideoError(f"the number of pictures must be between 1 and {MAX_FRAMES}")


def extract(path, start, fps, count, max_side=MAX_SIDE, clock=time.monotonic):
    """The first picture at or after start + k / fps for k = 0 .. count - 1, as [(time in seconds, Pillow image)].

    Fewer than `count` are returned when the video ends first (or after DECODE_SECONDS of work).
    """
    av = _av()
    wanted = [start + k / fps for k in range(count)]
    found = []
    deadline = clock() + DECODE_SECONDS
    try:
        with av.open(path) as container:
            stream = container.streams.video[0]
            if start > 1:
                container.seek(int((start - 0.5) / float(stream.time_base)), stream=stream, backward=True)
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                t = float(frame.pts * stream.time_base)
                if t + 1e-6 < wanted[len(found)]:
                    if clock() > deadline:
                        break
                    continue
                img = frame.to_image().convert("RGB")
                if max(img.size) > max_side:
                    scale = max_side / max(img.size)
                    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
                found.append((round(t, 3), img))
                if len(found) == count or clock() > deadline:
                    break
    except VideoError:
        raise
    except Exception as err:
        raise VideoError(f"cannot decode the video: {err}") from None
    if not found:
        raise VideoError("no picture could be taken at that time (is the start past the end of the video?)")
    return found
