"""The browser window (pixelbar/gui.py): crop maths, the local server's refusals, planning and the one send path.

No real device is ever opened: the workspace gets a fake device and a fake "TempoHub running" answer.

Run: python -m unittest discover -s tests -t .
"""
import http.client
import io
import json
import tempfile
import threading
import unittest

from PIL import Image

from pixelbar import frames, gui, image, image_send, limits, upload_replay
from tests.test_upload_replay import Clock, FakeDevice


def picture_bytes(width=400, height=300, shade=200):
    img = Image.new("RGB", (width, height), (0, 0, 0))
    for x in range(width // 4, width // 2):
        for y in range(height // 4, height // 2):
            img.putpixel((x, y), (shade, shade, shade))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


class ClosingDevice(FakeDevice):
    closed = False

    def close(self):
        self.closed = True


class CropBoxTests(unittest.TestCase):
    def test_zoom_one_is_the_biggest_8_to_1_frame(self):
        l, t, r, b = gui.crop_box(1000, 1000, 1, .5, .5)
        self.assertEqual((l, r), (0, 1000))
        self.assertAlmostEqual((t + b) / 2, 500, delta=1)
        self.assertAlmostEqual(b - t, 125, delta=1)
        self.assertEqual(gui.crop_box(256, 32, 1, .5, .5), (0, 0, 256, 32))
        l, t, r, b = gui.crop_box(100, 100, 1, .5, .5)
        self.assertEqual((l, r), (0, 100))
        self.assertAlmostEqual((r - l) / (b - t), 8, delta=.7)

    def test_zoom_shrinks_the_frame_and_it_stays_inside_the_picture(self):
        for zoom in (1, 2, 4, 8):
            for cx, cy in ((0, 0), (1, 1), (.5, .5), (.2, .9)):
                l, t, r, b = gui.crop_box(640, 480, zoom, cx, cy)
                self.assertTrue(0 <= l < r <= 640 and 0 <= t < b <= 480, (zoom, cx, cy, (l, t, r, b)))
        l, t, r, b = gui.crop_box(640, 480, 4, .5, .5)
        self.assertAlmostEqual(r - l, 640 / 4, delta=1)

    def test_the_frame_follows_the_centre(self):
        left = gui.crop_box(1000, 1000, 4, 0, .5)
        right = gui.crop_box(1000, 1000, 4, 1, .5)
        self.assertEqual((left[0], right[2]), (0, 1000))


class ValidationTests(unittest.TestCase):
    def test_bad_values_are_refused(self):
        for bad in ({"fit": "weird"}, {"zoom": 0.5}, {"zoom": 9}, {"cx": 1.5}, {"cy": -1}, {"zoom": "2"}, {"zoom": True}):
            with self.subTest(bad=bad), self.assertRaises(gui.GuiError):
                gui.check_item({"id": "a", **bad})
        for bad in ({"mode": "x"}, {"threshold": 300}, {"threshold": "a"}):
            with self.subTest(bad=bad), self.assertRaises(gui.GuiError):
                gui.check_settings(bad)

    def test_defaults(self):
        self.assertEqual(gui.check_item({"id": "a"}), {"id": "a", "fit": "crop", "zoom": 1.0, "cx": .5, "cy": .5})
        self.assertEqual(gui.check_settings(None), {"mode": "dither", "threshold": 128, "invert": False, "autocontrast": False})


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        self.opened = []
        self.clock = Clock()
        self.running = False
        self.frames_sent = []
        self.reply = None
        self.script = {}
        self.ws = gui.Workspace(open_device=self.open_device, send_frame=self.send_frame,
                                is_tempohub_running=lambda: self.running, state_dir=self.state.name)
        self.server = gui.GuiServer(self.ws)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def open_device(self):
        dev = ClosingDevice(self.clock, self.script)
        self.opened.append(dev)
        return dev

    def send_frame(self, frame):
        self.frames_sent.append(frame)
        return self.reply

    def request(self, method, path, body=None, token=True, host=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        headers = {"Host": host or f"127.0.0.1:{self.server.server_port}"}
        if token:
            headers["X-Token"] = self.server.token
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(payload)
        except ValueError:
            return resp.status, payload

    def add(self, **kw):
        status, info = self.request("POST", "/api/add?name=a.png", raw=picture_bytes(**kw))
        self.assertEqual(status, 200, info)
        return info

    def body(self, ids, **settings):
        return {"items": [{"id": i, "fit": "crop", "zoom": 1, "cx": .5, "cy": .5} for i in ids],
                "settings": {"mode": "threshold", **settings}}

    # ---- who may talk to the server
    def test_page_needs_the_token_and_the_right_host(self):
        port = self.server.server_port
        for path, host in ((f"/", None), (f"/?t=wrong", None), (f"/?t={self.server.token}", "evil.example:80")):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
            self.assertEqual(conn.getresponse().status, 403, (path, host))
            conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", f"/?t={self.server.token}", headers={"Host": f"127.0.0.1:{port}"})
        resp = conn.getresponse()
        page = resp.read().decode()
        self.assertEqual(resp.status, 200)
        self.assertIn(self.server.token, page)
        self.assertNotIn("__TOKEN__", page)
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))

    def test_api_refuses_without_token_or_with_a_wrong_host(self):
        self.assertEqual(self.request("POST", "/api/plan", {}, token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/plan", {}, host="evil.example:80")[0], 403)
        self.assertEqual(self.request("POST", "/api/nothing", {})[0], 404)
        self.assertEqual(self.request("POST", "/api/plan", raw=b"{not json")[0], 400)

    def test_the_server_listens_on_localhost_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    # ---- pictures, rendering, planning
    def test_add_render_and_plan_agree_with_the_bitmap_that_would_be_sent(self):
        info = self.add()
        self.assertEqual((info["width"], info["height"]), (400, 300))
        body = self.body([info["id"]])
        status, rendered = self.request("POST", "/api/render", {"item": body["items"][0], "settings": body["settings"]})
        self.assertEqual(status, 200, rendered)
        self.assertTrue(rendered["png"].startswith("data:image/png;base64,"))
        self.assertGreater(rendered["lit"], 0)
        status, plan = self.request("POST", "/api/plan", body)
        self.assertEqual(status, 200, plan)
        pictures, bitmap, steps, checks = self.ws.build(body)
        self.assertEqual(plan["sha"], image_send.bitmap_sha256(bitmap))
        self.assertEqual((plan["pictures"], plan["frames"], plan["bytes"]), (1, 25, 1024))
        self.assertTrue(plan["can_send"], plan)
        self.assertTrue(all(c["ok"] for c in plan["checks"]))
        self.assertEqual(sum(map(sum, pictures[0])), rendered["lit"])
        self.assertEqual(self.opened, [])                                   # planning never touches the device

    def test_the_crop_changes_the_result(self):
        info = self.add()
        left = self.ws.render({"item": {"id": info["id"], "zoom": 4, "cx": .2, "cy": .3}, "settings": {"mode": "threshold"}})
        right = self.ws.render({"item": {"id": info["id"], "zoom": 4, "cx": .9, "cy": .9}, "settings": {"mode": "threshold"}})
        self.assertNotEqual(left["png"], right["png"])

    def test_invert_and_modes_change_the_bitmap(self):
        info = self.add()
        item = {"id": info["id"], "fit": "contain"}
        plain = self.ws.render({"item": item, "settings": {"mode": "threshold"}})
        inverted = self.ws.render({"item": item, "settings": {"mode": "threshold", "invert": True}})
        self.assertNotEqual(plain["lit"], inverted["lit"])

    def test_bad_input_is_a_clear_400(self):
        status, err = self.request("POST", "/api/add?name=x", raw=b"not a picture")
        self.assertEqual(status, 400)
        self.assertIn("cannot read", err["error"])
        info = self.add()
        self.assertEqual(self.request("POST", "/api/plan", {"items": [], "settings": {}})[0], 400)
        self.assertEqual(self.request("POST", "/api/plan", {"items": [{"id": "nope"}], "settings": {}})[0], 400)
        too_many = {"items": [{"id": info["id"]}] * 21, "settings": {}}
        self.assertEqual(self.request("POST", "/api/plan", too_many)[0], 400)

    def test_twenty_pictures_can_be_planned(self):
        info = self.add()
        status, plan = self.request("POST", "/api/plan", self.body([info["id"]] * 20))
        self.assertEqual((status, plan["pictures"], plan["frames"]), (200, 20, 429))

    def test_plan_reports_a_running_tempohub_and_cannot_send(self):
        info = self.add()
        self.running = True
        _, plan = self.request("POST", "/api/plan", self.body([info["id"]]))
        self.assertTrue(plan["tempohub_running"])
        self.assertFalse(plan["can_send"])

    # ---- the one send path
    def prepare_send(self, ids=None):
        info = self.add()
        body = self.body(ids or [info["id"]])
        _, bitmap, steps, _ = self.ws.build(body)
        self.script = {i: [s[2]] for i, s in enumerate(steps) if s[2] is not None}
        body["expect_sha"] = image_send.bitmap_sha256(bitmap)[:16]
        body["confirm"] = True
        return body, steps

    def test_send_is_refused_before_the_device_is_opened(self):
        body, _ = self.prepare_send()
        cases = {"not confirmed": {**body, "confirm": False}, "hash mismatch": {**body, "expect_sha": "0" * 16},
                 "short hash": {**body, "expect_sha": body["expect_sha"][:6]}, "no hash": {k: v for k, v in body.items() if k != "expect_sha"}}
        for name, case in cases.items():
            with self.subTest(name):
                status, err = self.request("POST", "/api/send", case)
                self.assertEqual(status, 400, err)
                self.assertIn("nothing was sent", err["error"])
        self.running = True
        status, err = self.request("POST", "/api/send", body)
        self.assertEqual(status, 400)
        self.assertIn("TempoHub", err["error"])
        self.assertEqual(self.opened, [])
        self.assertEqual(upload_replay.UploadBudget(limits.state_path(limits.BUDGET_FILE, self.state.name)).used(), 0)

    def test_a_confirmed_send_runs_once_and_is_counted(self):
        body, steps = self.prepare_send()
        status, result = self.request("POST", "/api/send", body)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["written"], result["of"], len(self.opened), self.opened[0].closed), (25, 25, 1, True))
        self.assertEqual(upload_replay.UploadBudget(limits.state_path(limits.BUDGET_FILE, self.state.name)).used(), 1)
        status, again = self.request("POST", "/api/send", body)              # the same bitmap again: the limits refuse it
        self.assertEqual(status, 400)
        self.assertIn("same as the last", again["error"])
        self.assertEqual(len(self.opened), 1)

    def test_a_wrong_reply_stops_the_upload(self):
        body, steps = self.prepare_send()
        self.script[1] = [bytes(64)]                                            # the "ready" reply is wrong
        status, result = self.request("POST", "/api/send", body)
        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])
        self.assertLess(result["written"], 25)

    def test_the_budget_limit_applies(self):
        body, _ = self.prepare_send()
        with open(limits.state_path(limits.LIMITS_FILE, self.state.name), "w", encoding="utf-8") as fh:
            json.dump({"total": 0, "per_day": 10}, fh)
        status, err = self.request("POST", "/api/send", body)
        self.assertEqual(status, 400)
        self.assertIn("budget", err["error"])
        self.assertEqual(self.opened, [])

    # ---- time per picture
    def test_seconds_per_picture_sets_xx_xx_only_for_several_pictures(self):
        info = self.add()
        two = self.body([info["id"]] * 2)
        _, base = self.request("POST", "/api/plan", two)
        self.assertEqual((base["xx"], base["seconds_per_picture"]), (100, 0.2))
        for seconds, xx in ((0.2, 100), (0.4, 200), (0.35, 175), (0.8, 400)):
            status, plan = self.request("POST", "/api/plan", {**two, "seconds": seconds})
            self.assertEqual((status, plan["xx"]), (200, xx), seconds)
            self.assertTrue(plan["can_send"], plan)
            self.assertEqual(plan["sha"] == base["sha"], xx == 100)
        _, plan = self.request("POST", "/api/plan", {**two, "seconds": 0.4})
        _, bitmap, _, _ = self.ws.build({**two, "seconds": 0.4})
        self.assertEqual(plan["sha"], image_send.identity_sha256(bitmap, 200))
        self.assertIn("0x00c8", " ".join(c["name"] for c in plan["checks"]))
        one = self.body([info["id"]])
        _, plan = self.request("POST", "/api/plan", {**one, "seconds": 0.6})      # one picture does not move: the captured value stays
        self.assertEqual(plan["xx"], 100)
        for bad in (0.19, 0.81, 0, 1.5, "0.4", True):
            with self.subTest(bad=bad):
                self.assertEqual(self.request("POST", "/api/plan", {**two, "seconds": bad})[0], 400)

    def test_a_send_with_a_speed_writes_that_xx_xx_and_needs_the_matching_hash(self):
        info = self.add()
        body = {**self.body([info["id"]] * 2), "seconds": 0.4}
        _, bitmap, steps, _ = self.ws.build(body)
        self.script = {i: [s[2]] for i, s in enumerate(steps) if s[2] is not None}
        body["confirm"] = True
        body["expect_sha"] = image_send.bitmap_sha256(bitmap)[:16]            # the hash of the bitmap alone is not this upload's hash
        status, err = self.request("POST", "/api/send", body)
        self.assertEqual(status, 400, err)
        self.assertEqual(self.opened, [])
        body["expect_sha"] = image_send.identity_sha256(bitmap, 200)[:16]
        status, result = self.request("POST", "/api/send", body)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"], result)
        stream = b"".join(f[10:6 + int.from_bytes(f[4:6], "big")] for f in self.opened[0].writes[2:-2])
        self.assertIn(bytes([2, 0, 0, 0x00, 0xC8]), stream)
        self.assertNotIn(bytes([2, 0, 0, 0x00, 0x64]), stream)

    # ---- colour
    def test_colour_needs_confirmation_and_the_expected_reply(self):
        self.assertEqual(self.request("POST", "/api/color", {"rgb": "336699"})[0], 400)
        self.assertEqual(self.request("POST", "/api/color", {"rgb": "zzz", "confirm": True})[0], 400)
        self.running = True
        self.assertEqual(self.request("POST", "/api/color", {"rgb": "336699", "confirm": True})[0], 400)
        self.assertEqual(self.frames_sent, [])
        self.running = False
        self.reply = frames.expected_color_reply(0x33, 0x66, 0x99)
        status, ok = self.request("POST", "/api/color", {"rgb": "336699", "confirm": True})
        self.assertEqual((status, ok["ok"]), (200, True))
        self.assertEqual(self.frames_sent, [frames.color_frame(0x33, 0x66, 0x99)])
        self.reply = bytes(64)
        status, err = self.request("POST", "/api/color", {"rgb": "336699", "confirm": True})
        self.assertEqual(status, 400)
        self.assertIn("UNEXPECTED", err["error"])
        self.reply = None
        self.assertEqual(self.request("POST", "/api/color", {"rgb": "336699", "confirm": True})[0], 400)


