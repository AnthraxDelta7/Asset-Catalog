# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src/asset_catalogue/ui/main_window.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/asset_catalogue/blender_thumbnail_script.py', 'asset_catalogue'),
        ('src/asset_catalogue/blender_convert_script.py', 'asset_catalogue'),
        ('src/asset_catalogue/blender_common.py', 'asset_catalogue'),
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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='src/asset_catalogue/app_icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AssetCatalogue',
)
