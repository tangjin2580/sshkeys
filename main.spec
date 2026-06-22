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
    ],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageQt',
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
    argv_emulation=True,  # macOS 双击打开时支持拖入文件
    target_arch=None,
    codesign_identity=None,
    entitlements_file='entitlements.plist' if sys.platform == 'darwin' else None,
    icon=app_icon,
)

# macOS: 打包成双击即可运行的 .app Bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='SSH Key Manager.app',
        icon=app_icon,
        bundle_identifier='com.tangjin.sshkeymanager',
        info_plist={
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': '1.0',
            'CFBundleName': 'SSH Key Manager',
            'NSHumanReadableCopyright': '© tangjin2580',
        },
    )
