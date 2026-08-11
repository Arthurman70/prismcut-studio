# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec - builds a windowed PrismCut.exe (folder build for fast start).
# Run from the repo root:  pyinstaller packaging/prismcut.spec
import os

block_cipher = None

a = Analysis(
    ["../prismcut/__main__.py"],
    pathex=[".."],
    binaries=[],
    datas=[("../prismcut/assets", "prismcut/assets")],
    hiddenimports=[
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon_path = os.path.join(os.path.dirname("__file__"), "packaging", "icon.ico")
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PrismCut",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_path if os.path.exists(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="PrismCut",
)
