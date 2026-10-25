# PixelBar-Display-Hook

Goal: send custom text and images to the pixel screen of the Edifier HALO
PixelBar speaker (basic and enhanced editions) from Windows and Android.

No public protocol exists, so the first step is capturing what the official
apps send. Status: protocol unknown. Findings go in `docs/protocol.md`.

## Step 1: identify the device (Windows)

    pip install hidapi pyserial
    python tools/list_devices.py

Run it with the speaker connected by USB, in sound-card input mode, and note
the VID:PID and whether it shows up as HID or a serial port.

## Step 2: capture USB traffic (Windows, EDIFIER TempoHub)

1. Install Wireshark with the USBPcap option, and EDIFIER TempoHub.
2. Start a capture on the USBPcap interface that holds the speaker.
3. In TempoHub, do one action at a time and note the time of each:
   set text "A", then "AB", then a different colour, then upload an image.
4. Save as `captures/usb-<action>.pcapng`.

## Step 3: capture Bluetooth traffic (Android, phone app)

1. Developer options: enable "Bluetooth HCI snoop log".
2. Connect the speaker in the app and repeat the same actions as above.
3. Pull the log (`adb bugreport` or the btsnoop_hci.log path) into `captures/`.
4. Open in Wireshark and filter on ATT / GATT writes.

Record the same minimal actions in each capture so diffs are easy to read.
Only analyse hardware you own, and check the app's licence terms.
