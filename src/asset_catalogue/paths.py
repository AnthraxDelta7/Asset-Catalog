from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    """The folder holding asset_catalogue's own data files (the Blender
    helper scripts, currently) -- Path(__file__).parent when running from
    source, since that's a real folder on disk then. Under PyInstaller,
    __file__ no longer has a real parent folder (this module's code is
    compiled into an internal archive, not left as a loose file), so we use
    sys._MEIPASS instead -- the folder PyInstaller actually extracts/copies
    bundled data files into, set only when frozen.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "asset_catalogue"
    return Path(__file__).parent