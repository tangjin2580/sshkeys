"""
共享配置 — 供 main.py（GUI）和 webssh_routes.py（服务器）共同读写。
所有修改实时生效，无需重启。
"""

import json
from pathlib import Path

# 配置文件路径：~/.ssh/sshkeys-config.json
_CONFIG_DIR = Path.home() / ".ssh"
_CONFIG_FILE = _CONFIG_DIR / "sshkeys-config.json"

# 默认值
_DEFAULTS = {
    "sftp_max_download_mb": 100,   # SFTP 单文件下载上限（MB）
    "server_host": "127.0.0.1",
    "server_port": 5201,
}

# 运行时缓存（避免每次都读文件）
_cache = dict(_DEFAULTS)


def _ensure_config():
    """确保配置文件存在，返回已加载的配置 dict。"""
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            # 合并默认值（防止旧配置缺少新字段）
            for k, v in _DEFAULTS.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            pass
    # 文件不存在或读取失败 → 写入默认值
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(_DEFAULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    return dict(_DEFAULTS)


def load_config() -> dict:
    """从文件加载配置到缓存，并返回。"""
    global _cache
    _cache = _ensure_config()
    return _cache


def get(key: str, default=None):
    """读取配置值（优先缓存，缺失时从文件刷新）。"""
    if key not in _cache:
        load_config()
    return _cache.get(key, default)


def set(key: str, value):
    """写入配置值（同时更新缓存 + 文件）。"""
    global _cache
    _cache[key] = value
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")


def get_sftp_max_download_bytes() -> int:
    """返回 SFTP 下载上限（字节）。"""
    mb = get("sftp_max_download_mb", 100)
    return mb * 1024 * 1024
