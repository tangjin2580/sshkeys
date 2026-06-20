"""
Flask 服务端 — 提供 REST API + SSE 实时推送
"""

import os
import io
import sys
import json
import queue
import threading
import logging
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify,
    Response, stream_with_context, send_file,
)

from modules.key_generator import SSHKeyGenerator, KEY_TYPES
from modules.key_uploader import KeyUploader
from modules.ssh_config import (
    list_existing_keys,
    parse_ssh_config,
    add_or_update_host,
    save_key_to_ssh_dir,
    get_ssh_dir,
    delete_key_file,
    remove_host_from_config,
)
from modules.connections_store import (
    load_all as load_connections,
    add_connection,
    delete_connection,
    batch_sync_from_config,
)

logger = logging.getLogger(__name__)


# ==================== 统一错误响应 ====================

def error_response(message, code=None, suggestion=None, status=400):
    """
    返回统一的 JSON 错误响应。
    格式: {"success": false, "error": "...", "code": "...", "suggestion": "..."}
    """
    payload = {"success": False, "error": message}
    if code:
        payload["code"] = code
    if suggestion:
        payload["suggestion"] = suggestion
    return jsonify(payload), status


# --- Flask App 初始化 ---

if getattr(sys, "frozen", False):
    # PyInstaller --onefile 模式：资源在临时解压目录中
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ============ 请求日志 & 全局错误处理器 ============

import time as _time
from flask import g

@app.before_request
def _log_request_start():
    """记录请求开始时间和基本信息"""
    g.request_start = _time.time()
    g.request_path = request.path
    # 不记录 SSE 和长轮询（太频繁）
    if not request.path.startswith('/api/events') and not request.path.startswith('/api/webssh/recv'):
        logger.info(f"[REQUEST] {request.method} {request.path} — 开始")

@app.after_request
def _log_request_end(response):
    """记录请求耗时和状态"""
    if hasattr(g, 'request_start'):
        duration = round((_time.time() - g.request_start) * 1000, 2)
        # 只记录耗时超过 500ms 的请求，或错误请求
        if duration > 500 or response.status_code >= 400:
            logger.warning(f"[REQUEST] {g.request_path} — {response.status_code} （耗时 {duration}ms）")
    return response

@app.errorhandler(500)
def _handle_500(e):
    """全局 500 错误处理器：返回 JSON 而非 HTML"""
    logger.exception("[500] 未捕获的异常")
    return jsonify({
        "success": False,
        "error": "服务器内部错误，请查看日志",
        "code": "INTERNAL_ERROR"
    }), 500


# 注册 WebSSH HTTP API 路由（不再使用 SocketIO）
try:
    from modules.webssh import register_webssh_routes, cleanup_all_sessions
    register_webssh_routes(app)
    logger.info("  [WebSSH] HTTP API 路由已注册")
except Exception as e:
    logger.warning(f"  [WebSSH] 注册失败: {e}")



# SSE 消息队列 (全局，每个请求一个队列)
_sse_queues: list[queue.Queue] = []

# 存储最近生成的密钥（会话级）
_current_keys: dict = {}


def _sse_broadcast(event: str, data: dict):
    """向所有已连接的 SSE 客户端广播消息"""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    dead_queues = []
    for q in _sse_queues:
        try:
            q.put_nowait(msg)
        except queue.Full:
            dead_queues.append(q)
    for q in dead_queues:
        _sse_queues.remove(q)


def _create_progress_callback():
    """创建一个向 SSE 推送进度的回调函数"""
    def callback(message: str):
        _sse_broadcast("progress", {"message": message, "time": datetime.now().strftime("%H:%M:%S")})
    return callback


# ==================== 页面路由 ====================

@app.route("/")
def index():
    """主页面"""
    return render_template("index.html", key_types=KEY_TYPES)


# ==================== SSE 端点 ====================

