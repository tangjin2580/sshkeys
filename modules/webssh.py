"""
WebSSH — 基于 paramiko + HTTP 长轮询的 Web SSH 终端
使用 REST API 而非 WebSocket，兼容 Waitress。
"""

import os
import sys
import threading
import time
import queue
import select
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

# ============ 会话管理配置 ============
MAX_WEBSSH_SESSIONS = 5  # 最大并发会话数
SESSION_TIMEOUT = 600  # 会话超时（秒），10分钟无活动自动清理
_CLEANUP_INTERVAL = 60  # 清理线程运行间隔（秒）


def _cleanup_stale_sessions():
    """
    定期清理超时或异常的会话。
    运行在后台线程中，每 _CLEANUP_INTERVAL 秒执行一次。
    """
    while True:
        time.sleep(_CLEANUP_INTERVAL)
        now = time.time()
        to_close = []
        with _ssh_lock:
            for sid, info in list(_ssh_sessions.items()):
                last_active = info.get("last_active", info.get("connected_at", now))
                if now - last_active > SESSION_TIMEOUT:
                    to_close.append(sid)
                    logger.info(f"[WebSSH] 会话 {sid} 超时（{int(now - last_active)}秒无活动），自动关闭")
                # 检查 channel 是否已关闭
                channel = info.get("channel")
                if channel and channel.closed:
                    to_close.append(sid)
                    logger.info(f"[WebSSH] 会话 {sid} 的 channel 已关闭，清理")

        for sid in to_close:
            try:
                _close_ssh_session(sid)
            except Exception as e:
                logger.warning(f"[WebSSH] 清理会话 {sid} 时出错: {e}")

        if to_close:
            logger.info(f"[WebSSH] 本次清理了 {len(to_close)} 个超时/关闭的会话，当前活跃: {len(_ssh_sessions)}")


def _start_cleanup_thread():
    """启动会话清理后台线程（守护线程）"""
    t = threading.Thread(target=_cleanup_stale_sessions, daemon=True, name="WebSSH-Cleanup")
    t.start()
    logger.info(f"[WebSSH] 会话清理线程已启动（间隔 {_CLEANUP_INTERVAL}s，超时 {SESSION_TIMEOUT}s）")


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
    # 设置 keepalive，每 30 秒发送心跳包，防止 NAT 超时导致僵尸连接
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(30)
    return client


