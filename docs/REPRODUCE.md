# From nothing to the same results

This is the path that was actually followed, shortened. Each step says what you should see. If something differs,
**stop** and compare with [SPEC.md](SPEC.md) before sending anything else. Tested on Windows 11, Python 3.12.
Read "Safety" in the [README](../README.md) first: sending writes to the speaker's storage.

## 0. What you need

- The speaker (the author's is the Edifier HALO PixelBar, 漫步者 花再, VID:PID `2d99:a106`), connected by **USB**, in sound-card input mode.
- Windows with Python 3.12. If `python` is not on your PATH, the embeddable Python from python.org works too; it ignores the
  current folder, so run the repository's scripts through a small wrapper that adds the repository folder to `sys.path`
  (`launch_gui.py` does this for the browser window).
- `pip install -r requirements.txt` (hidapi is needed to send; Pillow for pictures; PyAV for video; all optional except hidapi
  for sending).
- To redo the **captures** (optional, you do not need them to use the tools): Wireshark with the USBPcap option, the
  official app EDIFIER TempoHub.

## 1. Check the code without the speaker

    python -m unittest discover -s tests -t .

Expected: all tests pass (170 or more, a few are skipped if Pillow or PyAV are missing). No device is touched; the tests use fake
devices and generated pictures and videos.

## 2. Find the speaker (read-only)

    python tools/list_devices.py

Expected: rows with `VID:PID=2d99:a106`, usage pages `0xff13`, `0xff14` (both on interface 4) and `0xc` (interface 3). If your
speaker has other IDs, this repository's code will refuse to send; see "Will it work on my speaker?" in the README.

## 3. Dry runs (nothing is sent)

    python -m pixelbar text "Hello" --color ffffff
    python -m pixelbar image my_picture.png
    python -m pixelbar slideshow a.png b.png --xx 190

Expected: the frames or the plan are printed and compared with what the official app sent (`identical to a captured TempoHub frame`
where one exists) and `dry run: nothing was sent.` Pictures are shown as a 256 x 32 preview (`#` = lit) and written as a PNG
to a preview folder outside the repository (`C:\pixelbar-raw\preview` by default; the path is in `pixelbar/__main__.py`).

## 4. First real send: text

1. Close TempoHub completely, also in the system tray.
2. `python -m pixelbar text "Hello" --color ffffff --send --yes-i-closed-tempohub`
3. Expected: two replies are printed (colour, text), both marked as expected, and the screen shows `Hello` in white.

If a reply differs from the expected one the command stops and prints it. Do not retry in a loop; look at SPEC.md.

## 5. A picture

    python -m pixelbar image my_picture.png --send --expect-sha <first 12 hex digits of the hash the dry run printed> --yes-i-closed-tempohub

`--expect-sha` makes sure that what is sent is what you looked at. The upload takes about 2 s, 25 frames, 24 replies.
The picture replaces the official scene "兼职成年人"; click another scene and then that one in TempoHub to restore it.
The limits (`~/.pixelbar/limits.json`, default 5 uploads in total and 10 a day) will refuse more; raise them only
on purpose, by editing the file.

## 6. An animation and its speed

    python -m pixelbar animation --frames 2 --xx 190 --send --expect-sha <12 hex digits> --yes-i-closed-tempohub

A generated test animation (a block moving across the screen with the picture number). `--xx` is the time per picture:
`0x190` should give about 0.8 s per picture, `0x64` (the default) about 0.2 s. To measure it yourself, film the screen with a
phone in slow motion and run `python tools/screen_timing.py video.mp4 --slow 4 --crop x0,y0,x1,y1 --bright 150` (the numbers in
SPEC.md section 6 were measured with 120 fps shown at 0.25x).

## 7. The browser window

    PixelBar-GUI.bat            (or: python -m pixelbar gui)

It opens a page on 127.0.0.1 (never reachable from another computer) where you add pictures, move and zoom the crop frame,
choose dither/threshold/invert, see the exact 256 x 32 result, set the time per picture (0.2 - 0.8 s), pick frames out of a
video and upload with a click and a confirmation. The server checks TempoHub, the limits and the picture hash again before it sends.

## 8. A whole video, 20 pictures at a time (what produced the Bad Apple run)

1. `python -m pixelbar film frames "video.mp4" --no-invert` (1 picture per second, whole video, to `C:\pixelbar-raw\badapple\`;
   look at `preview\` before anything else).
2. `python -m pixelbar film plan` (segments, time, and how many uploads your limit allows) and `film stats` (how bright the pictures are).
3. `python -m pixelbar film run --rehearse` (the timeline on a virtual clock, sends nothing).
4. Start a phone recording (normal video mode, steady, screen large in the picture), show `Hello` (step 4), then
   `python -m pixelbar film run --send --this-run-only --yes-tempohub-closed --yes-phone-recording --yes-hello-shown --max-uploads 11`.
   It uploads segment after segment, waits 22 s after each (20 s of play and a 2 s buffer), writes a log with the time of every
   upload to `film_log.jsonl`, and stops at the first surprise. Add `--stop-file C:\path\STOP` and create that file to stop it by hand.
   `--first-segment N` continues a run that was stopped at segment N. This needs 11 uploads, so raise `total` in `limits.json` first.
5. `python tools/film_post.py recording.mp4 film_log.jsonl --anchor <seconds> --music "video.mp4" --mv-below "video.mp4" --write out.mp4`
   cuts the 20 seconds of each pass out of the recording and puts the original video under it with its music. The anchor (the second
   of the recording at which the first upload started) has to be read from the video (the text 「图片上传中」 appears at about that
   moment) and refined with the one-second rhythm of the passes; `docs/protocol.md`, *The whole Bad Apple filmed and edited*,
   shows how. Without `--anchor` the script only guesses and refuses to write.

## 9. Redoing the analysis from captures (optional)

Install Wireshark with USBPcap, start a capture on the interface that holds the speaker, and in TempoHub do **one action at a
time** (set text `A`, then `AB`, then a colour, then change the display mode, then upload a scene), saving one file per action.
Keep the files **outside the repository** (they also hold keyboard traffic). Then:

    python tools/analyze_capture.py capture.pcap          # frames per command, checksum check
    python tools/analyze_upload.py capture.pcap --sheet-dir out    # rebuilds the pictures of an upload
    python tools/analyze_replies.py folder_with_captures           # every reply of the speaker, per command

To check your reading of a capture, rebuild the frames with `pixelbar/frames.py` or `pixelbar/upload.py` and compare: the tests in
`tests/` do exactly that with hex dumps of the captures in `tests/fixtures/`.

## 10. If something goes wrong

- A command stops with `STOP at step N ... raw reply ...`: nothing was retried. The speaker may show an unfinished upload
  (「图片上传中」 or 「图片传输失败，请重新上传」). Click any official scene in TempoHub, and look at SPEC.md section 7 for frames
  the speaker sends on its own.
- `hidapi missing`: `pip install hidapi`. `expected exactly one HID path with usage_page 0xff14`: a different device or the official app
  has the device open.
- The upload is refused: the limit file, the daily limit or "same as the last completed upload" (see the message).
