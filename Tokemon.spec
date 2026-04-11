# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller Tokemon.spec

import os
from pathlib import Path

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config.example.json', '.'),
    ],
    hiddenimports=[
        'browser_cookie3',
        'pycryptodomex',
        'lz4',
        'lz4.frame',
        'certifi',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Tokemon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Tokemon',
)

app = BUNDLE(
    coll,
    name='Tokemon.app',
    icon=None,
    bundle_identifier='com.rvt.tokemon',
    info_plist={
        'LSUIElement': True,           # hides from dock + app switcher
        'NSAppleEventsUsageDescription': 'Needed to read browser cookies for Claude.ai auth.',
        'NSKeychainUsageDescription': 'Reads your browser session to authenticate with Claude.ai.',
    },
)
