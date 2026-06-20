# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 - SSH Key Manager
打包命令: pyinstaller main.spec
输出目录: dist/SSHKeyManager/
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[
        # 打包模板和静态文件
        ('templates', 'templates'),
        ('static', 'static'),
        ('modules', 'modules'),
    ],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'pystray',
        'pystray._win32',
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
    [],
    exclude_binaries=True,
    name='SSHKeyManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 无控制台窗口
    icon=None,  # 可后续添加 .ico 图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SSHKeyManager',
)
