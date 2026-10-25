# PixelBar protocol notes

Fill in from captures. Questions to answer:

- Transport: USB HID, USB serial, BLE GATT, or classic Bluetooth?
- Screen resolution, colour depth
- Text packet: encoding, font, colour, scroll fields
- Image packet: chunking, pixel format, checksum
- Differences between basic and enhanced editions

## Confirmed findings

- USB VID:PID = 0x2D99:0xA106 (Windows Device Manager hardware ID, MI_00 = audio interface). Confirmed.
- USB composite device on root hub USBPcap1, port 10. Interfaces seen in USBPcapCMD: microphone,
  speaker, one HID consumer control, two vendor-defined HID. Confirmed.
- Guess: screen control uses one of the two vendor-defined HID interfaces. Not yet verified by capture.
- Raw captures taken on USBPcap1 include keyboard/mouse traffic: filter to the speaker's device
  address before saving anything into captures/.
