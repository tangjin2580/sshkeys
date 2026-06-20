"""
WebSSH HTTP 路由 — 终端连接 + SFTP 文件管理 API
"""

import os
import time
import queue
import select
import logging
import threading

from flask import request, jsonify, Response, stream_with_context

from modules.webssh_sessions import (
    _ssh_sessions, _ssh_lock, _output_buffers,
    _sessions_next_id, MAX_WEBSSH_SESSIONS,
    _create_ssh_client, _put_output,
    _close_ssh_session, _start_cleanup_thread,
)
from modules.webssh_sftp import (
    _get_session, _get_sftp,
    _exec_command, _shell_quote, _exec_ls,
)

logger = logging.getLogger(__name__)


def register_webssh_routes(app):
    """
    注册 WebSSH 相关的 HTTP API 路由。
    在 server.py 的 create_app() 中调用。
    """

    # ============================================================
    # SSH 终端 API
    # ============================================================

    @app.route("/api/webssh/connect", methods=["POST"])
    def _webssh_connect():
        """建立 SSH 连接"""
        import modules.webssh_sessions as _ws
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
            sftp = None
            file_mode = "exec"
            sftp_error = ""
            try:
                sftp = client.open_sftp()
                file_mode = "sftp"
            except Exception as e:
                sftp_error = str(e)
                logger.warning(f"[WebSSH] SFTP 不可用，降级为 exec 模式: {e}")

            session_id = str(_ws._sessions_next_id)
            _ws._sessions_next_id += 1

            output_q: queue.Queue = queue.Queue(maxsize=1000)

            with _ssh_lock:
                _ssh_sessions[session_id] = {
                    "client": client,
                    "channel": channel,
                    "sftp": sftp,
                    "file_mode": file_mode,
                    "file_cwd": "",
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
                    while channel.recv_ready():
                        data = channel.recv(65536).decode("utf-8", errors="replace")
                        _put_output(output_q, data)
                    output_q.put_nowait(None)
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
        """长轮询获取 SSH 输出。"""
        session_id = request.args.get("session_id", "")
        timeout = min(float(request.args.get("timeout", "5")), 10)

        with _ssh_lock:
            output_q = _output_buffers.get(session_id)
            session = _ssh_sessions.get(session_id)
            if session:
                session["last_active"] = time.time()
        if not output_q:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        try:
            first = output_q.get(timeout=timeout)
        except queue.Empty:
            return jsonify({"success": True, "data": "", "closed": False})

        if first is None:
            return jsonify({"success": True, "data": "", "closed": True})

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

    @app.route("/api/webssh/sftp/ls", methods=["GET"])
    def _sftp_ls():
        """列出远程目录内容"""
        session_id = request.args.get("session_id", "")
        path = request.args.get("path", "").strip()
        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        file_mode = session.get("file_mode", "exec")
        client = session.get("client")
        sftp = session.get("sftp")

        if file_mode == "sftp" and sftp:
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
                with _ssh_lock:
                    session["file_cwd"] = path
                return jsonify({"success": True, "path": path, "entries": items, "mode": "sftp"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        else:
            try:
                real_path, items = _exec_ls(client, path or "~")
                with _ssh_lock:
                    session["file_cwd"] = real_path
                return jsonify({"success": True, "path": real_path, "entries": items, "mode": "exec"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/webssh/sftp/download", methods=["GET"])
    def _sftp_download():
        """下载远程文件"""
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
                if st.st_mode and not (st.st_mode & 0o040000):
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
            try:
                check_cmd = f"file -b {_shell_quote(path)} 2>/dev/null | head -1"
                check_out, _, _ = _exec_command(client, check_cmd)
                is_text = check_out and ('text' in check_out.lower() or 'ascii' in check_out.lower() or 'utf' in check_out.lower())

                if is_text:
                    out, err, code = _exec_command(client, f"cat {_shell_quote(path)}")
                    if code != 0:
                        return jsonify({"success": False, "error": err or "下载失败"}), 500
                    resp = Response(out.encode("utf-8", errors="replace"), mimetype="application/octet-stream")
                    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
                    resp.headers["Content-Length"] = str(len(out))
                    return resp
                else:
                    import base64 as _b64
                    cmd = f"base64 {_shell_quote(path)}"
                    out, err, code = _exec_command(client, cmd, timeout=60)
                    if code != 0:
                        return jsonify({"success": False, "error": err or "下载失败"}), 500
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
        uploaded = request.files.get("file")
        if not uploaded:
            return jsonify({"success": False, "error": "未选择上传文件"}), 400

        session = _get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        file_mode = session.get("file_mode", "exec")
        sftp = session.get("sftp")
        client = session.get("client")

        if not remote_path:
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
            try:
                file_data = uploaded.read()
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
