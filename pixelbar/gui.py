"""A browser window on this computer to prepare pictures and, with your click, upload them.

`python -m pixelbar gui` (or the PixelBar-GUI.bat file) starts a small web server that
listens on 127.0.0.1 only, and opens the page in your browser. Nothing is reachable from
other computers; every request needs a random token that only the page knows, and a wrong
Host header is refused (no DNS-rebinding tricks from other web pages).

What it does: you add pictures, choose for each how it is cropped (move and zoom a frame
of 8:1 over the picture, or fit the whole picture, or stretch it), choose the black and
white conversion, see the exact 256x32 result and the upload checks. Nothing is sent
until you press the upload button and confirm; the server then re-checks everything
(TempoHub not running, limits, the hash of the bitmap you looked at) and runs the same
single bounded upload as the command line (image_send.send_upload): no retry, no loop.

The pictures stay in memory only. A video you open is copied to a temporary folder (deleted when
you open another video or close the program) so that pictures can be taken out of it.
"""
import base64
import contextlib
import hmac
import io
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import device, frames, image, image_send, limits, prepare, upload, upload_replay, video

PAGE = os.path.join(os.path.dirname(__file__), "gui_page.html")
MAX_PICTURE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_STORED = 60                 # pictures kept in memory
MAX_SLIDES = 20                 # one upload (the most tried on the speaker)
SECONDS_MIN, SECONDS_MAX = 0.2, 0.8   # time per picture: the range that was measured (XX XX 100..400, 2 ms per unit)
MS_PER_UNIT = 2
CAPTURED_XX = 0x0064
FITS = ("crop", "contain", "stretch")
MAX_ZOOM = 8.0
RATIO = prepare.WIDTH // prepare.HEIGHT      # 8


class GuiError(Exception):
    """A problem to show to the user (HTTP 400)."""


def tempohub_running():
    """True when TempoHub (or any Edifier program) shows in the process list; also used to refuse a send."""
    if os.name != "nt":
        return False
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return "tempohub" in out or "edifier" in out


def crop_box(width, height, zoom, cx, cy):
    """The 8:1 frame (left, top, right, bottom) in picture pixels: the biggest one at zoom 1, centred on (cx, cy) as fractions."""
    base_w = min(width, RATIO * height)
    box_w = max(RATIO, base_w / max(1.0, min(zoom, MAX_ZOOM)))
    box_w = min(box_w, base_w)
    box_h = box_w / RATIO
    left = min(max(cx * width - box_w / 2, 0), width - box_w)
    top = min(max(cy * height - box_h / 2, 0), height - box_h)
    l, t = round(left), round(top)
    r, b = min(width, max(l + 1, round(left + box_w))), min(height, max(t + 1, round(top + box_h)))
    return l, t, r, b


def number(value, name, low, high, default=None):
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or value != value:
        raise GuiError(f"{name} must be a number between {low} and {high}")
    return float(value)


def check_settings(settings):
    s = settings if isinstance(settings, dict) else {}
    mode = s.get("mode", "dither")
    if mode not in prepare.MODES:
        raise GuiError(f"mode must be one of {prepare.MODES}")
    return {"mode": mode, "threshold": int(number(s.get("threshold"), "threshold", 0, 255, prepare.DEFAULT_THRESHOLD)),
            "invert": bool(s.get("invert", False)), "autocontrast": bool(s.get("autocontrast", False))}


def check_item(item):
    if not isinstance(item, dict):
        raise GuiError("a picture entry is not valid")
    fit = item.get("fit", "crop")
    if fit not in FITS:
        raise GuiError(f"fit must be one of {FITS}")
    return {"id": str(item.get("id", "")), "fit": fit, "zoom": number(item.get("zoom"), "zoom", 1, MAX_ZOOM, 1.0),
            "cx": number(item.get("cx"), "cx", 0, 1, 0.5), "cy": number(item.get("cy"), "cy", 0, 1, 0.5)}