@app.route("/api/events")
def sse_events():
    """SSE 事件流（限时连接，防止占满线程）"""
    q: queue.Queue = queue.Queue(maxsize=100)
    _sse_queues.append(q)
    _sse_start = _time.time()
    _SSE_MAX_LIFETIME = 120  # 单次连接最多 120 秒，断开后前端自动重连

    def generate():
        try:
            # 发送初始连接确认
            yield f"event: connected\ndata: {json.dumps({'message': 'SSE 已连接'})}\n\n"
            while True:
                # 超过最大生存时间，主动断开（前端 EventSource 会自动重连）
                if _time.time() - _sse_start > _SSE_MAX_LIFETIME:
                    yield f"event: reconnect\ndata: {json.dumps({'message': '请重连'})}\n\n"
                    break
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            if q in _sse_queues:
                _sse_queues.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== API 端点 ====================

@app.route("/api/key-types", methods=["GET"])
def get_key_types():
    """获取支持的密钥类型列表"""
    return jsonify(KEY_TYPES)


@app.route("/api/generate", methods=["POST"])
def generate_key():
    """
    生成 SSH 密钥对
    请求体: {"key_type": "ed25519", "key_size": 256, "passphrase": "", "comment": "user@host"}
    """
    global _current_keys
    data = request.get_json() or {}

    key_type = data.get("key_type", "ed25519")
    key_size = data.get("key_size", 256)
    passphrase = data.get("passphrase", "") or None
    comment = data.get("comment", "user@host").strip() or "user@host"
    save_path = data.get("save_path", "")
    curve = data.get("curve") or None

    _sse_broadcast("progress", {"message": f"开始生成 {key_type.upper()} 密钥 ...", "time": datetime.now().strftime("%H:%M:%S")})

    try:
        priv_str, pub_str, priv_bytes, pub_bytes = SSHKeyGenerator.generate_key_pair(
            key_type=key_type,
            key_size=key_size,
            passphrase=passphrase,
            comment=comment,
            curve=curve,
        )

        # 存储到会话
        _current_keys = {
            "private_key": priv_str,
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
        }

        # 如果指定了保存路径，写入文件
        save_result = None
        if save_path:
            try:
                private_path = os.path.join(save_path, f"id_{key_type}")
                public_path = private_path + ".pub"
                SSHKeyGenerator.save_key_files(priv_str, pub_str, private_path, public_path)
                save_result = {"private": private_path, "public": public_path}
                _sse_broadcast("progress", {"message": f"密钥已保存到: {private_path}", "time": datetime.now().strftime("%H:%M:%S")})
            except Exception as e:
                _sse_broadcast("progress", {"message": f"保存失败: {e}", "time": datetime.now().strftime("%H:%M:%S")})
                save_result = {"error": str(e)}

        _sse_broadcast("progress", {"message": "✓ 密钥生成完成", "time": datetime.now().strftime("%H:%M:%S")})
        # 计算指纹
        from modules.key_generator import compute_fingerprint
        fingerprint = compute_fingerprint(pub_str)

        _sse_broadcast("key_generated", {
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
            "fingerprint": fingerprint,
            "has_passphrase": bool(passphrase),
            "saved": save_result,
        })

        return jsonify({
            "success": True,
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
            "fingerprint": fingerprint,
            "has_passphrase": bool(passphrase),
            "saved": save_result,
        })

    except Exception as e:
        logger.exception("密钥生成失败")
        _sse_broadcast("progress", {"message": f"✗ 生成失败: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/download-private-key", methods=["GET"])
def download_private_key():
    """下载私钥文件"""
    global _current_keys
    if not _current_keys.get("private_key"):
        return jsonify({"success": False, "error": "没有可下载的私钥，请先生成"}), 404

    key_type = _current_keys.get("key_type", "key")
    filename = f"id_{key_type}"

    return send_file(
        io.BytesIO(_current_keys["private_key"].encode("utf-8")),
        mimetype="application/x-pem-file",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/upload", methods=["POST"])
def upload_key():
    """
    上传公钥到指定目标
    请求体: {
        "target": "github" | "gitlab" | "server",
        "token": "...",           // GitHub/GitLab 需要
        "title": "SSH Key",
        "host": "...",            // server 模式需要
        "username": "...",        // server 模式需要
        "password": "...",        // server 模式可选
        "port": 22,               // server 模式可选
        "key_name": "...",        // 指定使用已有密钥文件名（可选）
        "public_key": "...",      // 或直接提供公钥内容（可选）
        "gitlab_url": "..."       // GitLab 自托管实例 (可选)
    }
    """
    global _current_keys
    data = request.get_json() or {}

    # 优先使用指定的密钥文件
    key_name = data.get("key_name", "").strip()
    public_key = data.get("public_key", "").strip()
    
    if key_name:
        # 从指定密钥文件读取公钥
        try:
            ssh_dir = get_ssh_dir()
            key_file = ssh_dir / key_name
            pub_key_file = ssh_dir / f"{key_name}.pub"
            
            if not key_file.exists():
                return jsonify({"success": False, "error": f"密钥文件不存在: {key_name}"}), 404
            if not pub_key_file.exists():
                return jsonify({"success": False, "error": f"公钥文件不存在: {key_name}.pub"}), 404
            
            with open(pub_key_file, 'r', encoding='utf-8') as f:
                public_key = f.read().strip()
        except Exception as e:
            logger.exception(f"读取密钥文件失败: {key_name}")
            return jsonify({"success": False, "error": f"读取密钥文件失败: {str(e)}"}), 500
    elif public_key:
        # 使用前端传来的公钥
        pass
    elif _current_keys.get("public_key"):
        # 使用最近生成的密钥
        public_key = _current_keys["public_key"]
    else:
        return jsonify({"success": False, "error": "请先生成密钥或选择已有密钥"}), 400

    target = data.get("target", "server")
    progress_cb = _create_progress_callback()

    try:
        if target == "github":
            token = data.get("token", "").strip()
            if not token:
                return jsonify({"success": False, "error": "请输入 GitHub Token"}), 400
            result = KeyUploader.upload_to_github(
                public_key=public_key,
                token=token,
                title=data.get("title", "SSH Key Manager"),
                progress_callback=progress_cb,
            )

        elif target == "gitlab":
            token = data.get("token", "").strip()
            if not token:
                return jsonify({"success": False, "error": "请输入 GitLab Token"}), 400
            result = KeyUploader.upload_to_gitlab(
                public_key=public_key,
                token=token,
                title=data.get("title", "SSH Key Manager"),
                gitlab_url=data.get("gitlab_url", "https://gitlab.com"),
                progress_callback=progress_cb,
            )

        elif target == "server":
            host = data.get("host", "").strip()
            username = data.get("username", "").strip()
            if not host or not username:
                return jsonify({"success": False, "error": "请输入服务器地址和用户名"}), 400
            result = KeyUploader.upload_to_server(
                public_key=public_key,
                host=host,
                username=username,
                password=data.get("password") or None,
                port=data.get("port", 22),
                progress_callback=progress_cb,
            )

        else:
            return jsonify({"success": False, "error": f"不支持的上传目标: {target}"}), 400

        _sse_broadcast("upload_result", result)
        return jsonify(result)

    except Exception as e:
        logger.exception("上传失败")
        _sse_broadcast("progress", {"message": f"✗ 上传异常: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== SSH Config & 本地密钥管理 ====================

@app.route("/api/existing-keys", methods=["GET"])
def get_existing_keys():
    """获取本地已有密钥列表"""
    try:
        key_name = request.args.get("key_name")
        key_type = request.args.get("key_type", "ed25519")
        if key_name:
            # 查询单个密钥的公钥
            ssh_dir = get_ssh_dir()
            pub_path = ssh_dir / f"{key_name}.pub"
            if pub_path.exists():
                with open(pub_path, "r", encoding="utf-8") as f:
                    return jsonify({"success": True, "public_key": f.read().strip()})
            return jsonify({"success": False, "error": "文件不存在"}), 404

        keys = list_existing_keys()
        logger.info(f"找到 {len(keys)} 个密钥: {[k['name'] for k in keys]}")
        return jsonify({"success": True, "keys": keys})
    except Exception as e:
        logger.exception("读取已有密钥失败")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/ssh-config", methods=["GET"])
def get_ssh_config():
    """获取本地 ~/.ssh 已有密钥列表和 config 条目"""
    try:
        keys = list_existing_keys()
        config_entries = parse_ssh_config()
        ssh_dir = str(get_ssh_dir())
        return jsonify({
            "success": True,
            "ssh_dir": ssh_dir,
            "existing_keys": keys,
            "config_entries": config_entries,
        })
    except Exception as e:
        logger.exception("读取 SSH config 失败")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/save-and-setup", methods=["POST"])
def save_and_setup():
    """
    一键：保存私钥到 ~/.ssh + 写入 SSH config + （可选）上传到服务器
    请求体: {
        "host_alias": "myserver",       // SSH config Host 别名
        "hostname": "192.168.1.100",    // 服务器地址
        "user": "root",                 // SSH 用户名
        "port": 22,                     // SSH 端口
        "upload": true,                 // 是否同时上传公钥到服务器
        "upload_password": "xxx"        // 上传所需的密码（可选）
    }
    """
    global _current_keys
    data = request.get_json() or {}

    if not _current_keys.get("private_key"):
        return jsonify({"success": False, "error": "请先生成密钥"}), 400

    host_alias = data.get("host_alias", "").strip()
    hostname = data.get("hostname", "").strip()
    user = data.get("user", "").strip()
    port = data.get("port", 22)
    do_upload = data.get("upload", False)

    if not host_alias or not hostname or not user:
        return jsonify({"success": False, "error": "请填写 Host 别名、服务器地址和用户名"}), 400

    progress_cb = _create_progress_callback()
    results = {"saved": None, "config": None, "upload": None}

    try:
        # 1. 保存私钥到 ~/.ssh
        _sse_broadcast("progress", {"message": "正在保存密钥到 ~/.ssh ...", "time": datetime.now().strftime("%H:%M:%S")})
        saved = save_key_to_ssh_dir(
            private_key_str=_current_keys["private_key"],
            public_key_str=_current_keys["public_key"],
            key_type=_current_keys.get("key_type", "ed25519"),
        )
        results["saved"] = saved
        _sse_broadcast("progress", {"message": f"✓ 密钥已保存: {saved['filename']}", "time": datetime.now().strftime("%H:%M:%S")})

        # 2. 写入 SSH config
        _sse_broadcast("progress", {"message": f"正在写入 SSH config (Host {host_alias}) ...", "time": datetime.now().strftime("%H:%M:%S")})
        identity_file = f"~/.ssh/{saved['filename']}"
        add_or_update_host(
            host_alias=host_alias,
            hostname=hostname,
            user=user,
            identity_file=identity_file,
            port=port,
        )
        results["config"] = {"host": host_alias, "hostname": hostname, "user": user, "port": port}
        _sse_broadcast("progress", {"message": f"✓ SSH config 已写入: Host {host_alias}", "time": datetime.now().strftime("%H:%M:%S")})

        # 2.5 同步保存到连接管理
        add_connection(
            alias=host_alias,
            hostname=hostname,
            user=user,
            identity_file=identity_file,
            port=port,
        )
        _sse_broadcast("progress", {"message": f"✓ 连接已录入: {host_alias}", "time": datetime.now().strftime("%H:%M:%S")})

        # 3. 可选：同时上传公钥到服务器
        if do_upload:
            _sse_broadcast("progress", {"message": f"正在上传公钥到 {hostname} ...", "time": datetime.now().strftime("%H:%M:%S")})
            upload_result = KeyUploader.upload_to_server(
                public_key=_current_keys["public_key"],
                host=hostname,
                username=user,
                password=data.get("upload_password") or None,
                port=port,
                progress_callback=progress_cb,
            )
            results["upload"] = upload_result
            if upload_result.get("success"):
                _sse_broadcast("progress", {"message": "✓ 公钥已上传到服务器", "time": datetime.now().strftime("%H:%M:%S")})
            else:
                _sse_broadcast("progress", {"message": "⚠ 密钥已保存但上传失败: " + upload_result.get("message", ""), "time": datetime.now().strftime("%H:%M:%S")})

        # 汇总结果
        all_ok = results["saved"] is not None and results["config"] is not None
        if do_upload:
            all_ok = all_ok and results["upload"] is not None and results["upload"].get("success")

        summary = "🎉 全部完成！现在可以用 `ssh {alias}` 免密登录了".format(alias=host_alias)
        if not all_ok:
            summary = "⚠ 部分操作完成，请查看日志"

        _sse_broadcast("setup_complete", {"success": all_ok, "message": summary, "results": results})

        return jsonify({
            "success": all_ok,
            "message": summary,
            "results": results,
        })

    except Exception as e:
        logger.exception("一键部署失败")
        _sse_broadcast("progress", {"message": f"✗ 部署失败: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e), "results": results}), 500


# ==================== 删除管理 ====================

@app.route("/api/delete-key", methods=["POST"])
def delete_key():
    """删除指定密钥文件"""
    data = request.get_json() or {}
    key_name = data.get("key_name", "").strip()
    if not key_name:
        return jsonify({"success": False, "error": "请指定密钥文件名"}), 400

    result = delete_key_file(key_name)
    _sse_broadcast("progress", {"message": result["message"], "time": datetime.now().strftime("%H:%M:%S")})
    return jsonify(result)


@app.route("/api/delete-config-host", methods=["POST"])
def delete_config_host():
    """删除指定 SSH config Host 条目"""
    data = request.get_json() or {}
    host_alias = data.get("host_alias", "").strip()
    if not host_alias:
        return jsonify({"success": False, "error": "请指定 Host 别名"}), 400

    result = remove_host_from_config(host_alias)
    _sse_broadcast("progress", {"message": result["message"], "time": datetime.now().strftime("%H:%M:%S")})
    return jsonify(result)


@app.route("/api/ssh-config/batch", methods=["POST"])
def batch_update_ssh_config():
    """
    批量写入 SSH config 条目（追加/覆盖）
    请求体: {
        "entries": [
            {"host": "srv1", "hostname": "1.2.3.4", "user": "root", "port": 22, "identityfile": "~/.ssh/id_ed25519"},
            ...
        ]
    }
    """
    data = request.get_json() or {}
    entries = data.get("entries", [])

    if not entries or not isinstance(entries, list):
        return jsonify({"success": False, "error": "请提供 entries 数组"}), 400

    success_count = 0
    errors = []

    for i, entry in enumerate(entries):
        host = entry.get("host", "").strip()
        hostname = entry.get("hostname", "").strip()
        user = entry.get("user", "").strip()
        port = entry.get("port", 22)
        identityfile = entry.get("identityfile", "").strip()

        if not host or not hostname or not user:
            errors.append(f"条目 {i+1}: Host/HostName/User 为必填")
            continue

        try:
            ok = add_or_update_host(
                host_alias=host,
                hostname=hostname,
                user=user,
                identity_file=identityfile or f"~/.ssh/id_{host}",
                port=int(port) if port else 22,
            )
            if ok:
                success_count += 1
                _sse_broadcast("progress", {
                    "message": f"✓ 已写入: Host {host}",
                    "time": datetime.now().strftime("%H:%M:%S")
                })
            else:
                errors.append(f"条目 {i+1} ({host}): 写入失败")
        except Exception as e:
            errors.append(f"条目 {i+1} ({host}): {str(e)}")

    if success_count == 0 and errors:
        return jsonify({"success": False, "error": "全部失败", "errors": errors}), 500

    result = {
        "success": True,
        "count": success_count,
        "errors": errors if errors else None,
    }
    _sse_broadcast("progress", {
        "message": f"批量写入完成: {success_count}/{len(entries)} 个条目",
        "time": datetime.now().strftime("%H:%M:%S")
    })
    return jsonify(result)


# ==================== 连接管理 ====================

@app.route("/api/connections", methods=["GET"])
def list_connections():
    """获取所有连接 — 自动从 ~/.ssh/config 同步"""
    try:
        # 1. 解析 SSH config，批量同步到 connections.json（1 次读 + 1 次写）
        config_entries = parse_ssh_config()
        conns = batch_sync_from_config(config_entries)

        # 2. 附加密钥有效性检查
        for c in conns:
            idf = c.get("identity_file", "")
            if idf:
                # 展开 ~ 为 home 目录
                resolved = Path(idf).expanduser()
                c["key_valid"] = resolved.exists()
            else:
                c["key_valid"] = True  # 无显式密钥，依赖 ssh agent 或默认路径
        return jsonify({"success": True, "connections": conns})
    except Exception as e:
        logger.exception("读取连接列表失败")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/connections", methods=["POST"])
def save_connection():
    """手动保存一条连接"""
    data = request.get_json() or {}
    alias = data.get("alias", "").strip()
    hostname = data.get("hostname", "").strip()
    user = data.get("user", "").strip()
    port = data.get("port", 22)
    identity_file = data.get("identity_file", "").strip()

    if not alias or not hostname or not user:
        return jsonify({"success": False, "error": "请填写 alias、hostname、user"}), 400

    result = add_connection(
        alias=alias,
        hostname=hostname,
        user=user,
        identity_file=identity_file,
        port=port,
    )
    return jsonify(result)


@app.route("/api/connections/<conn_id>", methods=["DELETE"])
def delete_connection_route(conn_id):
    """删除一条连接"""
    result = delete_connection(conn_id)
    return jsonify(result)


@app.route("/api/connections/connect", methods=["POST"])
def connect_to_server():
    """
    一键打开终端 SSH 连接到服务器
    平台适配: macOS → Terminal.app/iTerm2 | Windows → wt/cmd | Linux → gnome-terminal/konsole/xterm
    """
    import shutil
    import subprocess
    import sys
    
    data = request.get_json() or {}
    alias = data.get("alias", "").strip()
    terminal_path = data.get("terminal_path", "").strip()

    if not alias:
        return error_response(
            "请指定连接别名",
            code="INVALID_PARAM",
            suggestion="请在「我的连接」中选择一个连接，或先添加一个新连接"
        )
    
    # 校验 alias 是否在连接列表中（可选，用于更友好的错误提示）
    try:
        connections = load_connections()
        matched = [c for c in connections if c.get("alias") == alias]
        if not matched:
            logger.warning(f"连接别名 '{alias}' 不在保存的连接列表中，但仍将尝试连接（可能是 Raw Host）")
    except Exception:
        pass

    _sse_broadcast("progress", {"message": f"正在启动 SSH 连接到 {alias} ...", "time": datetime.now().strftime("%H:%M:%S")})
    
    try:
        if sys.platform == "win32":
            # Windows 终端启动逻辑
            def exists(path):
                if not path:
                    return False
                r = subprocess.run(["where", path], capture_output=True, text=True)
                return r.returncode == 0
            
            success = False
            
            # 1. 尝试用户指定的终端
            if terminal_path and exists(terminal_path):
                try:
                    subprocess.Popen([terminal_path, "ssh", alias], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    success = True
                    logger.info(f"使用指定终端启动: {terminal_path}")
                except Exception as e:
                    logger.warning(f"指定终端启动失败: {e}")
                    success = False
            
            # 2. 回退到 wt.exe
            if not success and exists("wt.exe"):
                try:
                    subprocess.Popen(["wt.exe", "ssh", alias], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    success = True
                    logger.info("使用 Windows Terminal 启动")
                except Exception as e:
                    logger.warning(f"Windows Terminal 启动失败: {e}")
            
            # 3. 回退到 powershell
            if not success and exists("powershell.exe"):
                try:
                    subprocess.Popen(["powershell.exe", "-NoExit", "-Command", f"ssh {alias}"], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    success = True
                    logger.info("使用 PowerShell 启动")
                except Exception as e:
                    logger.warning(f"PowerShell 启动失败: {e}")
            
            # 4. 最后回退到 cmd
            if not success:
                try:
                    subprocess.Popen(["cmd.exe", "/c", "start", "cmd", "/k", f"ssh {alias}"], shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    success = True
                    logger.info("使用 CMD 启动")
                except Exception as e:
                    logger.error(f"CMD 启动失败: {e}")
                    return jsonify({"success": False, "error": f"无法启动终端: {str(e)}"}), 500
            
            if not success:
                return error_response(
                    "无法找到可用的终端模拟器",
                    code="TERMINAL_NOT_FOUND",
                    suggestion="请安装 Windows Terminal、PowerShell 或 Git Bash，或在「本地终端设置」中手动指定终端路径"
                )

        elif sys.platform == "darwin":
            # macOS 终端启动逻辑
            logger.info(f"macOS: 尝试启动 SSH 连接 {alias}")
            
            # 1. 尝试使用指定的终端路径
            if terminal_path and os.path.exists(terminal_path):
                try:
                    if "iTerm" in terminal_path:
                        # iTerm2 特殊处理
                        script = f'tell application "iTerm2" to create terminal with profile "Default" command "ssh {alias}"'
                        subprocess.Popen(["osascript", "-e", script])
                    else:
                        # Terminal.app
                        script = f'tell application "Terminal" to do script "ssh {alias}"'
                        subprocess.Popen(["osascript", "-e", script])
                    logger.info(f"使用指定终端启动: {terminal_path}")
                except Exception as e:
                    logger.warning(f"指定终端启动失败: {e}")
            
            # 2. 尝试使用 iTerm2 (如果已安装)
            try:
                if subprocess.run(["osascript", "-e", 'tell app "iTerm2" to get name'], capture_output=True).returncode == 0:
                    script = f'tell application "iTerm2" to create terminal with profile "Default" command "ssh {alias}"'
                    subprocess.Popen(["osascript", "-e", script])
                    logger.info("使用 iTerm2 启动")
                else:
                    raise Exception("iTerm2 未运行")
            except Exception:
                # 3. 回退到 Terminal.app
                try:
                    script = f'tell application "Terminal" to do script "ssh {alias}; echo; echo \\"连接已关闭，按 Cmd+Q 退出\\""'
                    subprocess.Popen(["osascript", "-e", script])
                    logger.info("使用 Terminal.app 启动")
                except Exception as e:
                    logger.error(f"macOS 终端启动失败: {e}")
                    return error_response(
                        f"无法启动终端: {str(e)}",
                        code="TERMINAL_NOT_FOUND",
                        suggestion="请确认 Terminal.app 或 iTerm2 已安装，并且允许此应用控制终端"
                    )

        else:
            # Linux 终端启动逻辑
            logger.info(f"Linux: 尝试启动 SSH 连接 {alias}")
            
            # 终端检测顺序 (按常见程度)
            terminals = [
                ("gnome-terminal", ["gnome-terminal", "--", "bash", "-c", f"ssh {alias}; echo; echo '连接已关闭，按回车退出'; read"]),
                ("konsole", ["konsole", "-e", "bash", "-c", f"ssh {alias}; read"]),
                ("xfce4-terminal", ["xfce4-terminal", "-e", f"bash -c 'ssh {alias}; read'"]),
                ("terminator", ["terminator", "-e", f"bash -c 'ssh {alias}; read'"]),
                ("alacritty", ["alacritty", "-e", "bash", "-c", f"ssh {alias}; read"]),
                ("kitty", ["kitty", "+run", "bash", "-c", f"ssh {alias}; read"]),
                ("xterm", ["xterm", "-e", f"bash -c 'ssh {alias}; read'"]),
                ("x-terminal-emulator", ["x-terminal-emulator", "-e", f"bash -c 'ssh {alias}; read'"]),
            ]
            
            success = False
            for name, cmd in terminals:
                if shutil.which(name):
                    try:
                        subprocess.Popen(cmd)
                        logger.info(f"使用 {name} 启动")
                        success = True
                        break
                    except Exception as e:
                        logger.warning(f"{name} 启动失败: {e}")
                        continue
            
            if not success:
                # 尝试使用 xdg-terminal (跨桌面环境)
                try:
                    subprocess.Popen(["xdg-terminal", "--command", f"bash -c 'ssh {alias}; read'"])
                    success = True
                    logger.info("使用 xdg-terminal 启动")
                except Exception:
                    pass
            
            if not success:
                return error_response(
                    "无法找到可用的终端模拟器",
                    code="TERMINAL_NOT_FOUND",
                    suggestion="请安装 GNOME Terminal、Konsole 或 xterm，然后重试。\n或者手动执行: ssh " + alias
                )

        _sse_broadcast("progress", {"message": f"✓ 已启动终端 SSH 连接到 {alias}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": True, "message": f"终端已打开，正在连接 {alias}"})

    except Exception as e:
        logger.exception("启动终端失败")
        return error_response(
            f"启动终端失败: {str(e)}",
            code="SSH_CONNECT_FAILED",
            suggestion="请检查：1) SSH 密钥是否已生成并上传；2) 服务器地址是否正确；3) 网络连接是否正常"
        )


# ==================== 平台信息 API ====================

@app.route("/api/platform-info", methods=["GET"])
def platform_info():
    """
    返回当前运行平台信息。
    用于前端根据平台显示不同的终端候选列表。
    """
    import sys
    import os
    
    platform = sys.platform
    info = {
        "platform": platform,
        "is_windows": platform == "win32",
        "is_macos": platform == "darwin",
        "is_linux": platform == "linux",
        "platform_name": {
            "win32": "Windows",
            "darwin": "macOS",
            "linux": "Linux"
        }.get(platform, platform)
    }
    
    # 尝试获取更详细的版本信息
    if platform == "win32":
        try:
            import platform as plat
            info["version"] = plat.version()
            info["release"] = plat.release()
        except Exception:
            pass
    elif platform == "darwin":
        try:
            import platform as plat
            info["mac_version"] = plat.mac_ver()[0]
        except Exception:
            pass
    elif platform == "linux":
        try:
            import distro
            info["distro"] = distro.name()
            info["distro_version"] = distro.version()
        except ImportError:
            # distro 未安装，尝试读取 /etc/os-release
            try:
                with open("/etc/os-release", "r") as f:
                    for line in f:
                        if line.startswith("NAME="):
                            info["distro"] = line.split("=")[1].strip().strip('"')
                            break
            except Exception:
                pass
    
    return jsonify(info)


# ==================== 终端路径检测 ====================

@app.route("/api/check-terminal-path", methods=["POST"])
def check_terminal_path():
    """
    检测终端路径是否有效。
    接受 POST，body: {"path": "wt.exe"} 或 {"path": "C:\\path\\to\\term.exe"}
    返回: {"valid": bool, "path": str, "message": str}
    """
    data = request.get_json() or {}
    path = data.get("path", "").strip()
    if not path:
        return jsonify({"valid": False, "message": "路径不能为空"}), 400

    import shutil
    import os

    # 判断是否是完整路径（包含路径分隔符）
    is_absolute = "/" in path or "\\" in path

    if is_absolute:
        # 完整路径：直接检查文件是否存在
        exists = os.path.isfile(path)
        if exists:
            return jsonify({
                "valid": True,
                "path": path,
                "message": f"✅ 路径有效：{path}"
            })
        else:
            return jsonify({
                "valid": False,
                "path": path,
                "message": f"❌ 文件不存在：{path}"
            })

    # 不是完整路径：在 PATH 中查找
    found = shutil.which(path)
    if found:
        return jsonify({
            "valid": True,
            "path": found,
            "message": f"✅ 已找到：{found}"
        })

    # Windows 下尝试加 .exe 后缀
    if sys.platform == "win32" and not path.lower().endswith(".exe"):
        found = shutil.which(path + ".exe")
        if found:
            return jsonify({
                "valid": True,
                "path": found,
                "message": f"✅ 已找到：{found}"
            })

    return jsonify({
        "valid": False,
        "path": path,
        "message": f"❌ 未在 PATH 中找到：{path}"
    })


# ==================== 启动 ====================

def create_app():
    """
    创建 Flask 应用实例。
    返回 app，供 main.py 使用。
    （WebSSH 路由已在模块级别注册）
    """
    return app
