"""
WebSSH Compatibility Layer - Retained for backward compatibility with old imports
Actual code has been split into:
  - webssh_sessions.py  (session management, SSH client)
  - webssh_sftp.py      (SFTP / exec file operations)
  - webssh_routes.py    (HTTP route registration)
  - routes/webssh.py    (Blueprint-based routes)
"""

# Session management (main.py / server.py import from here)
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

# SFTP helpers
from modules.webssh_sftp import (
    _get_session,
    _get_sftp,
    _exec_command,
    _shell_quote,
    _exec_ls,
)

# Route registration (server.py imports from here)
from modules.webssh_routes import register_webssh_routes
