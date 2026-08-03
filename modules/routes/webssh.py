"""
WebSSH Routes - SSH Terminal + SFTP File Management API
"""

import os
import time
import queue
import select
import logging
import threading
from typing import Optional

from flask import Blueprint, request, jsonify, Response, stream_with_context

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
from modules.config import get_sftp_max_download_bytes
from modules.server import limiter

logger = logging.getLogger(__name__)

# Create Blueprint
webssh_bp = Blueprint("webssh", __name__, url_prefix="/api/webssh")


def _get_sftp_max_bytes() -> int:
    """Runtime read of SFTP download limit (supports hot updates)."""
    return get_sftp_max_download_bytes()


# ============================================================
# SSH Terminal API
# ============================================================

@webssh_bp.route("/connect", methods=["POST"])
@limiter.limit("20 per minute")  # Limit connection attempts
def webssh_connect():
    """Establish SSH connection"""
    import modules.webssh_sessions as _ws
    data = request.get_json() or {}

    hostname = data.get("hostname", "").strip()
    port = int(data.get("port", 22))
    username = data.get("username", "").strip()
    password = data.get("password", "") or None
    identity_file = data.get("identity_file", "") or None
    alias = data.get("alias", "").strip()

    if not hostname or not username:
        return jsonify({"success": False, "error": "Missing server address or username"}), 400

    # Check max session count
    with _ssh_lock:
        active_count = len(_ssh_sessions)
    if active_count >= MAX_WEBSSH_SESSIONS:
        return jsonify({
            "success": False,
            "error": f"Maximum sessions ({MAX_WEBSSH_SESSIONS}) reached. Please close other sessions first."
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

        # Open SFTP channel for remote file management
        sftp = None
        file_mode = "exec"
        sftp_error = ""
        try:
            sftp = client.open_sftp()
            file_mode = "sftp"
        except Exception as e:
            sftp_error = str(e)
            logger.warning(f"[WebSSH] SFTP unavailable, falling back to exec mode: {e}")

        output_q: queue.Queue = queue.Queue(maxsize=1000)

        with _ssh_lock:
            session_id = str(_ws._sessions_next_id)
            _ws._sessions_next_id += 1
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

        # Start read thread
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
                        recv_data = channel.recv(65536).decode("utf-8", errors="replace")
                        _put_output(output_q, recv_data)
                    if channel.recv_stderr_ready():
                        recv_data = channel.recv_stderr(65536).decode("utf-8", errors="replace")
                        _put_output(output_q, recv_data)
                while channel.recv_ready():
                    recv_data = channel.recv(65536).decode("utf-8", errors="replace")
                    _put_output(output_q, recv_data)
                output_q.put_nowait(None)
            except Exception as e:
                logger.warning(f"[WebSSH] Read thread error: {e}")
                try:
                    output_q.put_nowait(None)
                except Exception:
                    pass

        t = threading.Thread(target=_read_loop, daemon=True)
        t.start()

        logger.info(f"[WebSSH] Connected {username}@{hostname} (session={session_id}, file_mode={file_mode})")
        return jsonify({
            "success": True,
            "session_id": session_id,
            "message": f"Connected to {username}@{hostname}",
            "file_mode": file_mode,
        })

    except Exception as e:
        logger.exception(f"[WebSSH] Connection failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@webssh_bp.route("/send", methods=["POST"])
def webssh_send():
    """Send input to SSH"""
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    input_data = data.get("data", "")

    with _ssh_lock:
        session = _ssh_sessions.get(session_id)
        if session:
            session["last_active"] = time.time()
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404

    try:
        session["channel"].send(input_data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@webssh_bp.route("/recv", methods=["GET"])
def webssh_recv():
    """Long-polling for SSH output (exempt from rate limiting)"""
    session_id = request.args.get("session_id", "")
    timeout = min(float(request.args.get("timeout", "5")), 10)

    with _ssh_lock:
        output_q = _output_buffers.get(session_id)
        session = _ssh_sessions.get(session_id)
        if session:
            session["last_active"] = time.time()
    if not output_q:
        return jsonify({"success": False, "error": "Session not found"}), 404

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


@webssh_bp.route("/resize", methods=["POST"])
def webssh_resize():
    """Resize terminal"""
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    cols = int(data.get("cols", 120))
    rows = int(data.get("rows", 30))

    with _ssh_lock:
        session = _ssh_sessions.get(session_id)
        if session:
            session["last_active"] = time.time()
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404

    try:
        session["channel"].resize_pty(width=cols, height=rows)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@webssh_bp.route("/close", methods=["POST"])
def webssh_close():
    """Close SSH session"""
    data = request.get_json() or {}
    session_id = data.get("session_id", "")

    with _ssh_lock:
        exists = session_id in _ssh_sessions
    if not exists:
        return jsonify({"success": False, "error": "Session not found"}), 404

    _close_ssh_session(session_id)
    return jsonify({"success": True, "message": "Session closed"})


# ============================================================
# SFTP File Management API
# ============================================================

@webssh_bp.route("/sftp/list", methods=["GET"])
def sftp_list():
    """List directory contents"""
    session_id = request.args.get("session_id", "")
    path = request.args.get("path", "").strip()
    session = _get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404

    file_mode = session.get("file_mode", "exec")
    sftp = session.get("sftp")
    client = session.get("client")

    if file_mode == "sftp" and sftp:
        try:
            items = []
            for entry in sftp.listdir_attr(path or "."):
                is_link = entry.longname.startswith("l")
                items.append({
                    "name": entry.filename,
                    "size": entry.st_size or 0,
                    "is_dir": entry.longname.startswith("d"),
                    "is_link": is_link,
                    "mtime": entry.st_mtime or 0,
                    "permissions": entry.longname[:10],
                })
            items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            real_path = sftp.normalize(path or ".")
            return jsonify({"success": True, "path": real_path, "items": items})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        real_path, items = _exec_ls(client, path)
        return jsonify({"success": True, "path": real_path, "items": items})


@webssh_bp.route("/sftp/download", methods=["GET"])
def sftp_download():
    """Download remote file"""
    session_id = request.args.get("session_id", "")
    path = request.args.get("path", "").strip()
    session = _get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404
    if not path:
        return jsonify({"success": False, "error": "Missing path"}), 400

    file_mode = session.get("file_mode", "exec")
    sftp = session.get("sftp")
    client = session.get("client")

    max_bytes = _get_sftp_max_bytes()

    if file_mode == "sftp" and sftp:
        try:
            st = sftp.stat(path)
            if st.st_size and st.st_size > max_bytes:
                return jsonify({"success": False, "error": f"File exceeds maximum size ({max_bytes // (1024*1024)} MB)"}), 413
            sftp_stat = sftp.stat(path)
            file_size = sftp_stat.st_size if sftp_stat else 0
            if file_size > max_bytes:
                return jsonify({"success": False, "error": f"File size exceeds limit"}), 413
            file_obj = sftp.file(path, "rb")
            return Response(
                file_obj,
                mimetype="application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename={os.path.basename(path)}",
                    "Content-Length": str(file_size) if file_size else None,
                },
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        cmd = f"cat {_shell_quote(path)}"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
        data = stdout.read()
        if len(data) > max_bytes:
            return jsonify({"success": False, "error": "File too large to download"}), 413
        code = stdout.channel.recv_exit_status()
        if code != 0:
            err = stderr.read().decode("utf-8", errors="replace")
            return jsonify({"success": False, "error": err or "Download failed"}), 500
        return Response(
            data,
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename={os.path.basename(path)}",
            },
        )


@webssh_bp.route("/sftp/upload", methods=["POST"])
def sftp_upload():
    """Upload file to remote server"""
    session_id = request.form.get("session_id", "")
    dest = request.form.get("path", "").strip()
    uploaded = request.files.get("file")

    session = _get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404
    if not dest or not uploaded:
        return jsonify({"success": False, "error": "Missing path or file"}), 400

    file_mode = session.get("file_mode", "exec")
    sftp = session.get("sftp")
    client = session.get("client")

    if file_mode == "sftp" and sftp:
        try:
            with sftp.file(dest, "wb") as f:
                while True:
                    chunk = uploaded.stream.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            return jsonify({"success": True, "message": f"Uploaded {uploaded.filename}"})
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
                return jsonify({"success": False, "error": err or "Upload failed"}), 500
            return jsonify({"success": True, "message": f"Uploaded {uploaded.filename}"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500


@webssh_bp.route("/sftp/delete", methods=["POST"])
def sftp_delete():
    """Delete remote file or directory"""
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    path = data.get("path", "").strip()
    session = _get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404
    if not path:
        return jsonify({"success": False, "error": "Missing path"}), 400

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
            return jsonify({"success": True, "message": f"Deleted {os.path.basename(path)}"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        out, err, code = _exec_command(client, f"rm -rf {_shell_quote(path)}")
        if code != 0:
            return jsonify({"success": False, "error": err or "Delete failed"}), 500
        return jsonify({"success": True, "message": f"Deleted {os.path.basename(path)}"})


@webssh_bp.route("/sftp/mkdir", methods=["POST"])
def sftp_mkdir():
    """Create remote directory"""
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    path = data.get("path", "").strip()
    session = _get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404
    if not path:
        return jsonify({"success": False, "error": "Missing path"}), 400

    file_mode = session.get("file_mode", "exec")
    sftp = session.get("sftp")
    client = session.get("client")

    if file_mode == "sftp" and sftp:
        try:
            sftp.mkdir(path)
            return jsonify({"success": True, "message": f"Created directory {os.path.basename(path)}"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        out, err, code = _exec_command(client, f"mkdir -p {_shell_quote(path)}")
        if code != 0:
            return jsonify({"success": False, "error": err or "Create failed"}), 500
        return jsonify({"success": True, "message": f"Created directory {os.path.basename(path)}"})


@webssh_bp.route("/sftp/rename", methods=["POST"])
def sftp_rename():
    """Rename remote file or directory"""
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    old_path = data.get("old_path", "").strip()
    new_path = data.get("new_path", "").strip()
    session = _get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404
    if not old_path or not new_path:
        return jsonify({"success": False, "error": "Missing path"}), 400

    file_mode = session.get("file_mode", "exec")
    sftp = session.get("sftp")
    client = session.get("client")

    if file_mode == "sftp" and sftp:
        try:
            sftp.rename(old_path, new_path)
            return jsonify({"success": True, "message": "Renamed successfully"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        out, err, code = _exec_command(client, f"mv {_shell_quote(old_path)} {_shell_quote(new_path)}")
        if code != 0:
            return jsonify({"success": False, "error": err or "Rename failed"}), 500
        return jsonify({"success": True, "message": "Renamed successfully"})


@webssh_bp.route("/sftp/stat", methods=["GET"])
def sftp_stat():
    """Get remote file/directory info"""
    session_id = request.args.get("session_id", "")
    path = request.args.get("path", "").strip()
    session = _get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404
    if not path:
        return jsonify({"success": False, "error": "Missing path"}), 400

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
                    "permissions": "??????????",
                    "mtime": 0,
                    "path": path,
                }
            })
        return jsonify({"success": False, "error": "Cannot get file info"}), 500


# Start cleanup thread on module load
_start_cleanup_thread()
logger.info("[WebSSH] Blueprint routes registered (with SFTP file management)")
