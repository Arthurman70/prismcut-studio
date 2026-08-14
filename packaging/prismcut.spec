# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec - builds a windowed PrismCut.exe (folder build for fast start).
# Run from the repo root:  pyinstaller packaging/prismcut.spec
import os

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

a = Analysis(
    ["../prismcut/__main__.py"],
    pathex=[".."],
    binaries=[],
    # pyspellchecker bundles its dictionaries as package data
    # (spellchecker/resources/*.json.gz) with no PyInstaller built-in hook
    # to pick them up automatically, unlike most of this app's other
    # dependencies - collect_data_files() is PyInstaller's own standard
    # helper for exactly this case.
    datas=[("../prismcut/assets", "prismcut/assets")] + collect_data_files("spellchecker"),
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

icon_path = os.path.join(SPECPATH, "icon.ico")
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
