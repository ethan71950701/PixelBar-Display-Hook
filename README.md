# PixelBar-Display-Hook

Send your own text, pictures and animations to the pixel screen (256 x 32, one bit per pixel) of the
**Edifier HALO PixelBar** speaker (漫步者 花再 Halo PixelBar) from a Windows computer over USB, without the
official app. The protocol was worked out from USB captures of the official app (EDIFIER TempoHub) and
checked on a real speaker, step by step.

> **Not affiliated with Edifier.** This is independent interoperability research on hardware its author
> owns. It contains no Edifier software, firmware or captures. **Use it at your own risk**: it writes to the
> speaker's storage, and how often that can be done safely is **not known** (see "Safety"). MIT licence.

## What works (confirmed on one speaker)

| Feature | Command |
|---|---|
| Text and its colour, scroll or static | `python -m pixelbar text "Hello" --color ffffff` |
| Your own pictures (converted to 256 x 32, black and white) | `python -m pixelbar image picture.png` |
| Several pictures in one upload (an animation, up to 20 tried), speed per picture 0.2-0.8 s | `python -m pixelbar slideshow a.png b.png ... --xx 190` |
| A browser window to add pictures or video frames, crop them and upload with a click | `PixelBar-GUI.bat` or `python -m pixelbar gui` |
| A long video, 20 pictures at a time, filmed from the speaker's screen | `python -m pixelbar film ...` (see docs/REPRODUCE.md) |

Every command that can send is a **dry run by default** (it prints what it would send and checks it against
what the official app sent). Sending needs `--send` and your confirmation that the official app is closed.

## Will it work on my speaker?

**Probably only on the same model, and that is untested for other units.**

- The code talks only to a USB device with **VID:PID `2d99:a106`** and the vendor HID collection `0xFF14`.
  It refuses to open anything else.
- Everything was tested on **one** unit (the owner's; described as the "enhanced" edition, not checked
  against the box). The firmware version was not recorded.
- The basic edition, other firmware versions and other Edifier models may use a different screen size or
  protocol. **Nobody has tested them.** If you try, start with `tools/list_devices.py` and read-only analysis
  of your own captures, and please report what you find.

## Quick start (Windows 11, Python 3.12)

    pip install -r requirements.txt
    python tools/list_devices.py          # read-only: finds the speaker (2d99:a106)
    python -m unittest discover -s tests -t .      # 170+ tests, no speaker needed
    python -m pixelbar text "Hello" --color ffffff  # dry run: shows the frames
    python -m pixelbar text "Hello" --color ffffff --send --yes-i-closed-tempohub

The speaker must be connected by USB in sound-card input mode, and the official app (TempoHub) must be
**fully closed** (also in the system tray) while you send. The step-by-step guide, including how to repeat
the captures yourself, is in [docs/REPRODUCE.md](docs/REPRODUCE.md).

## Documents

- [docs/SPEC.md](docs/SPEC.md): the protocol as far as it is **confirmed**, with the guesses and unknowns
  listed separately. Start here.
- [docs/REPRODUCE.md](docs/REPRODUCE.md): how to get from nothing to the same results.
- [docs/protocol.md](docs/protocol.md): the full lab notebook (2,500 lines, in time order). Early parts
  contain guesses that later parts corrected; it is the evidence behind SPEC.md, not the place to learn from.

## Safety

- **Flash wear is unknown.** Uploaded text and pictures survive a power-off, so they are stored; how many
  times they can be rewritten is not known. The code therefore limits itself: a total number of uploads and a
  number per day (`~/.pixelbar/limits.json`; the command line cannot raise them), and it refuses to upload
  the same thing twice in a row. Please keep these limits.
- Uploads are one-shot with bounded waits: the first unexpected reply or a timeout stops everything, with no
  retry. An unfinished upload may leave the speaker showing an error text ("picture transfer failed, please
  upload again"); clicking any official scene in TempoHub restores it.
- Unknown protocol fields are never changed, except the one that sets the time per picture (`XX XX`), and only
  within the range that was measured.
- Only analyse hardware you own, and check the licence terms of the official app before you capture its traffic.

## Layout

    pixelbar/      the library and command line (frames, uploads, pictures, the browser window, filming)
    tools/         read-only analysis of USB captures, timing measurement of a phone video, the cutting script
    tests/         unit tests (fake devices, generated pictures and videos; no real files needed)
    docs/          SPEC.md, REPRODUCE.md, protocol.md
    PixelBar-GUI.bat, launch_gui.py    open the browser window

Captures (`.pcap`), pictures and videos are **not** part of this repository and should not be added to it.
