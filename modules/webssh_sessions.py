"""
WebSSH Session Management - SSH client creation, session lifecycle, output buffering
"""

import os
import threading
import time
import queue
import select
import logging
from typing import Any, Optional
import paramiko
from pathlib import Path

logger = logging.getLogger(__name__)

# Each browser session has one SSH connection, indexed by session_id
_ssh_sessions: dict[str, dict[str, Any]] = {}
_ssh_lock = threading.Lock()
_sessions_next_id = 0

# Output buffers: one Queue per session
_output_buffers: dict[str, queue.Queue] = {}

# ============ Session Management Config ============
MAX_WEBSSH_SESSIONS = 5  # Max concurrent sessions
SESSION_TIMEOUT = 600  # Session timeout (seconds), auto-cleanup after 10 min inactivity
_CLEANUP_INTERVAL = 60  # Cleanup thread run interval (seconds)


def get_ssh_dir() -> Path:
    """Return ~/.ssh directory"""
    return Path.home() / ".ssh"


def _resolve_identity_file(id_file: str) -> Optional[str]:
    """Resolve IdentityFile path, supports ~ and relative paths"""
    if not id_file:
        return None
    p = Path(id_file).expanduser().resolve()
    if p.exists():
        return str(p)
    alt = get_ssh_dir() / Path(id_file).name
    if alt.exists():
        return str(alt)
    return None


def _create_ssh_client(
    hostname: str,
    port: int,
    username: str,
    password: Optional[str] = None,
    identity_file: Optional[str] = None
) -> paramiko.SSHClient:
    """Create and connect SSH client"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict[str, Any] = {
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
            logger.info(f"Using key file: {resolved}")
            # Try loading keys in order of common types
            key_classes = [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]
            for key_cls in key_classes:
                try:
                    pkey = key_cls.from_private_key_file(resolved)
                    connect_kwargs["pkey"] = pkey
                    break
                except Exception:
                    continue
            else:
                logger.warning(f"Cannot load key {resolved}: unsupported key type")
            connect_kwargs["allow_agent"] = True
            connect_kwargs["look_for_keys"] = True

    if password:
        connect_kwargs["password"] = password
        connect_kwargs["allow_agent"] = True
        connect_kwargs["look_for_keys"] = True

    client.connect(**connect_kwargs)
    # Set keepalive, send heartbeat every 30 seconds to prevent NAT timeout
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(30)
    return client


def _put_output(q: queue.Queue, data: str) -> None:
    """Safely write data to output queue. Drop oldest data if queue is full."""
    try:
        q.put_nowait(data)
    except queue.Full:
        try:
            q.get_nowait()  # Discard oldest data to make room
            q.put_nowait(data)
        except queue.Empty:
            pass


def _close_ssh_session(session_id: str) -> None:
    """Close SSH connection for specified session"""
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
        logger.info(f"[WebSSH] Session {session_id} closed ({session.get('username')}@{session.get('hostname')})")
    except Exception as e:
        logger.warning(f"[WebSSH] Error closing session {session_id}: {e}")


def cleanup_all_sessions() -> None:
    """Close all SSH sessions (called on service shutdown)"""
    with _ssh_lock:
        sids = list(_ssh_sessions.keys())
    for sid in sids:
        _close_ssh_session(sid)
    logger.info(f"[WebSSH] Closed {len(sids)} sessions")


def _cleanup_stale_sessions() -> None:
    """
    Periodically clean up timed out or abnormal sessions.
    Runs in background thread, executes every _CLEANUP_INTERVAL seconds.
    """
    while True:
        time.sleep(_CLEANUP_INTERVAL)
        now = time.time()
        to_close: list[str] = []
        with _ssh_lock:
            for sid, info in list(_ssh_sessions.items()):
                last_active = info.get("last_active", info.get("connected_at", now))
                if now - last_active > SESSION_TIMEOUT:
                    to_close.append(sid)
                    logger.info(f"[WebSSH] Session {sid} timed out ({int(now - last_active)}s inactive), auto-closing")
                # Check if channel is closed
                channel = info.get("channel")
                if channel and channel.closed:
                    to_close.append(sid)
                    logger.info(f"[WebSSH] Session {sid} channel closed, cleaning up")

        for sid in to_close:
            try:
                _close_ssh_session(sid)
            except Exception as e:
                logger.warning(f"[WebSSH] Error cleaning up session {sid}: {e}")

        if to_close:
            logger.info(f"[WebSSH] Cleaned up {len(to_close)} timed-out/closed sessions, active: {len(_ssh_sessions)}")


def _start_cleanup_thread() -> None:
    """Start session cleanup background thread (daemon thread)"""
    t = threading.Thread(target=_cleanup_stale_sessions, daemon=True, name="WebSSH-Cleanup")
    t.start()
    logger.info(f"[WebSSH] Session cleanup thread started (interval {_CLEANUP_INTERVAL}s, timeout {SESSION_TIMEOUT}s)")
