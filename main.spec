# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 - SSH Key Manager (单文件模式)
打包命令: pyinstaller main.spec
输出: dist/ssh-key-manager-<platform> (单个文件)
"""

import sys
import os
from pathlib import Path

block_cipher = None

# 平台对应的输出文件名和图标
if sys.platform == 'win32':
    exe_name = 'ssh-key-manager-windows'
    app_icon = 'asset/icon.ico'
elif sys.platform == 'darwin':
    exe_name = 'ssh-key-manager-macos'
    app_icon = 'asset/icon.icns'
else:
    exe_name = 'ssh-key-manager-linux'
    app_icon = None  # Linux bootloader 不支持图标

# 图标文件不存在时降级为 None（避免打包报错）
if app_icon is not None and not os.path.exists(app_icon):
    app_icon = None

# customtkinter 主题/图片资源文件必须显式打包
import customtkinter as _ctk
_ctk_dir = os.path.dirname(_ctk.__file__)

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('modules', 'modules'),
        ('VERSION', '.'),
        ('asset/icon.ico', 'asset'),
        ('asset/icon.icns', 'asset'),
        (_ctk_dir, 'customtkinter'),  # CTk 主题/图片资源
    ],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'customtkinter',
        'pystray',
        'pystray._win32',
        'pystray._unix',
        'pystray._darwin',      # macOS 系统托盘后端
        'AppKit',               # pyobjc: macOS AppKit 框架
        'Foundation',           # pyobjc: macOS Foundation 框架
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
        'modules.routes',
        'modules.routes.keys',
        'modules.routes.ssh_config',
        'modules.routes.connections',
        'modules.routes.platform',
        'modules.webssh',
        'modules.webssh_routes',
        'modules.webssh_sessions',
        'modules.webssh_sftp',
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
    icon=app_icon,
)
