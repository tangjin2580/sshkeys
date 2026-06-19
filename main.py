"""
SSH Key Manager — 主入口
启动 Flask Web 服务，自动打开浏览器

用法:
    python main.py          # 生产模式（Waitress WSGI 服务器）
    python main.py --dev    # 开发模式（Flask 内置服务器，代码热重载）
"""

import os
import sys
import signal
import socket
import time
import webbrowser
import threading
import logging
import argparse
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from modules.server import create_app, _sse_queues

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ==================== 配置 ====================
HOST = "127.0.0.1"
PORT = 5201
APP_URL = f"http://{HOST}:{PORT}"

# ==================== 全局状态 ====================
_shutting_down = False

# ==================== 优雅关闭 ====================

def _shutdown(signum, frame):
    global _shutting_down
    _shutting_down = True
    logger.info("收到终止信号，正在关闭服务...")
    for q in _sse_queues:
        try:
            q.put_nowait(None)
        except Exception:
            pass
    _sse_queues.clear()
    # 关闭所有 WebSSH 会话
    try:
        from modules.webssh import _ssh_sessions, _ssh_lock, _close_ssh_session
        with _ssh_lock:
            sids = list(_ssh_sessions.keys())
        for sid in sids:
            try:
                _close_ssh_session(sid, emit=False)
            except Exception:
                pass
        logger.info(f"已关闭 {len(sids)} 个 WebSSH 会话")
    except Exception:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ==================== 浏览器 ====================

def _open_browser():
    """延迟打开浏览器"""
    webbrowser.open(APP_URL)

# ==================== 端口就绪检测 ====================

def _wait_for_port(timeout: float = 15.0) -> bool:
    """等待服务端口就绪"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((HOST, PORT), timeout=0.5)
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False

# ==================== 生产模式：Waitress + 自动重启 ====================

_MAX_RESTARTS = 5          # 最大连续重启次数
_MAX_BACKOFF = 30          # 最大退避秒数

def _serve_with_restart(app_obj):
    """
    使用 Waitress 提供服务，异常崩溃后自动重启。
    Ctrl+C / SIGTERM 正常退出，不触发重启。
    """
    from waitress import serve
    restart_count = 0
    start_time = time.time()

    while not _shutting_down:
        try:
            logger.info("  WSGI 服务器: Waitress（生产模式）")
            serve(app_obj, host=HOST, port=PORT, threads=8,
                  channel_timeout=120, cleanup_interval=30)
            break
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            if _shutting_down:
                break
            restart_count += 1
            if time.time() - start_time > 300:
                restart_count = 1
            if restart_count > _MAX_RESTARTS:
                logger.error(f"连续重启 {_MAX_RESTARTS} 次仍失败，放弃重启")
                break
            delay = min(2 ** restart_count, _MAX_BACKOFF)
            logger.error(f"服务器异常退出: {e}")
            logger.info(f"{delay} 秒后重启（第 {restart_count}/{_MAX_RESTARTS} 次）...")
            time.sleep(delay)
            start_time = time.time()

# ==================== 主入口 ====================

def main():
    parser = argparse.ArgumentParser(description="SSH Key Manager — 可视化 SSH 密钥管理工具 + WebSSH 终端")
    parser.add_argument("--dev", action="store_true",
                        help="开发模式：启用代码热重载")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("  SSH Key Manager — 可视化 SSH 密钥管理 + WebSSH")
    logger.info("=" * 50)
    logger.info(f"  服务地址: {APP_URL}")
    logger.info(f"  运行模式: {'开发（热重载）' if args.dev else '生产（Waitress）'}")
    logger.info(f"  按 Ctrl+C 停止服务")
    logger.info("=" * 50)

    app = create_app()

    if args.dev:
        logger.info("  服务器: Flask 开发服务器（热重载已启用）")
        threading.Timer(1.5, _open_browser).start()
        # 开发模式：Flask 内置服务器，支持热重载
        app.run(
            host=HOST,
            port=PORT,
            debug=True,
            use_reloader=True,
        )
    else:
        # 生产模式：Waitress 跑 HTTP
        logger.info("  注意: WebSSH 使用 HTTP 长轮询模式（Waitress 兼容）")
        server_thread = threading.Thread(
            target=_serve_with_restart,
            args=(app,),
            daemon=True
        )
        server_thread.start()

        if _wait_for_port():
            _open_browser()
        else:
            logger.warning("服务启动超时，请手动打开浏览器访问: " + APP_URL)

        try:
            while server_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
