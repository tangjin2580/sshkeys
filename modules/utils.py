"""
跨平台工具函数
"""

import os
import sys
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
