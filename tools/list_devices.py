"""List USB HID devices and serial ports so the PixelBar can be identified.

Usage: python tools/list_devices.py
Requires: pip install hidapi pyserial
"""
import sys


def list_hid():
    try:
        import hid
    except ImportError:
        print("[hid] missing: pip install hidapi")
        return
    print("== HID devices ==")
    for d in hid.enumerate():
        print(
            f"VID:PID={d['vendor_id']:04x}:{d['product_id']:04x} "
            f"mfr={d['manufacturer_string']!r} product={d['product_string']!r} "
            f"usage_page=0x{d['usage_page']:x} usage=0x{d['usage']:x} "
            f"iface={d['interface_number']}"
        )


def list_serial():
    try:
        from serial.tools import list_ports
    except ImportError:
        print("[serial] missing: pip install pyserial")
        return
    print("== Serial ports ==")
    for p in list_ports.comports():
        print(f"{p.device} VID:PID={p.vid}:{p.pid} desc={p.description!r}")


if __name__ == "__main__":
    list_hid()
    list_serial()
    sys.exit(0)
