# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 - SSH Key Manager (单文件模式)
打包命令: pyinstaller main.spec
输出: dist/ssh-key-manager-<platform> (单个文件)
"""

import sys
from pathlib import Path

block_cipher = None

# 平台对应的输出文件名
if sys.platform == 'win32':
    exe_name = 'ssh-key-manager-windows'
elif sys.platform == 'darwin':
    exe_name = 'ssh-key-manager-macos'
else:
    exe_name = 'ssh-key-manager-linux'

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('modules', 'modules'),
        ('VERSION', '.'),
    ],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'pystray',
        'pystray._win32',
        'pystray._unix',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageTk',
        'PIL.ImageFont',
        'PIL.ImageColor',
        'paramiko',
        'waitress',
        'flask',
        'jinja2',
        'cryptography',
        'nacl',
        'bcrypt',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'transformers', 'numpy', 'pandas', 'matplotlib',
        'seaborn', 'selenium', 'playwright', 'openai', 'pytest',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 无控制台黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
