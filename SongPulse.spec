# -*- mode: python ; coding: utf-8 -*-
# Full PyInstaller spec including optional AI Voice & SFX Removal dependencies
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

datas = [('songpulse/assets', 'songpulse/assets')]
binaries = []
hiddenimports = [
    'torchvision._C',
    'PyQt6.QtMultimedia',
    '_cffi_backend',
    'soundcard',
    'aiohttp_retry',
]
datas += copy_metadata('audio-separator')
hiddenimports += collect_submodules('songpulse')

for pkg in ('audio_separator', 'librosa', 'torchvision', 'shazamio', 'shazamio_core', 'onnxruntime', 'faster_whisper'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

a = Analysis(
    ['song_id_app.pyw'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'onnxruntime.quantization',
        'matplotlib',
        'tkinter',
        'pytest',
        'IPython',
        'jupyter',
        'notebook',
        'torch.testing',
        'torch.distributed',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SongPulse',
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
    icon=['songpulse.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SongPulse',
)
