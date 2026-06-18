"""
Flask 服务端 — 提供 REST API + SSE 实时推送
"""

import os
import io
import json
import queue
import threading
import logging
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify,
    Response, stream_with_context, send_file
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
)

logger = logging.getLogger(__name__)

# --- Flask App 初始化 ---
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))

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


@app.route("/connections")
def connections_page():
    """连接管理页面"""
    return render_template("connections.html", key_types=KEY_TYPES)


# ==================== SSE 端点 ====================

@app.route("/api/events")
def sse_events():
    """SSE 事件流"""
    q: queue.Queue = queue.Queue(maxsize=100)
    _sse_queues.append(q)

    def generate():
        try:
            # 发送初始连接确认
            yield f"event: connected\ndata: {json.dumps({'message': 'SSE 已连接'})}\n\n"
            while True:
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
            "Connection": "keep-alive",
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
        _sse_broadcast("key_generated", {
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
            "has_passphrase": bool(passphrase),
            "saved": save_result,
        })

        return jsonify({
            "success": True,
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
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
        "gitlab_url": "..."       // GitLab 自托管实例 (可选)
    }
    """
    global _current_keys
    data = request.get_json() or {}

    if not _current_keys.get("public_key"):
        return jsonify({"success": False, "error": "请先生成密钥"}), 400

    public_key = _current_keys["public_key"]
    target = data.get("target", "github")
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


# ==================== 连接管理 ====================

@app.route("/api/connections", methods=["GET"])
def list_connections():
    """获取所有连接 — 自动从 ~/.ssh/config 同步"""
    try:
        # 1. 读取已有手动保存的连接
        saved_conns = load_connections()

        # 2. 解析 SSH config，自动导入/更新条目
        config_entries = parse_ssh_config()
        for entry in config_entries:
            alias = entry.get("host", "").split()[0]
            if not alias or alias == "*":
                continue
            # 自动同步到 connections.json（已存在则更新）
            add_connection(
                alias=alias,
                hostname=entry.get("hostname", ""),
                user=entry.get("user", ""),
                identity_file=entry.get("identityfile", ""),
                port=entry.get("port", 22),
            )

        # 3. 返回最新列表，附加密钥有效性检查
        conns = load_connections()
        from pathlib import Path
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
    平台适配: macOS → Terminal.app | Windows → wt/cmd | Linux → gnome-terminal/xterm
    """
    import shutil
    import subprocess
    import sys
    data = request.get_json() or {}
    alias = data.get("alias", "").strip()

    if not alias:
        return jsonify({"success": False, "error": "请指定连接别名"}), 400

    _sse_broadcast("progress", {"message": f"正在启动 SSH 连接到 {alias} ...", "time": datetime.now().strftime("%H:%M:%S")})

    try:
        if sys.platform == "win32":
            # Windows — 优先 Windows Terminal，回退 cmd
            if shutil.which("wt.exe"):
                subprocess.Popen(["wt.exe", "ssh", alias])
            elif shutil.which("cmd.exe"):
                subprocess.Popen(["cmd.exe", "/c", "start", "ssh", alias, "&&", "pause"])
            else:
                return jsonify({"success": False, "error": "无法找到可用终端，请手动执行 ssh " + alias}), 500

        elif sys.platform == "darwin":
            # macOS — 使用 osascript 打开 Terminal
            script = f'tell app "Terminal" to do script "ssh {alias}; echo; echo —— 按任意键关闭窗口 ——; read"'
            subprocess.Popen(["osascript", "-e", script])

        else:
            # Linux — 检测可用终端模拟器
            if shutil.which("gnome-terminal"):
                subprocess.Popen(["gnome-terminal", "--", "bash", "-c", f"ssh {alias}; echo; echo '—— 按回车关闭 ——'; read"])
            elif shutil.which("x-terminal-emulator"):
                subprocess.Popen(["x-terminal-emulator", "-e", f"bash -c 'ssh {alias}; read'"])
            elif shutil.which("xterm"):
                subprocess.Popen(["xterm", "-e", f"ssh {alias}; read"])
            else:
                return jsonify({"success": False, "error": "无法找到可用的终端模拟器，请手动执行 ssh " + alias}), 500

        _sse_broadcast("progress", {"message": f"✓ 已启动终端 SSH 连接到 {alias}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": True, "message": f"终端已打开，正在连接 {alias}"})

    except Exception as e:
        logger.exception("启动终端失败")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== 启动 ====================

def create_app():
    """创建 Flask 应用实例"""
    return app