try:
    import av
except ImportError:        # videos are optional
    av = None


def make_video(path, seconds=4, fps=10, size=(64, 48)):
    """A small mpeg4 video whose brightness grows with time: frame i has grey level 10 + 5 * i."""
    with av.open(path, "w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = size[0], size[1], "yuv420p"
        for i in range(seconds * fps):
            grey = min(255, 10 + 5 * i)
            img = Image.new("RGB", size, (grey, grey, grey))
            for packet in stream.encode(av.VideoFrame.from_image(img)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@unittest.skipIf(av is None, "PyAV is not installed")
class VideoTests(unittest.TestCase):
    open_device, send_frame, request, add, body = (ServerTests.open_device, ServerTests.send_frame, ServerTests.request,
                                                   ServerTests.add, ServerTests.body)

    def setUp(self):
        ServerTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = f"{self.tmp.name}/clip.mp4"
        make_video(self.path)
        with open(self.path, "rb") as fh:
            self.data = fh.read()

    def open_video(self):
        status, info = self.request("POST", "/api/video?name=clip", raw=self.data)
        self.assertEqual(status, 200, info)
        return info

    def grey_of(self, url):
        _, b64 = url.split(",", 1)
        import base64
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("L")
        return img.getpixel((img.width // 2, img.height // 2))

    def test_probe_and_extract_follow_start_fps_and_count(self):
        info = self.open_video()
        self.assertEqual((info["width"], info["height"], info["name"]), (64, 48, "clip"))
        self.assertAlmostEqual(info["duration"], 4.0, delta=.3)
        status, res = self.request("POST", "/api/video/frames", {"start": 0.5, "fps": 2, "count": 4})
        self.assertEqual(status, 200, res)
        times = [f["time"] for f in res["frames"]]
        self.assertEqual(len(times), 4)
        for k, t in enumerate(times):
            self.assertAlmostEqual(t, 0.5 + k / 2, delta=.15)
        greys = [self.grey_of(f["url"]) for f in res["frames"]]
        self.assertEqual(greys, sorted(greys))
        self.assertLess(greys[0], greys[-1])
        self.assertEqual(len(self.ws.pictures), 4)                          # the pictures are stored for cropping and planning
        status, plan = self.request("POST", "/api/plan", self.body([f["id"] for f in res["frames"]]))
        self.assertEqual((status, plan["pictures"], plan["frames"]), (200, 4, 4 * 8 * 133 // 50 + 3 + (1 if 4 * 8 * 133 % 50 else 0)))
        self.assertEqual(self.opened, [])

    def test_a_short_video_returns_fewer_pictures(self):
        self.open_video()
        status, res = self.request("POST", "/api/video/frames", {"start": 3.0, "fps": 2, "count": 20})
        self.assertEqual(status, 200, res)
        self.assertTrue(1 <= len(res["frames"]) < 20)
        self.assertEqual(res["wanted"], 20)

    def test_bad_requests_are_refused(self):
        self.assertEqual(self.request("POST", "/api/video/frames", {"start": 0, "fps": 1, "count": 2})[0], 400)    # no video yet
        self.open_video()
        for bad in ({"start": -1, "fps": 1, "count": 2}, {"start": 0, "fps": 0, "count": 2}, {"start": 0, "fps": 100, "count": 2},
                    {"start": 0, "fps": 1, "count": 0}, {"start": 0, "fps": 1, "count": 21}, {"start": 0, "fps": 1, "count": 1.5},
                    {"start": 99, "fps": 1, "count": 2}, {"start": "0", "fps": 1, "count": 2}):
            with self.subTest(bad=bad):
                self.assertEqual(self.request("POST", "/api/video/frames", bad)[0], 400)
        status, err = self.request("POST", "/api/video?name=x", raw=b"this is not a video")
        self.assertEqual(status, 400)
        self.assertIn("cannot read the video", err["error"])

    def test_the_temporary_copy_is_deleted_when_replaced_and_on_close(self):
        import os
        self.open_video()
        first = self.ws.video["dir"]
        self.assertTrue(os.path.isdir(first))
        self.open_video()
        self.assertFalse(os.path.isdir(first))
        second = self.ws.video["dir"]
        self.ws.close()
        self.assertFalse(os.path.isdir(second))
        self.assertIsNone(self.ws.video)


if __name__ == "__main__":
    unittest.main()
