from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    """The folder holding asset_catalogue's own data files (the Blender
    helper scripts, app_icon.png/.ico) -- Path(__file__).parent when
    running from source, since that's a real folder on disk then. Under
    PyInstaller, __file__ no longer has a real parent folder (this
    module's code is compiled into an internal archive, not left as a
    loose file), so we use sys._MEIPASS instead -- the folder PyInstaller
    actually extracts/copies bundled data files into, set only when
    frozen.

    Important: this always resolves to src/asset_catalogue/ itself, never
    a subpackage. A file referenced via package_dir() must physically
    live directly there (not e.g. under ui/) or dev-mode resolution
    breaks silently -- a missing-file QIcon/QPixmap just renders as
    blank, no error -- even though bundling it into the right spot in
    AssetCatalogue.spec's `datas` can still make the frozen build work
    fine. (This bit app_icon.png for exactly this reason: it lived under
    ui/ while every load path used package_dir(), so the packaged .exe's
    icon worked but the window/splash icon was silently blank when
    running from source.)
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "asset_catalogue"
    return Path(__file__).parent