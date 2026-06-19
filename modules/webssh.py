"""
WebSSH — 基于 paramiko + HTTP 长轮询的 Web SSH 终端
使用 REST API 而非 WebSocket，兼容 Waitress。
"""

import os
import sys
import threading
import time
import queue
import logging
import paramiko
from pathlib import Path

logger = logging.getLogger(__name__)

# 每个浏览器会话一个 SSH 连接，用 session_id 索引
_ssh_sessions: dict[str, dict] = {}
_ssh_lock = threading.Lock()
_sessions_next_id = 0

# 输出缓冲区：每个会话一个 Queue
_output_buffers: dict[str, queue.Queue] = {}


def get_ssh_dir() -> Path:
    """返回 ~/.ssh 目录"""
    return Path.home() / ".ssh"


def _resolve_identity_file(id_file: str) -> str | None:
    """解析 IdentityFile 路径，支持 ~ 和相对路径"""
    if not id_file:
        return None
    p = Path(id_file).expanduser().resolve()
    if p.exists():
        return str(p)
    alt = get_ssh_dir() / Path(id_file).name
    if alt.exists():
        return str(alt)
    return None


def _create_ssh_client(hostname: str, port: int, username: str,
                       password: str | None = None,
                       identity_file: str | None = None) -> paramiko.SSHClient:
    """创建并连接 SSH 客户端"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": hostname,
        "port": port,
        "username": username,
        "timeout": 10,
        "allow_agent": False,
        "look_for_keys": False,
    }

    if identity_file:
        resolved = _resolve_identity_file(identity_file)
        if resolved:
            logger.info(f"使用密钥文件: {resolved}")
            try:
                pkey = paramiko.RSAKey.from_private_key_file(resolved)
                connect_kwargs["pkey"] = pkey
            except Exception:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(resolved)
                    connect_kwargs["pkey"] = pkey
                except Exception:
                    try:
                        pkey = paramiko.ECDSAKey.from_private_key_file(resolved)
                        connect_kwargs["pkey"] = pkey
                    except Exception as e:
                        logger.warning(f"无法加载密钥 {resolved}: {e}")
            connect_kwargs["allow_agent"] = True
            connect_kwargs["look_for_keys"] = True

    if password:
        connect_kwargs["password"] = password
        connect_kwargs["allow_agent"] = True
        connect_kwargs["look_for_keys"] = True

    client.connect(**connect_kwargs)
    return client


def register_webssh_routes(app):
    """
    注册 WebSSH 相关的 HTTP API 路由。
    在 server.py 的 create_app() 中调用。
    """
    from flask import request, jsonify, Response, stream_with_context
    import json

    @app.route("/api/webssh/connect", methods=["POST"])
    def _webssh_connect():
        """建立 SSH 连接"""
        global _sessions_next_id
        data = request.get_json() or {}

        hostname = data.get("hostname", "").strip()
        port = int(data.get("port", 22))
        username = data.get("username", "").strip()
        password = data.get("password", "") or None
        identity_file = data.get("identity_file", "") or None
        alias = data.get("alias", "").strip()

        if not hostname or not username:
            return jsonify({"success": False, "error": "缺少服务器地址或用户名"}), 400

        try:
            client = _create_ssh_client(
                hostname=hostname,
                port=port,
                username=username,
                password=password,
                identity_file=identity_file,
            )

            channel = client.get_transport().open_session()
            channel.get_pty(
                term=data.get("term", "xterm-256color"),
                width=data.get("cols", 120),
                height=data.get("rows", 30),
            )
            channel.invoke_shell()

            session_id = str(_sessions_next_id)
            _sessions_next_id += 1

            output_q: queue.Queue = queue.Queue(maxsize=1000)

            with _ssh_lock:
                _ssh_sessions[session_id] = {
                    "client": client,
                    "channel": channel,
                    "hostname": hostname,
                    "username": username,
                    "connected_at": time.time(),
                }
                _output_buffers[session_id] = output_q

            # 启动读取线程
            def _read_loop():
                try:
                    while not channel.exit_status_ready():
                        if channel.recv_ready():
                            data = channel.recv(4096).decode("utf-8", errors="replace")
                            try:
                                output_q.put_nowait(data)
                            except queue.Full:
                                pass  # 丢弃最老的数据
                        elif channel.recv_stderr_ready():
                            data = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                            try:
                                output_q.put_nowait(data)
                            except queue.Full:
                                pass
                        else:
                            time.sleep(0.01)
                    # 退出后读残余数据
                    while channel.recv_ready():
                        data = channel.recv(4096).decode("utf-8", errors="replace")
                        try:
                            output_q.put_nowait(data)
                        except queue.Full:
                            pass
                    output_q.put_nowait(None)  # None 表示连接关闭
                except Exception as e:
                    logger.warning(f"[WebSSH] 读取线程异常: {e}")
                    try:
                        output_q.put_nowait(None)
                    except Exception:
                        pass

            t = threading.Thread(target=_read_loop, daemon=True)
            t.start()

            logger.info(f"[WebSSH] 连接成功 {username}@{hostname} (session={session_id})")
            return jsonify({
                "success": True,
                "session_id": session_id,
                "message": f"已连接到 {username}@{hostname}"
            })

        except Exception as e:
            logger.exception(f"[WebSSH] 连接失败: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/webssh/send", methods=["POST"])
    def _webssh_send():
        """发送输入到 SSH"""
        data = request.get_json() or {}
        session_id = data.get("session_id", "")
        input_data = data.get("data", "")

        with _ssh_lock:
            session = _ssh_sessions.get(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        try:
            session["channel"].send(input_data)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/webssh/recv", methods=["GET"])
    def _webssh_recv():
        """长轮询获取 SSH 输出"""
        session_id = request.args.get("session_id", "")
        timeout = float(request.args.get("timeout", "5"))

        with _ssh_lock:
            output_q = _output_buffers.get(session_id)
        if not output_q:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        # 等待数据（长轮询）
        try:
            data = output_q.get(timeout=timeout)
            if data is None:
                # 连接已关闭
                return jsonify({"success": True, "data": "", "closed": True})
            return jsonify({"success": True, "data": data, "closed": False})
        except queue.Empty:
            # 超时无数据
            return jsonify({"success": True, "data": "", "closed": False})

    @app.route("/api/webssh/resize", methods=["POST"])
    def _webssh_resize():
        """调整终端大小"""
        data = request.get_json() or {}
        session_id = data.get("session_id", "")
        cols = int(data.get("cols", 120))
        rows = int(data.get("rows", 30))

        with _ssh_lock:
            session = _ssh_sessions.get(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        try:
            session["channel"].resize_pty(width=cols, height=rows)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/webssh/disconnect", methods=["POST"])
    def _webssh_disconnect():
        """关闭 SSH 连接"""
        data = request.get_json() or {}
        session_id = data.get("session_id", "")

        if session_id:
            _close_ssh_session(session_id)
            return jsonify({"success": True, "message": "连接已关闭"})
        return jsonify({"success": False, "error": "缺少 session_id"}), 400

    logger.info("[WebSSH] HTTP API 路由已注册")


def _close_ssh_session(session_id: str):
    """关闭指定会话的 SSH 连接"""
    with _ssh_lock:
        session = _ssh_sessions.pop(session_id, None)
        output_q = _output_buffers.pop(session_id, None)
    if not session:
        return
    try:
        channel = session.get("channel")
        if channel:
            channel.close()
        client = session.get("client")
        if client:
            client.close()
        logger.info(f"[WebSSH] 会话 {session_id} 已关闭 ({session.get('username')}@{session.get('hostname')})")
    except Exception as e:
        logger.warning(f"[WebSSH] 关闭会话 {session_id} 时出错: {e}")


def cleanup_all_sessions():
    """关闭所有 SSH 会话（服务关闭时调用）"""
    with _ssh_lock:
        sids = list(_ssh_sessions.keys())
    for sid in sids:
        _close_ssh_session(sid)
    logger.info(f"[WebSSH] 已关闭 {len(sids)} 个会话")