def render_rows(img, item, settings):
    """32 rows of 256 values (0/1) for one picture, exactly as it would be uploaded."""
    if item["fit"] == "crop":
        w, h = img.size
        picture = img.crop(crop_box(w, h, item["zoom"], item["cx"], item["cy"]))
        fit = "stretch"
    else:
        picture, fit = img, item["fit"]
    return prepare.prepare_image(picture, fit=fit, mode=settings["mode"], threshold=settings["threshold"],
                                 invert=settings["invert"], autocontrast=settings["autocontrast"])


def data_url(png):
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class Workspace:
    """The pictures added so far (in memory) and the work the page asks for."""

    def __init__(self, open_device=device.open_device, send_frame=device.send_frame, is_tempohub_running=tempohub_running,
                 state_dir=None):
        self.pictures = {}
        self.video = None            # {"dir", "path", "name", "info"}: the video opened last (a temporary copy)
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.open_device, self.send_frame, self.is_tempohub_running, self.state_dir = open_device, send_frame, is_tempohub_running, state_dir

    def add(self, data, name):
        if len(self.pictures) >= MAX_STORED:
            raise GuiError(f"at most {MAX_STORED} pictures at a time; remove some first")
        try:
            img = prepare.open_bytes(data)
            with contextlib.closing(img):
                _, ImageOps = prepare._pillow()
                oriented = ImageOps.exif_transpose(img).convert("RGBA")      # upright: the crop frame is drawn on what the user sees
        except prepare.PrepareError as err:
            raise GuiError(str(err)) from None
        except (OSError, ValueError) as err:
            raise GuiError(f"cannot read the picture: {err}") from None
        pid = secrets.token_hex(6)
        with self.lock:
            self.pictures[pid] = oriented
        return {"id": pid, "name": name, "width": oriented.width, "height": oriented.height}

    def add_video(self, rfile, length, name):
        """Copy the request body to a temporary file (never more than `length` bytes), check it is a video, keep it."""
        folder = tempfile.mkdtemp(prefix="pixelbar_video_")
        path = os.path.join(folder, "video.bin")
        try:
            with open(path, "wb") as out:
                left = length
                while left > 0:
                    chunk = rfile.read(min(1024 * 1024, left))
                    if not chunk:
                        raise GuiError("the upload ended early")
                    out.write(chunk)
                    left -= len(chunk)
            info = video.probe(path)
        except (video.VideoError, GuiError) as err:
            shutil.rmtree(folder, ignore_errors=True)
            raise GuiError(str(err)) from None
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self.drop_video()
        self.video = {"dir": folder, "path": path, "name": name, "info": info}
        return {"name": name, **info}

    def drop_video(self):
        old, self.video = self.video, None
        if old:
            shutil.rmtree(old["dir"], ignore_errors=True)

    def close(self):
        self.drop_video()

    def video_frames(self, body):
        if self.video is None:
            raise GuiError("open a video first")
        info = self.video["info"]
        start, fps, count = body.get("start", 0), body.get("fps"), body.get("count")
        try:
            video.check_request(start, fps, count, info["duration"])
        except video.VideoError as err:
            raise GuiError(str(err)) from None
        if len(self.pictures) + count > MAX_STORED:
            raise GuiError(f"at most {MAX_STORED} pictures at a time; remove some first")
        try:
            taken = video.extract(self.video["path"], float(start), float(fps), count)
        except video.VideoError as err:
            raise GuiError(str(err)) from None
        out = []
        for t, img in taken:
            jpeg = io.BytesIO()
            img.save(jpeg, "JPEG", quality=88)
            pid = secrets.token_hex(6)
            with self.lock:
                self.pictures[pid] = img.convert("RGBA")
            out.append({"id": pid, "name": f"{self.video['name']} {t:.2f}s", "time": t, "width": img.width, "height": img.height,
                        "url": "data:image/jpeg;base64," + base64.b64encode(jpeg.getvalue()).decode("ascii")})
        return {"frames": out, "wanted": count}

    def remove(self, pid):
        with self.lock:
            self.pictures.pop(pid, None)

    def picture(self, item):
        with self.lock:
            img = self.pictures.get(item["id"])
        if img is None:
            raise GuiError("a picture is no longer loaded here (the window was restarted?); add it again")
        return img

    def render(self, body):
        item, settings = check_item(body.get("item")), check_settings(body.get("settings"))
        rows = render_rows(self.picture(item), item, settings)
        return {"png": data_url(image.rows_to_png(rows, 1)), "lit": sum(map(sum, rows))}

    @staticmethod
    def xx_of(body, count):
        """The record header XX XX for the wanted time per picture (seconds), or None for the captured one.

        Only the measured range (0.2 to 0.8 s) is allowed, and only for several pictures: one picture does not move.
        """
        seconds = body.get("seconds")
        if seconds is None:
            return None
        seconds = number(seconds, "seconds per picture", SECONDS_MIN, SECONDS_MAX)
        xx = round(seconds * 1000 / MS_PER_UNIT)
        return None if xx == CAPTURED_XX or count < 2 else xx

    def build(self, body):
        settings = check_settings(body.get("settings"))
        raw = body.get("items")
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SLIDES:
            raise GuiError(f"add between 1 and {MAX_SLIDES} pictures to the upload list")
        pictures = [render_rows(self.picture(i), i, settings) for i in map(check_item, raw)]
        bitmap = b"".join(image.pack_pixels(rows) for rows in pictures)
        xx = self.xx_of(body, len(pictures))
        steps, checks = upload_replay.load_plan(image=bitmap, xx=xx)
        return pictures, bitmap, steps, checks

    def plan(self, body):
        pictures, bitmap, steps, checks = self.build(body)
        xx = self.xx_of(body, len(pictures))
        sha = image_send.identity_sha256(bitmap, xx)
        try:
            status, problems = limits.status_line(self.state_dir), limits.problems_for_upload(sha, self.state_dir)
        except limits.LimitsError as err:
            status, problems = "limits unreadable", [str(err)]
        running = self.is_tempohub_running()
        return {"pictures": len(pictures), "frames": len(steps), "bytes": len(pictures) * upload.IMAGE_BYTES,
                "seconds": round(0.4 + 0.048 * (len(steps) - 3)), "limit_seconds": round(upload_replay.total_limit_for(steps)),
                "sha": sha, "xx": xx or CAPTURED_XX, "seconds_per_picture": (xx or CAPTURED_XX) * MS_PER_UNIT / 1000,
                "checks": [{"name": k, "ok": bool(v)} for k, v in checks.items()], "limits": status,
                "problems": problems, "tempohub_running": running,
                "can_send": all(checks.values()) and not problems and not running}

    def send(self, body):
        """The one place a picture upload starts. Every condition is checked again here, whatever the page says."""
        if body.get("confirm") is not True:
            raise GuiError("not confirmed; nothing was sent")
        if not self.send_lock.acquire(blocking=False):
            raise GuiError("an upload is already running; nothing was sent")
        try:
            pictures, bitmap, steps, checks = self.build(body)
            xx = self.xx_of(body, len(pictures))
            sha = image_send.identity_sha256(bitmap, xx)
            expect = str(body.get("expect_sha", "")).lower()
            if len(expect) < 12 or not sha.startswith(expect):
                raise GuiError("the pictures changed since the last check (hash mismatch); look at the new preview and confirm again; nothing was sent")
            if not all(checks.values()):
                raise GuiError("plan verification failed; nothing was sent")
            if self.is_tempohub_running():
                raise GuiError("TempoHub (or an Edifier program) is still running: close it fully, also in the system tray; nothing was sent")
            lines = []
            try:
                result = image_send.send_upload(steps, bitmap, self.open_device, state_dir=self.state_dir, log=lines.append, xx=xx)
            except (image_send.UploadRefused, device.DeviceError, limits.LimitsError, RuntimeError) as err:
                raise GuiError(f"{err}; nothing was sent") from None
            return {"ok": result["ok"], "written": result["written"], "of": len(steps), "matched": result["matched"],
                    "unsolicited": [list(map(str, u)) for u in result["unsolicited"]], "elapsed": round(result["elapsed"], 2),
                    "reason": result["reason"], "stopped_at": result["stopped_at"], "raw": result["raw"],
                    "log": lines[:2] + lines[-3:]}
        finally:
            self.send_lock.release()

    def color(self, body):
        if body.get("confirm") is not True:
            raise GuiError("not confirmed; nothing was sent")
        try:
            rgb = frames.parse_hex_color(str(body.get("rgb", "")))
        except ValueError as err:
            raise GuiError(str(err)) from None
        if self.is_tempohub_running():
            raise GuiError("TempoHub (or an Edifier program) is still running: close it fully, also in the system tray; nothing was sent")
        if not self.send_lock.acquire(blocking=False):
            raise GuiError("an upload is already running; nothing was sent")
        try:
            try:
                reply = self.send_frame(frames.color_frame(*rgb))
            except (device.DeviceError, RuntimeError, OSError) as err:
                raise GuiError(f"{err}; nothing further was sent") from None
        finally:
            self.send_lock.release()
        if not reply:
            raise GuiError(f"no reply within {device.READ_TIMEOUT_MS} ms; nothing further was sent")
        if reply != frames.expected_color_reply(*rgb):
            raise GuiError(f"UNEXPECTED reply {reply.rstrip(bytes(1)).hex()}; nothing further was sent")
        return {"ok": True, "reply": reply.rstrip(bytes(1)).hex()}


