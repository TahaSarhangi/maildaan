# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all

datas = [('ui', 'ui'), ('assets', 'assets')]
# The Microsoft WebView2 Fixed Version Runtime (x64), extracted by
# build-installer.ps1 into webview2_runtime\. Optional so that plain
# development builds still work when the runtime has not been downloaded.
runtime_root = os.path.join(SPECPATH, 'webview2_runtime')
if os.path.isdir(runtime_root):
    datas.append(('webview2_runtime', 'webview2_runtime'))
binaries = []
hiddenimports = []
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += ['imapclient', 'openpyxl', 'argon2', 'rapidfuzz']


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='MailDaan',
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
    icon=['assets/maildaan.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MailDaan',
)
