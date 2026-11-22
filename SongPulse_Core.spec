# -*- mode: python ; coding: utf-8 -*-
# Slim Core PyInstaller spec (excludes heavy optional AI packages to keep installer tiny)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [('songpulse/assets', 'songpulse/assets')]
binaries = []
hiddenimports = [
    'PyQt6.QtMultimedia',
    '_cffi_backend',
    'soundcard',
    'aiohttp_retry',
]
hiddenimports += collect_submodules('songpulse')

for pkg in ('shazamio', 'shazamio_core'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

excludes = [
    'audio_separator',
    'faster_whisper',
    'ctranslate2',
    'huggingface_hub',
    'tokenizers',
    'onnxruntime',
    'onnx',
    'torch',
    'torchvision',
    'torchaudio',
    'librosa',
    'scipy',
    'sklearn',
    'llvmlite',
    'numba',
    'Cython',
    'matplotlib',
    'tkinter',
    'pytest',
    'IPython',
    'jupyter',
    'notebook',
    'PIL.ImageTk',
]

a = Analysis(
    ['song_id_app.pyw'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name='SongPulse_Core',
)
