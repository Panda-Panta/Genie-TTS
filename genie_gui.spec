# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

block_cipher = None

# Collect all data files
datas = []
datas += collect_data_files('genie_tts')
datas += collect_data_files('pyopenjtalk')
datas += collect_data_files('eunjeon')
datas += collect_data_files('jieba_fast')
datas += collect_data_files('g2pM')
datas += collect_data_files('pypinyin')
datas += collect_data_files('sounddevice')
datas += collect_data_files('uvicorn')
datas += collect_data_files('fastapi')

# Collect dynamic libraries
binaries = []
binaries += collect_dynamic_libs('onnxruntime')
binaries += collect_dynamic_libs('pyopenjtalk')
binaries += collect_dynamic_libs('sounddevice')

uv_dlls = os.path.join(os.environ.get('USERPROFILE', ''), r'AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\DLLs')
if os.path.exists(uv_dlls):
    for dll in ['libssl-3-x64.dll', 'libcrypto-3-x64.dll', '_ssl.pyd']:
        p = os.path.join(uv_dlls, dll)
        if os.path.exists(p):
            binaries.append((p, '.'))

# Collect hidden imports
hiddenimports = [
    'genie_tts',
    'genie_tts.GUI.GUI',
    'genie_tts.GUI.ApiServerWidget',
    'genie_tts.Server',
    'genie_tts.Core.Inference',
    'genie_tts.Core.Resources',
    'genie_tts.Core.TTSPlayer',
    'genie_tts.G2P.Chinese.ChineseG2P',
    'genie_tts.G2P.Japanese.JapaneseG2P',
    'genie_tts.G2P.English.EnglishG2P',
    'genie_tts.G2P.Korean.KoreanG2P',
    'onnxruntime',
    'onnxruntime.capi',
    'onnxruntime.capi.onnxruntime_pybind11_state',
    'pyopenjtalk',
    'pypinyin',
    'g2pM',
    'jieba_fast',
    'eunjeon',
    'jamo',
    'ko_pron',
    'g2pk2',
    'soundfile',
    'sounddevice',
    'soxr',
    'tokenizers',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'pkg_resources',
    'uvicorn',
    'fastapi',
    'starlette',
]

hiddenimports += collect_submodules('PySide6')
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('starlette')
hiddenimports += collect_submodules('pydantic')
hiddenimports += collect_submodules('pydantic_core')
hiddenimports += collect_submodules('pyopenjtalk')
hiddenimports += collect_submodules('jieba_fast')
hiddenimports += collect_submodules('g2pM')
hiddenimports += collect_submodules('pypinyin')
hiddenimports += collect_submodules('eunjeon')
hiddenimports += collect_submodules('g2pk2')
hiddenimports += collect_submodules('genie_tts')

a = Analysis(
    ['Main.py'],
    pathex=['src', '.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'torchaudio', 'matplotlib', 'scipy', 'IPython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='genie-gui',
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='genie-gui',
)
