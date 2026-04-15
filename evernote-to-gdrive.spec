# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for evernote-to-gdrive.
# Build: pyinstaller evernote-to-gdrive.spec --clean --noconfirm

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

a = None
hiddenimports = []
hiddenimports += collect_submodules("googleapiclient")
hiddenimports += collect_submodules("google.auth")
hiddenimports += collect_submodules("google_auth_oauthlib")
hiddenimports += [
    "googleapiclient.discovery_cache.file_cache",
    "google.auth.transport.requests",
]

client_secrets = Path(".auth/client_secrets.json")
datas = []
if client_secrets.exists():
    datas.append((str(client_secrets), "evernote_to_gdrive/_bundled_auth"))

a = Analysis(
    ["scripts/pyinstaller_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="evernote-to-gdrive",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
