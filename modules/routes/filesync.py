"""
文件同步路由 — 本地 ←→ 远程服务器 文件同步（SCP / SFTP）
通过 SSH 将本地文件/文件夹推送到远程目标目录（支持 macOS → Windows 跨平台）。
"""
import os
import threading
import logging
from pathlib import Path

from flask import Blueprint, request, jsonify

import paramiko

from modules.connections_store import load_all

logger = logging.getLogger(__name__)

filesync_bp = Blueprint("filesync", __name__)

# ---------------- 全局状态 ----------------
_fs_lock = threading.Lock()
_fs_running = False
_fs_logs = []          # [{ts, level, message}]
_fs_progress = {"total": 0, "done": 0, "current": ""}

# 默认目标（与用户常用环境一致，可在页面修改）
DEFAULT_TARGET = {
    "host": "172.1.1.19",
    "port": 22,
    "username": "admin",
    "password": "2324",
    "remote_base": "C:/sync",
}


def _add_log(level, message):
    from datetime import datetime
    entry = {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "message": message,
    }
    with _fs_lock:
        _fs_logs.append(entry)
        if len(_fs_logs) > 500:
            _fs_logs[:] = _fs_logs[-200:]
    logger.info(f"[FILESYNC] {message}")


# ---------------- 辅助：SSH / SFTP ----------------
def _make_client(cfg):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        hostname=cfg["host"],
        port=int(cfg.get("port", 22)),
        username=cfg["username"],
        timeout=10,
        allow_agent=True,
        look_for_keys=True,
    )
    pwd = cfg.get("password")
    key = cfg.get("identity_file")
    if pwd:
        kwargs["password"] = pwd
    if key:
        kwargs["key_filename"] = key
    client.connect(**kwargs)
    return client


def _mkdir_p(sftp, remote_path: str):
    """递归创建远程目录（forward-slash 路径，兼容 Windows OpenSSH）。"""
    parts = [p for p in remote_path.replace("\\", "/").split("/") if p]
    current = ""
    for p in parts:
        current = f"{current}/{p}" if current else p
        try:
            sftp.stat(current)
        except Exception:
            try:
                sftp.mkdir(current)
            except Exception:
                # 根盘符（如 C:）无法创建时跳过
                pass


def _put_file(sftp, local_path, remote_path):
    _mkdir_p(sftp, os.path.dirname(remote_path).replace("\\", "/"))
    sftp.put(local_path, remote_path.replace("\\", "/"))


def _build_items(local_paths, remote_paths):
    """展开目录为具体文件列表，保留相对结构。"""
    items = []
    for local, remote_dir in zip(local_paths, remote_paths):
        local = str(Path(local).expanduser())
        remote_dir = remote_dir.replace("\\", "/").rstrip("/")
        if os.path.isfile(local):
            items.append((local, f"{remote_dir}/{os.path.basename(local)}"))
        elif os.path.isdir(local):
            for root, _dirs, files in os.walk(local):
                rel = os.path.relpath(root, os.path.dirname(local))
                for fname in files:
                    lf = os.path.join(root, fname)
                    rf = f"{remote_dir}/{rel}/{fname}".replace("\\", "/").replace("//", "/")
                    items.append((lf, rf))
        else:
            _add_log("warn", f"路径不存在，已跳过: {local}")
    return items


# ---------------- API ----------------
@filesync_bp.route("/api/filesync/connections", methods=["GET"])
def list_connections():
    """返回已保存的连接，供下拉选择。"""
    try:
        conns = load_all()
        data = [
            {
                "alias": c.get("alias", ""),
                "hostname": c.get("hostname", ""),
                "user": c.get("user", ""),
                "port": c.get("port", 22),
                "identity_file": c.get("identity_file", ""),
            }
            for c in conns
        ]
        return jsonify({"success": True, "connections": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@filesync_bp.route("/api/filesync/browse", methods=["GET"])
def browse():
    """浏览本地目录。"""
    path = request.args.get("path") or str(Path.home())
    if not os.path.exists(path):
        return jsonify({"success": False, "error": "路径不存在"}), 400
    if os.path.isfile(path):
        return jsonify({"success": False, "error": "这是一个文件"}), 400
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            try:
                size = 0 if is_dir else os.path.getsize(full)
            except OSError:
                size = 0
            entries.append({
                "name": name,
                "path": full,
                "is_dir": is_dir,
                "size": size,
            })
        parent = os.path.dirname(path) if path not in ("/", "") else None
        return jsonify({"success": True, "path": path, "parent": parent, "entries": entries})
    except PermissionError:
        return jsonify({"success": False, "error": "权限不足"}), 403


@filesync_bp.route("/api/filesync/test", methods=["POST"])
def test_conn():
    """测试 SSH 连接。"""
    cfg = {**DEFAULT_TARGET, **(request.get_json() or {})}
    try:
        client = _make_client(cfg)
        client.close()
        return jsonify({"success": True, "message": f"✅ 成功连接到 {cfg['host']}"})
    except Exception as e:
        return jsonify({"success": False, "error": f"连接失败: {e}"})


@filesync_bp.route("/api/filesync/sync", methods=["POST"])
def start_sync():
    global _fs_running
    if _fs_running:
        return jsonify({"success": False, "error": "已有同步任务在运行"}), 409

    data = request.get_json() or {}
    cfg = {**DEFAULT_TARGET, **data.get("config", {})}
    local_paths = data.get("local_paths", [])
    remote_paths = data.get("remote_paths", [])
    if not local_paths:
        return jsonify({"success": False, "error": "未选择本地文件/文件夹"}), 400
    if len(local_paths) != len(remote_paths):
        return jsonify({"success": False, "error": "本地路径与远程路径数量不匹配"}), 400

    _fs_running = True
    _add_log("info", f"开始同步 {len(local_paths)} 项 → {cfg['host']}")

    def _run():
        global _fs_running, _fs_progress
        client = None
        try:
            client = _make_client(cfg)
            sftp = client.open_sftp()
            items = _build_items(local_paths, remote_paths)
            with _fs_lock:
                _fs_progress = {"total": len(items), "done": 0, "current": ""}
            for i, (lf, rf) in enumerate(items):
                with _fs_lock:
                    _fs_progress["done"] = i
                    _fs_progress["current"] = os.path.basename(lf)
                try:
                    _put_file(sftp, lf, rf)
                    _add_log("info", f"✓ {lf} → {rf}")
                except Exception as e:
                    _add_log("error", f"✗ {lf}: {e}")
            with _fs_lock:
                _fs_progress["done"] = len(items)
                _fs_progress["current"] = "完成"
            _add_log("success", "同步完成 ✓")
        except Exception as e:
            _add_log("error", f"同步失败: {e}")
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass
            _fs_running = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "message": "同步已启动"})


@filesync_bp.route("/api/filesync/status", methods=["GET"])
def status():
    with _fs_lock:
        return jsonify({
            "running": _fs_running,
            "progress": dict(_fs_progress),
            "logs": _fs_logs[-150:],
        })


@filesync_bp.route("/api/filesync/clear-logs", methods=["POST"])
def clear_logs():
    with _fs_lock:
        _fs_logs.clear()
    return jsonify({"success": True})
