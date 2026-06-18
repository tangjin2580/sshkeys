"""
连接配置持久化存储 — JSON 文件读写
存储路径: ~/.ssh/connections.json
"""

import json
import uuid
import logging
from datetime import datetime
from pathlib import Path

from modules.ssh_config import get_ssh_dir, safe_chmod

logger = logging.getLogger(__name__)


def _get_store_path() -> Path:
    """获取连接存储文件路径"""
    ssh_dir = get_ssh_dir()
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    return ssh_dir / "connections.json"


def load_all() -> list[dict]:
    """加载所有已保存的连接"""
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
    """保存全部连接到文件"""
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
    connections = load_all()

    # 按 alias 去重，存在则更新
    updated = False
    for c in connections:
        if c.get("alias") == alias:
            c["hostname"] = hostname
            c["user"] = user
            c["port"] = port
            c["identity_file"] = identity_file
            c["updated_at"] = datetime.now().isoformat()
            updated = True
            break

    if not updated:
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
    return {"success": True, "message": f"连接 {alias} 已保存", "connection": conn if not updated else None}


def delete_connection(conn_id: str) -> dict:
    """
    删除指定连接

    Returns:
        {"success": bool, "message": str}
    """
    connections = load_all()
    initial_len = len(connections)
    connections = [c for c in connections if c.get("id") != conn_id]

    if len(connections) == initial_len:
        return {"success": False, "message": "连接不存在"}

    _save_all(connections)
    logger.info(f"连接已删除: {conn_id}")
    return {"success": True, "message": "连接已删除"}


def get_connections_summary() -> list[dict]:
    """返回连接摘要列表（隐藏敏感路径）"""
    return load_all()

