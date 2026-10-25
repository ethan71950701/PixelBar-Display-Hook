"""Start the PixelBar picture window (used by PixelBar-GUI.bat). The embeddable Python ignores the
current folder, so this file puts its own folder on the module path first."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pixelbar import gui  # noqa: E402

if __name__ == "__main__":
    gui.serve()
