# -*- mode: python ; coding: utf-8 -*-

# Windows Defender/SmartScreen (and various third-party AV heuristics) tend
# to flag an unsigned PyInstaller build outright -- the same reputation
# problem Godot's own unsigned builds are known for. Nothing here fully
# solves that (only a paid code-signing certificate genuinely does), but two
# real, zero-cost mitigations: UPX packing is one of the single most common
# heuristic triggers for "looks like a malware packer" (disabled below, in
# both EXE and COLLECT), and an exe with zero version resource info reads as
# another minor suspicion signal to some engines (added below, kept in sync
# with version.py automatically rather than hand-maintained separately).
import sys as _sys

_sys.path.insert(0, "src")
from asset_catalogue.version import __version__ as _app_version

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

_version_tuple = tuple(int(p) for p in _app_version.split(".")) + (0, 0, 0, 0)
_version_tuple = _version_tuple[:4]

_version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_version_tuple,
        prodvers=_version_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Asset Catalogue"),
                        StringStruct("FileDescription", "Asset Catalogue - Game Asset Cataloguing Tool"),
                        StringStruct("FileVersion", _app_version),
                        StringStruct("InternalName", "AssetCatalogue"),
                        StringStruct("LegalCopyright", "GPL-3.0-or-later"),
                        StringStruct("OriginalFilename", "AssetCatalogue.exe"),
                        StringStruct("ProductName", "Asset Catalogue"),
                        StringStruct("ProductVersion", _app_version),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)


a = Analysis(
    ['src/asset_catalogue/ui/main_window.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/asset_catalogue/blender_thumbnail_script.py', 'asset_catalogue'),
        ('src/asset_catalogue/blender_animation_script.py', 'asset_catalogue'),
        ('src/asset_catalogue/blender_convert_script.py', 'asset_catalogue'),
        ('src/asset_catalogue/blender_common.py', 'asset_catalogue'),
        ('src/asset_catalogue/texture_matching.py', 'asset_catalogue'),
        ('src/asset_catalogue/godot_export_script.gd', 'asset_catalogue'),
        ('src/asset_catalogue/godot_meshinstance_wrapper_script.gd', 'asset_catalogue'),
        ('src/asset_catalogue/app_icon.png', 'asset_catalogue'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AssetCatalogue',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='src/asset_catalogue/app_icon.ico',
    version=_version_info,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AssetCatalogue',
)
