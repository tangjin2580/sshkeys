"""
WebSSH SFTP / 文件管理 — exec 降级模式 + SFTP 辅助函数
"""

import os
import time
import logging

from modules.webssh_sessions import _ssh_sessions, _ssh_lock

logger = logging.getLogger(__name__)


def _get_session(session_id: str):
    """获取会话信息"""
    with _ssh_lock:
        session = _ssh_sessions.get(session_id)
        if session:
            session["last_active"] = time.time()
            return session
    return None


def _get_sftp(session_id: str):
    """从会话中获取 SFTP client，不存在返回 None"""
    with _ssh_lock:
        session = _ssh_sessions.get(session_id)
        if session:
            session["last_active"] = time.time()
            return session.get("sftp"), session
    return None, None


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

    # 获取规范路径
    pwd_out, _, _ = _exec_command(client, f"cd {_shell_quote(path)} && pwd -P")
    real_path = pwd_out.strip() if pwd_out.strip() else path

    items = []
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("total "):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        perms = parts[0]

        # 判断日期时间列数，确定文件名起始位置
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
