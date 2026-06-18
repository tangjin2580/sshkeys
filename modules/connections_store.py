"""
连接配置持久化存储 — JSON 文件读写
存储路径: ~/.ssh/connections.json
"""

import json
import uuid
import logging
import threading
from datetime import datetime
from pathlib import Path

from modules.ssh_config import get_ssh_dir, safe_chmod

logger = logging.getLogger(__name__)

# 文件操作线程锁（防止并发读写 connections.json）
_file_lock = threading.Lock()


def _get_store_path() -> Path:
    """获取连接存储文件路径"""
    ssh_dir = get_ssh_dir()
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    return ssh_dir / "connections.json"


def load_all() -> list[dict]:
    """加载所有已保存的连接"""
    with _file_lock:
        return _load_all_unlocked()


def _load_all_unlocked() -> list[dict]:
    """内部：加载连接（调用方须已持有 _file_lock）"""
    path = _get_store_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        logger.warning("连接存储文件损坏，已重置")
    return []


def _save_all(connections: list[dict]):
    """保存全部连接到文件（调用方须已持有 _file_lock）"""
    path = _get_store_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(connections, f, indent=2, ensure_ascii=False)
    safe_chmod(str(path), 0o600)


def add_connection(alias: str, hostname: str, user: str,
                   identity_file: str = "", port: int = 22) -> dict:
    """
    添加或更新一条连接记录（按 alias 去重）

    Returns:
        {"success": bool, "message": str, "connection": dict}
    """
    with _file_lock:
        connections = _load_all_unlocked()

        # 按 alias 去重，存在则更新
        conn = None
        for c in connections:
            if c.get("alias") == alias:
                c["hostname"] = hostname
                c["user"] = user
                c["port"] = port
                c["identity_file"] = identity_file
                c["updated_at"] = datetime.now().isoformat()
                conn = c
                break

        if conn is None:
            conn = {
                "id": uuid.uuid4().hex[:8],
                "alias": alias,
                "hostname": hostname,
                "user": user,
                "port": port,
                "identity_file": identity_file,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            connections.append(conn)

        _save_all(connections)
    logger.info(f"连接已保存: {alias} → {user}@{hostname}:{port}")
    return {"success": True, "message": f"连接 {alias} 已保存", "connection": conn}


def delete_connection(conn_id: str) -> dict:
    """
    删除指定连接

    Returns:
        {"success": bool, "message": str}
    """
    with _file_lock:
        connections = _load_all_unlocked()
        initial_len = len(connections)
        connections = [c for c in connections if c.get("id") != conn_id]

        if len(connections) == initial_len:
            return {"success": False, "message": "连接不存在"}

        _save_all(connections)
    logger.info(f"连接已删除: {conn_id}")
    return {"success": True, "message": "连接已删除"}


def batch_sync_from_config(config_entries: list[dict]) -> list[dict]:
    """
    从 SSH config 条目批量同步到 connections.json（一次读 + 一次写）

    相比逐条调用 add_connection()，将 N 次读 + N 次写 缩减为 1 次读 + 1 次写。

    Args:
        config_entries: parse_ssh_config() 的返回结果

    Returns:
        同步后的完整连接列表
    """
    with _file_lock:
        connections = _load_all_unlocked()
        now = datetime.now().isoformat()

        # 构建 alias → index 快速索引
        alias_map = {c.get("alias"): i for i, c in enumerate(connections)}
        changed = False

        for entry in config_entries:
            alias = entry.get("host", "").split()[0]
            if not alias or alias == "*":
                continue

            hostname = entry.get("hostname", "")
            user = entry.get("user", "")
            identity_file = entry.get("identityfile", "")
            port = entry.get("port", 22)

            if alias in alias_map:
                # 更新已有条目
                c = connections[alias_map[alias]]
                if (c.get("hostname") != hostname or c.get("user") != user
                        or c.get("port") != port
                        or c.get("identity_file") != identity_file):
                    c["hostname"] = hostname
                    c["user"] = user
                    c["port"] = port
                    c["identity_file"] = identity_file
                    c["updated_at"] = now
                    changed = True
            else:
                # 新增条目
                conn = {
                    "id": uuid.uuid4().hex[:8],
                    "alias": alias,
                    "hostname": hostname,
                    "user": user,
                    "port": port,
                    "identity_file": identity_file,
                    "created_at": now,
                    "updated_at": now,
                }
                alias_map[alias] = len(connections)
                connections.append(conn)
                changed = True

        if changed:
            _save_all(connections)
            logger.info(f"批量同步完成，共 {len(connections)} 条连接")

        return connections

