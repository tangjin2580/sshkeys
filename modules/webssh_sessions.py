"""
WebSSH 会话管理 — SSH 客户端创建、会话生命周期、输出缓冲
"""

import os
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
            # 按常见类型顺序尝试加载密钥
            key_classes = [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]
            for key_cls in key_classes:
                try:
                    pkey = key_cls.from_private_key_file(resolved)
                    connect_kwargs["pkey"] = pkey
                    break
                except Exception:
                    continue
            else:
                logger.warning(f"无法加载密钥 {resolved}: 不支持的密钥类型")
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
