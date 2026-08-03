"""
WebSSH SFTP / File Management - exec fallback mode + SFTP helper functions
"""

import os
import time
import logging
from typing import Any, Optional, Tuple, List, Dict

from modules.webssh_sessions import _ssh_sessions, _ssh_lock

logger = logging.getLogger(__name__)


def _get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get session info"""
    with _ssh_lock:
        session = _ssh_sessions.get(session_id)
        if session:
            session["last_active"] = time.time()
            return session
    return None


def _get_sftp(session_id: str) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Get SFTP client from session, returns None if not available"""
    with _ssh_lock:
        session = _ssh_sessions.get(session_id)
        if session:
            session["last_active"] = time.time()
            return session.get("sftp"), session
    return None, None


def _exec_command(client: Any, cmd: str, timeout: int = 15) -> Tuple[str, str, int]:
    """Execute command on SSH connection (new channel, doesn't affect interactive shell)"""
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return out, err, code
    except Exception as e:
        return "", str(e), -1


def _shell_quote(s: str) -> str:
    """Safely quote path with single quotes to prevent shell injection"""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _exec_ls(client: Any, path: str) -> Tuple[str, List[Dict[str, Any]]]:
    """exec mode list directory: parse ls -la output (compatible with multiple ls formats)"""
    if not path or path == "~":
        out, err, code = _exec_command(client, "echo $HOME")
        if code == 0:
            path = out.strip()
        else:
            path = "/"

    # Prefer --time-style=long-iso (GNU/Linux), fall back to BSD -D format
    for fmt_flag in ["--time-style=long-iso", "-D '%Y-%m-%d %H:%M'"]:
        cmd = f"ls -la {fmt_flag} {_shell_quote(path)}"
        out, err, code = _exec_command(client, cmd)
        if code == 0:
            break
    else:
        cmd = f"ls -la {_shell_quote(path)}"
        out, err, code = _exec_command(client, cmd)
        if code != 0:
            return path, []

    # Get canonical path
    pwd_out, _, _ = _exec_command(client, f"cd {_shell_quote(path)} && pwd -P")
    real_path = pwd_out.strip() if pwd_out.strip() else path

    items: List[Dict[str, Any]] = []
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("total "):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        perms = parts[0]

        # Determine filename start position based on date/time columns
        if '-' in parts[5]:
            name = ' '.join(parts[7:])
        else:
            name = ' '.join(parts[8:]) if len(parts) >= 9 else ''

        if not name or name == "." or name == "..":
            continue
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