def _put_output(q: queue.Queue, data: str):
    """安全地向输出队列写入数据。队列满时丢弃最旧的数据而非新数据。"""
    try:
        q.put_nowait(data)
    except queue.Full:
        try:
            q.get_nowait()  # 丢弃最旧的数据，腾出位置
            q.put_nowait(data)
        except queue.Empty:
            pass


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

        # 检查是否超过最大会话数
        with _ssh_lock:
            active_count = len(_ssh_sessions)
        if active_count >= MAX_WEBSSH_SESSIONS:
            return jsonify({
                "success": False,
                "error": f"已达到最大会话数（{MAX_WEBSSH_SESSIONS}），请先关闭其他会话"
            }), 429

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

            # 打开 SFTP 通道用于远程文件管理
            # 部分服务器（NAS/路由器/精简系统）未启用 SFTP subsystem，open_sftp 会失败
            # 失败后自动降级为 exec 模式（用 SSH exec 命令模拟文件操作）
            sftp = None
            file_mode = "exec"  # 默认 exec 降级模式
            sftp_error = ""
            try:
                sftp = client.open_sftp()
                file_mode = "sftp"
            except Exception as e:
                sftp_error = str(e)
                logger.warning(f"[WebSSH] SFTP 不可用，降级为 exec 模式: {e}")

            session_id = str(_sessions_next_id)
            _sessions_next_id += 1

            output_q: queue.Queue = queue.Queue(maxsize=1000)

            with _ssh_lock:
                _ssh_sessions[session_id] = {
                    "client": client,
                    "channel": channel,
                    "sftp": sftp,
                    "file_mode": file_mode,  # "sftp" 或 "exec"
                    "file_cwd": "",          # 当前远程工作目录，sftp/ls 时更新
                    "hostname": hostname,
                    "username": username,
                    "connected_at": time.time(),
                    "last_active": time.time(),
                }
                _output_buffers[session_id] = output_q

            # 启动读取线程
            def _read_loop():
                try:
                    while not channel.closed:
                        # 用 select 阻塞等待数据，1 秒超时
                        # 替代 time.sleep(0.01) 忙等待，CPU 占用从 ~100% 降到 ~0%
                        try:
                            r, _, _ = select.select([channel], [], [], 1.0)
                        except (ValueError, OSError):
                            break
                        if not r:
                            continue
                        if channel.recv_ready():
                            data = channel.recv(65536).decode("utf-8", errors="replace")
                            _put_output(output_q, data)
                        if channel.recv_stderr_ready():
                            data = channel.recv_stderr(65536).decode("utf-8", errors="replace")
                            _put_output(output_q, data)
                    # channel 关闭后读残余数据
                    while channel.recv_ready():
                        data = channel.recv(65536).decode("utf-8", errors="replace")
                        _put_output(output_q, data)
                    output_q.put_nowait(None)  # None 表示连接关闭
                except Exception as e:
                    logger.warning(f"[WebSSH] 读取线程异常: {e}")
                    try:
                        output_q.put_nowait(None)
                    except Exception:
                        pass

            t = threading.Thread(target=_read_loop, daemon=True)
            t.start()

            logger.info(f"[WebSSH] 连接成功 {username}@{hostname} (session={session_id}, file_mode={file_mode})")
            return jsonify({
                "success": True,
                "session_id": session_id,
                "message": f"已连接到 {username}@{hostname}",
                "file_mode": file_mode,
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
            if session:
                session["last_active"] = time.time()
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        try:
            session["channel"].send(input_data)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/webssh/recv", methods=["GET"])
    def _webssh_recv():
        """长轮询获取 SSH 输出。阻塞等待第一条数据，然后一次性 drain 所有积压数据。"""
        session_id = request.args.get("session_id", "")
        timeout = min(float(request.args.get("timeout", "5")), 10)  # 最多 10 秒

        with _ssh_lock:
            output_q = _output_buffers.get(session_id)
            session = _ssh_sessions.get(session_id)
            if session:
                session["last_active"] = time.time()
        if not output_q:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        # 阻塞等待第一条数据（长轮询）
        try:
            first = output_q.get(timeout=timeout)
        except queue.Empty:
            # 超时无数据
            return jsonify({"success": True, "data": "", "closed": False})

        if first is None:
            # 连接已关闭
            return jsonify({"success": True, "data": "", "closed": True})

        # drain 队列中剩余的所有数据，合并为一次返回
        chunks = [first]
        closed = False
        while True:
            try:
                item = output_q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                closed = True
                break
            chunks.append(item)

        return jsonify({
            "success": True,
            "data": "".join(chunks),
            "closed": closed,
        })

    @app.route("/api/webssh/resize", methods=["POST"])
    def _webssh_resize():
        """调整终端大小"""
        data = request.get_json() or {}
        session_id = data.get("session_id", "")
        cols = int(data.get("cols", 120))
        rows = int(data.get("rows", 30))

        with _ssh_lock:
            session = _ssh_sessions.get(session_id)
            if session:
                session["last_active"] = time.time()
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

    # ============================================================
    # 远程文件管理 API（SFTP 优先，exec 降级）
    # ============================================================

    def _get_session(session_id: str):
        """获取会话信息"""
        with _ssh_lock:
            session = _ssh_sessions.get(session_id)
            if session:
                session["last_active"] = time.time()
                return session
        return None

    def _exec_command(client, cmd, timeout=15):
        """在 SSH 连接上执行命令（新 channel，不影响交互式 shell）"""
        try:
            stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            return out, err, code
        except Exception as e:
            return "", str(e), -1

    def _shell_quote(s):
        """安全地用单引号包裹路径，防止 shell 注入"""
        return "'" + s.replace("'", "'\"'\"'") + "'"

    def _exec_ls(client, path):
        """exec 模式列目录：用 ls -la 解析输出（兼容多种 ls 格式）"""
        if not path or path == "~":
            out, err, code = _exec_command(client, "echo $HOME")
            if code == 0:
                path = out.strip()
            else:
                path = "/"

        # 优先用 --time-style=long-iso（GNU/Linux），失败则尝试 BSD 的 -D 格式
        # 如果都失败，回退到默认 ls -la 并用启发式解析
        for fmt_flag in ["--time-style=long-iso", "-D '%Y-%m-%d %H:%M'"]:
            cmd = f"ls -la {fmt_flag} {_shell_quote(path)}"
            out, err, code = _exec_command(client, cmd)
            if code == 0:
                break
        else:
            # 两种格式都不支持，用默认 ls -la
            cmd = f"ls -la {_shell_quote(path)}"
            out, err, code = _exec_command(client, cmd)
            if code != 0:
                return path, []

        # 获取规范路径
        pwd_out, _, _ = _exec_command(client, f"cd {_shell_quote(path)} && pwd -P")
        real_path = pwd_out.strip() if pwd_out.strip() else path

        items = []
        for line in out.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("total "):
                continue
            # 解析 ls -la 输出
            # 前 5 列固定：权限 硬链接数 所有者 组 大小
            # 之后是日期时间列（2~3 列），剩余部分是文件名
            # --time-style=long-iso 格式：日期 2024-01-15（含 -）→ 日期时间共 2 列
            # 默认格式：月份 Jan（不含 -）→ 日期时间共 3 列
            parts = line.split()
            if len(parts) < 8:
                continue
            perms = parts[0]

            # 判断日期时间列数，确定文件名起始位置
            # parts[5] 是日期列的第一个部分
            if '-' in parts[5]:
                # long-iso 格式：2024-01-15 10:30 → 文件名从 parts[7] 开始
                name = ' '.join(parts[7:])
            else:
                # 默认格式：Jan 15 10:30 或 Jan 15 2023 → 文件名从 parts[8] 开始
                name = ' '.join(parts[8:]) if len(parts) >= 9 else ''

            if not name or name == "." or name == "..":
                continue
            # 处理符号链接：name -> target
            is_link = perms.startswith("l")
            if is_link and " -> " in name:
                name = name.split(" -> ")[0]
            is_dir = perms.startswith("d")
            try:
                size = int(parts[4])
            except (ValueError, IndexError):
                size = 0
            items.append({
                "name": name,
                "size": size,
                "is_dir": is_dir,
                "is_link": is_link,
                "mtime": 0,
                "permissions": perms,
            })
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return real_path, items

    def _get_sftp(session_id: str):
        """从会话中获取 SFTP client，不存在返回 None"""
        with _ssh_lock:
            session = _ssh_sessions.get(session_id)
            if session:
                session["last_active"] = time.time()
                return session.get("sftp"), session
        return None, None

    @app.route("/api/webssh/sftp/ls", methods=["GET"])
    def _sftp_ls():
        """列出远程目录内容（自动选择 SFTP 或 exec 模式）"""
        session_id = request.args.get("session_id", "")
        path = request.args.get("path", "").strip()
        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        file_mode = session.get("file_mode", "exec")
        client = session.get("client")
        sftp = session.get("sftp")

        if file_mode == "sftp" and sftp:
            # ===== SFTP 模式 =====
            if not path or path == "~":
                try:
                    path = sftp.normalize(".")
                except Exception:
                    username = session.get("username", "root")
                    path = f"/home/{username}"
            else:
                try:
                    path = sftp.normalize(path)
                except Exception as e:
                    return jsonify({"success": False, "error": f"路径无效: {e}"}), 400
            import stat as stat_mod
            try:
                entries = sftp.listdir_attr(path)
                items = []
                for e in entries:
                    is_dir = stat_mod.S_ISDIR(e.st_mode or 0)
                    is_link = stat_mod.S_ISLNK(e.st_mode or 0)
                    items.append({
                        "name": e.filename,
                        "size": e.st_size or 0,
                        "is_dir": is_dir,
                        "is_link": is_link,
                        "mtime": e.st_mtime or 0,
                        "permissions": stat_mod.filemode(e.st_mode or 0) or "?????????",
                    })
                items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
                # 记录当前远程路径到 session，供上传等功能使用
                with _ssh_lock:
                    session["file_cwd"] = path
                return jsonify({"success": True, "path": path, "entries": items, "mode": "sftp"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            # ===== exec 降级模式 =====
            try:
                real_path, items = _exec_ls(client, path or "~")
                # 记录当前远程路径到 session
                with _ssh_lock:
                    session["file_cwd"] = real_path
                return jsonify({"success": True, "path": real_path, "entries": items, "mode": "exec"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/webssh/sftp/download", methods=["GET"])
    def _sftp_download():
        """下载远程文件（流式传输）"""
        session_id = request.args.get("session_id", "")
        path = request.args.get("path", "").strip()
        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        if not path:
            return jsonify({"success": False, "error": "缺少文件路径"}), 400

        file_mode = session.get("file_mode", "exec")
        sftp = session.get("sftp")
        client = session.get("client")
        filename = os.path.basename(path)

        if file_mode == "sftp" and sftp:
            try:
                st = sftp.stat(path)
                if st.st_mode and not (st.st_mode & 0o040000):  # 不是目录
                    def _generate():
                        with sftp.file(path, "rb") as f:
                            while True:
                                chunk = f.read(65536)
                                if not chunk:
                                    break
                                yield chunk
                    resp = Response(
                        stream_with_context(_generate()),
                        mimetype="application/octet-stream",
                    )
                    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
                    resp.headers["Content-Length"] = str(st.st_size)
                    return resp
                else:
                    return jsonify({"success": False, "error": "不能下载目录"}), 400
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            # exec 模式：用 base64 编码读取，避免二进制文件损坏
            try:
                # 先用 file 命令检测是否为文本文件
                check_cmd = f"file -b {_shell_quote(path)} 2>/dev/null | head -1"
                check_out, _, _ = _exec_command(client, check_cmd)
                is_text = check_out and ('text' in check_out.lower() or 'ascii' in check_out.lower() or 'utf' in check_out.lower())

                if is_text:
                    # 文本文件直接返回
                    out, err, code = _exec_command(client, f"cat {_shell_quote(path)}")
                    if code != 0:
                        return jsonify({"success": False, "error": err or "下载失败"}), 500
                    resp = Response(out.encode("utf-8", errors="replace"), mimetype="application/octet-stream")
                    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
                    resp.headers["Content-Length"] = str(len(out))
                    return resp
                else:
                    # 二进制文件用 base64 编码传输
                    cmd = f"base64 {_shell_quote(path)}"
                    out, err, code = _exec_command(client, cmd, timeout=60)
                    if code != 0:
                        return jsonify({"success": False, "error": err or "下载失败"}), 500
                    import base64 as _b64
                    file_data = _b64.b64decode(out.strip())
                    resp = Response(file_data, mimetype="application/octet-stream")
                    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
                    resp.headers["Content-Length"] = str(len(file_data))
                    return resp
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/webssh/sftp/upload", methods=["POST"])
    def _sftp_upload():
        """上传文件到远程服务器"""
        session_id = request.form.get("session_id", "")
        remote_path = request.form.get("path", "").strip()
        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        if not remote_path:
            # 未指定路径时，使用当前远程工作目录或家目录
            remote_path = session.get("file_cwd", "")
            if not remote_path:
                if file_mode == "sftp" and sftp:
                    try:
                        remote_path = sftp.normalize(".")
                    except Exception:
                        remote_path = f"/home/{session.get('username', 'root')}"
                else:
                    out, _, code = _exec_command(client, "echo $HOME")
                    remote_path = out.strip() if code == 0 and out.strip() else "/"
        dest = remote_path.rstrip("/") + "/" + os.path.basename(uploaded.filename)

        if file_mode == "sftp" and sftp:
            try:
                with sftp.file(dest, "wb") as f:
                    while True:
                        chunk = uploaded.stream.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                return jsonify({"success": True, "message": f"已上传 {uploaded.filename}"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            # exec 模式：通过 stdin 流式写入，避开命令行长度限制
            try:
                file_data = uploaded.read()
                # 用 cat 从 stdin 读取并写入目标文件
                cmd = f"cat > {_shell_quote(dest)}"
                stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
                stdin.write(file_data)
                stdin.close()
                code = stdout.channel.recv_exit_status()
                err = stderr.read().decode("utf-8", errors="replace")
                if code != 0:
                    return jsonify({"success": False, "error": err or "上传失败"}), 500
                return jsonify({"success": True, "message": f"已上传 {uploaded.filename}"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/webssh/sftp/delete", methods=["POST"])
    def _sftp_delete():
        """删除远程文件或目录"""
        data = request.get_json() or {}
        session_id = data.get("session_id", "")
        path = data.get("path", "").strip()
        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        if not path:
            return jsonify({"success": False, "error": "缺少路径"}), 400

        file_mode = session.get("file_mode", "exec")
        sftp = session.get("sftp")
        client = session.get("client")

        if file_mode == "sftp" and sftp:
            try:
                import stat as stat_mod
                st = sftp.stat(path)
                if stat_mod.S_ISDIR(st.st_mode or 0):
                    def _rmdir_recursive(p):
                        for entry in sftp.listdir_attr(p):
                            full = p.rstrip("/") + "/" + entry.filename
                            if stat_mod.S_ISDIR(entry.st_mode or 0):
                                _rmdir_recursive(full)
                            else:
                                sftp.remove(full)
                        sftp.rmdir(p)
                    _rmdir_recursive(path)
                else:
                    sftp.remove(path)
                return jsonify({"success": True, "message": f"已删除 {os.path.basename(path)}"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            # exec 模式：rm -rf
            out, err, code = _exec_command(client, f"rm -rf {_shell_quote(path)}")
            if code != 0:
                return jsonify({"success": False, "error": err or "删除失败"}), 500
            return jsonify({"success": True, "message": f"已删除 {os.path.basename(path)}"})

    @app.route("/api/webssh/sftp/mkdir", methods=["POST"])
    def _sftp_mkdir():
        """创建远程目录"""
        data = request.get_json() or {}
        session_id = data.get("session_id", "")
        path = data.get("path", "").strip()
        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        if not path:
            return jsonify({"success": False, "error": "缺少路径"}), 400

        file_mode = session.get("file_mode", "exec")
        sftp = session.get("sftp")
        client = session.get("client")

        if file_mode == "sftp" and sftp:
            try:
                sftp.mkdir(path)
                return jsonify({"success": True, "message": f"已创建目录 {os.path.basename(path)}"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            out, err, code = _exec_command(client, f"mkdir -p {_shell_quote(path)}")
            if code != 0:
                return jsonify({"success": False, "error": err or "创建失败"}), 500
            return jsonify({"success": True, "message": f"已创建目录 {os.path.basename(path)}"})

    @app.route("/api/webssh/sftp/rename", methods=["POST"])
    def _sftp_rename():
        """重命名远程文件或目录"""
        data = request.get_json() or {}
        session_id = data.get("session_id", "")
        old_path = data.get("old_path", "").strip()
        new_path = data.get("new_path", "").strip()
        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        if not old_path or not new_path:
            return jsonify({"success": False, "error": "缺少路径"}), 400

        file_mode = session.get("file_mode", "exec")
        sftp = session.get("sftp")
        client = session.get("client")

        if file_mode == "sftp" and sftp:
            try:
                sftp.rename(old_path, new_path)
                return jsonify({"success": True, "message": "重命名成功"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            out, err, code = _exec_command(client, f"mv {_shell_quote(old_path)} {_shell_quote(new_path)}")
            if code != 0:
                return jsonify({"success": False, "error": err or "重命名失败"}), 500
            return jsonify({"success": True, "message": "重命名成功"})

    @app.route("/api/webssh/sftp/stat", methods=["GET"])
    def _sftp_stat():
        """获取远程文件/目录信息"""
        session_id = request.args.get("session_id", "")
        path = request.args.get("path", "").strip()
        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        if not path:
            return jsonify({"success": False, "error": "缺少路径"}), 400

        file_mode = session.get("file_mode", "exec")
        sftp = session.get("sftp")
        client = session.get("client")

        if file_mode == "sftp" and sftp:
            try:
                import stat as stat_mod
                st = sftp.stat(path)
                return jsonify({
                    "success": True,
                    "info": {
                        "name": os.path.basename(path),
                        "size": st.st_size or 0,
                        "is_dir": stat_mod.S_ISDIR(st.st_mode or 0),
                        "permissions": stat_mod.filemode(st.st_mode or 0),
                        "mtime": st.st_mtime or 0,
                        "path": path,
                    }
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            # exec 模式：用 stat 命令
            out, err, code = _exec_command(client, f"stat -c '%s %F' {_shell_quote(path)} 2>/dev/null || stat -f '%z %HT' {_shell_quote(path)}")
            if code == 0 and out.strip():
                parts = out.strip().split(None, 1)
                size = 0
                is_dir = False
                if len(parts) >= 2:
                    try:
                        size = int(parts[0])
                    except ValueError:
                        pass
                    is_dir = "directory" in parts[1].lower()
                return jsonify({
                    "success": True,
                    "info": {
                        "name": os.path.basename(path),
                        "size": size,
                        "is_dir": is_dir,
                        "permissions": "?????????",
                        "mtime": 0,
                        "path": path,
                    }
                })
            return jsonify({"success": False, "error": "无法获取文件信息"}), 500

    logger.info("[WebSSH] HTTP API 路由已注册（含 SFTP 文件管理）")
    _start_cleanup_thread()


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
        sftp = session.get("sftp")
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
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