class Handler(BaseHTTPRequestHandler):
    server_version = "PixelBarGui"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        pass

    def _reply(self, code, payload, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type != "application/json" else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' data: blob:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def _token_ok(self, supplied):
        return hmac.compare_digest(str(supplied or ""), self.server.token)

    def do_GET(self):
        url = urlparse(self.path)
        if not self._host_ok():
            return self._reply(403, {"error": "wrong host"})
        if url.path != "/" or not self._token_ok(parse_qs(url.query).get("t", [""])[0]):
            return self._reply(403, {"error": "open the address printed by the program (it contains the token)"})
        with open(PAGE, encoding="utf-8") as fh:
            page = fh.read().replace("__TOKEN__", self.server.token)
        self._reply(200, page.encode("utf-8"), "text/html")

    def do_POST(self):
        url = urlparse(self.path)
        if not self._host_ok() or not self._token_ok(self.headers.get("X-Token")):
            try:                                   # read a small body first: closing on unread data can reset the client's connection
                self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 65536))
            except (ValueError, OSError):
                pass
            return self._reply(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._reply(400, {"error": "bad length"})
        limit = {"/api/add": MAX_PICTURE_BYTES, "/api/video": MAX_VIDEO_BYTES}.get(url.path, MAX_JSON_BYTES)
        if not 0 <= length <= limit:
            return self._reply(413, {"error": f"too large (at most {limit // (1024 * 1024) or 1} MB)"})
        ws = self.server.workspace
        try:
            if url.path == "/api/video":
                name = parse_qs(url.query).get("name", ["video"])[0][:80]
                return self._reply(200, ws.add_video(self.rfile, length, name))
            raw = self.rfile.read(length)
            if url.path == "/api/add":
                name = parse_qs(url.query).get("name", ["picture"])[0][:80]
                return self._reply(200, ws.add(raw, name))
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                raise GuiError("the request is not valid JSON") from None
            if not isinstance(body, dict):
                raise GuiError("the request is not valid")
            routes = {"/api/render": ws.render, "/api/plan": ws.plan, "/api/send": ws.send, "/api/color": ws.color,
                      "/api/video/frames": ws.video_frames}
            if url.path == "/api/remove":
                ws.remove(str(body.get("id", "")))
                return self._reply(200, {"ok": True})
            if url.path not in routes:
                return self._reply(404, {"error": "unknown request"})
            return self._reply(200, routes[url.path](body))
        except GuiError as err:
            return self._reply(400, {"error": str(err)})
        except (prepare.PrepareError, video.VideoError) as err:
            return self._reply(400, {"error": str(err)})


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, workspace, port=0):
        super().__init__(("127.0.0.1", port), Handler)
        self.workspace = workspace
        self.token = secrets.token_urlsafe(24)

    def server_close(self):
        super().server_close()
        self.workspace.close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}/?t={self.token}"


def serve(open_browser=True, port=0):
    try:
        prepare._pillow()
    except prepare.PrepareError as err:
        raise SystemExit(f"error: {err}") from None
    server = GuiServer(Workspace(), port)
    print(f"PixelBar window: {server.url}", flush=True)
    print("Keep this program open while you use the page. Close it (Ctrl+C or close this window) to stop; nothing is saved.", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
