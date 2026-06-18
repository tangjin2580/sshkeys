"""
跨平台工具函数
"""

import os
import sys
import stat
import logging

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"


def safe_chmod(path: str, mode: int) -> None:
    """
    跨平台安全设置文件权限。
    - Unix/macOS: 设置 POSIX 权限位
    - Windows: 静默跳过（Windows 使用 ACL，不支持 POSIX 权限位）
    """
    if IS_WINDOWS:
        return
    try:
        os.chmod(path, mode)
    except OSError as e:
        logger.debug(f"chmod 失败（可能无影响）: {path} — {e}")


def set_key_permissions(private_path: str, public_path: str) -> None:
    """
    设置 SSH 密钥文件的标准权限：
    - 私钥: 600 (仅拥有者可读写)
    - 公钥: 644 (拥有者可读写，其他只读)

    Windows 上静默跳过。
    """
    safe_chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)
    safe_chmod(public_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def get_ssh_dir() -> str:
    """
    获取用户 .ssh 目录路径（跨平台）。
    - Windows: C:\\Users\\<用户名>\\.ssh
    - macOS:   /Users/<用户名>/.ssh
    - Linux:   /home/<用户名>/.ssh
    """
    return os.path.join(os.path.expanduser("~"), ".ssh")
