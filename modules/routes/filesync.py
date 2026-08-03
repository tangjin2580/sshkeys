"""
文件同步路由 — 本地 ←→ 远程服务器 文件同步（SCP / SFTP）
通过 SSH 将本地文件/文件夹推送到远程目标目录（支持 macOS → Windows 跨平台）。
"""
import os
import threading
import logging
import concurrent.futures
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


def _mkdir_p_cached(sftp, remote_path: str, seen: set):
    """递归创建远程目录（带 seen 缓存，避免同一目录被反复 stat）。"""
    parts = [p for p in remote_path.replace("\\", "/").split("/") if p]
    current = ""
    for p in parts:
        current = f"{current}/{p}" if current else p
        if current in seen:
            continue
        try:
            sftp.stat(current)
        except Exception:
            try:
                sftp.mkdir(current)
            except Exception:
                # 根盘符（如 C:）或并发已创建时跳过
                pass
        seen.add(current)


def _ensure_dirs(sftp, remote_dirs):
    """批量创建所有需要的远程目录，每个目录最多 stat/mkdir 一次。"""
    seen = set()
    for d in sorted(set(remote_dirs), key=lambda x: x.count("/")):
        _mkdir_p_cached(sftp, d, seen)


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
    """浏览本地目录（服务端分页，文件夹优先排序）。"""
    path = request.args.get("path") or str(Path.home())
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = max(10, min(500, int(request.args.get("page_size", 100))))
    except (TypeError, ValueError):
        page, page_size = 1, 100
    if not os.path.exists(path):
        return jsonify({"success": False, "error": "路径不存在"}), 400
    if os.path.isfile(path):
        return jsonify({"success": False, "error": "这是一个文件"}), 400
    try:
        dirs, files = [], []
        for name in os.listdir(path):
            full = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            try:
                size = 0 if is_dir else os.path.getsize(full)
            except OSError:
                size = 0
            entry = {"name": name, "path": full, "is_dir": is_dir, "size": size}
            (dirs if is_dir else files).append(entry)
        dirs.sort(key=lambda e: e["name"].lower())
        files.sort(key=lambda e: e["name"].lower())
        all_entries = dirs + files
        total = len(all_entries)
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * page_size
        slice_entries = all_entries[start:start + page_size]
        parent = os.path.dirname(path) if path not in ("/", "") else None
        return jsonify({
            "success": True,
            "path": path,
            "parent": parent,
            "entries": slice_entries,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        })
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
            if not items:
                _add_log("warn", "没有可同步的文件")
                with _fs_lock:
                    _fs_progress = {"total": 0, "done": 0, "current": ""}
            else:
                # 一次性预建所有远程目录（带缓存，避免每文件反复 stat，这是慢的主因）
                parent_dirs = {os.path.dirname(rf).replace("\\", "/") for _, rf in items}
                _add_log("info", f"准备创建 {len(parent_dirs)} 个远程目录…")
                _ensure_dirs(sftp, parent_dirs)

                with _fs_lock:
                    _fs_progress = {"total": len(items), "done": 0, "current": ""}
                _add_log("info", f"开始并发传输 {len(items)} 个文件…")

                # 每个工作线程使用独立的 SFTP 会话（同一 transport 多通道），实现并发推送
                tlocal = threading.local()

                def do_one(item):
                    lf, rf = item
                    try:
                        if not hasattr(tlocal, "sftp"):
                            tlocal.sftp = client.open_sftp()
                        tlocal.sftp.put(lf, rf.replace("\\", "/"))
                        with _fs_lock:
                            _fs_progress["done"] += 1
                            _fs_progress["current"] = os.path.basename(lf)
                        _add_log("info", f"✓ {os.path.basename(lf)}")
                    except Exception as e:
                        with _fs_lock:
                            _fs_progress["done"] += 1
                        _add_log("error", f"✗ {os.path.basename(lf)}: {e}")

                workers = min(8, max(1, os.cpu_count() or 4))
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    list(ex.map(do_one, items))

                with _fs_lock:
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
