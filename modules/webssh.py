"""
WebSSH 兼容层 — 拆分后保留此文件以兼容旧 import
实际代码已拆分为:
  - webssh_sessions.py  (会话管理、SSH 客户端)
  - webssh_sftp.py      (SFTP / exec 文件操作)
  - webssh_routes.py    (HTTP 路由注册)
"""

# 会话管理（main.py / server.py 从这里 import）
from modules.webssh_sessions import (
    _ssh_sessions,
    _ssh_lock,
    _sessions_next_id,
    _output_buffers,
    MAX_WEBSSH_SESSIONS,
    SESSION_TIMEOUT,
    get_ssh_dir,
    _create_ssh_client,
    _put_output,
    _close_ssh_session,
    cleanup_all_sessions,
    _start_cleanup_thread,
)

# SFTP 辅助
from modules.webssh_sftp import (
    _get_session,
    _get_sftp,
    _exec_command,
    _shell_quote,
    _exec_ls,
)

# 路由注册（server.py 从这里 import）
from modules.webssh_routes import register_webssh_routes
