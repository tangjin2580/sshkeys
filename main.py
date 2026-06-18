"""
SSH Key Manager — 主入口
启动 Flask Web 服务，自动打开浏览器

用法:
    python main.py          # 生产模式（Waitress WSGI 服务器，崩溃自动重启）
    python main.py --dev    # 开发模式（Flask 内置服务器，代码/模板热重载）
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

# ==================== 端口重用 Monkey Patch ====================
_original_socket = socket.socket

def _patched_socket(family=-1, type=-1, proto=-1, fileno=None):
    sock = _original_socket(family, type, proto, fileno)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock

socket.socket = _patched_socket

# ==================== 配置 ====================
HOST = "127.0.0.1"
PORT = 5200
APP_URL = f"http://{HOST}:{PORT}"

# ==================== 全局状态 ====================
_shutting_down = False

# ==================== 优雅关闭 ====================

def _shutdown(signum, frame):
    global _shutting_down
    _shutting_down = True
    logger.info("收到终止信号，正在关闭服务...")
    # 清空 SSE 队列
    for q in _sse_queues:
        try:
            q.put_nowait(None)
        except Exception:
            pass
    _sse_queues.clear()
    sys.exit(0)

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ==================== 浏览器 ====================

def _open_browser():
    """延迟打开浏览器"""
    webbrowser.open(APP_URL)

# ==================== 生产模式：Waitress + 自动重启 ====================

_MAX_RESTARTS = 5          # 最大连续重启次数
_MAX_BACKOFF = 30          # 最大退避秒数

def _serve_with_restart(app):
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
            serve(app, host=HOST, port=PORT, threads=8,
                  channel_timeout=120, cleanup_interval=30)
            # serve() 正常返回 = 主动关闭
            break
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            if _shutting_down:
                break

            restart_count += 1
            # 如果上次运行超过 5 分钟，重置计数
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

# ==================== 开发模式：Flask + 热重载 ====================

def _serve_dev(app):
    """Flask 内置开发服务器，代码/模板改动自动重载"""
    logger.info("  服务器: Flask 开发服务器（热重载已启用）")
    app.run(
        host=HOST,
        port=PORT,
        debug=True,
        use_reloader=True,
        threaded=True,
    )

# ==================== 端口就绪检测 ====================

def _wait_for_port(timeout: float = 15.0) -> bool:
    """等待服务端口就绪（CDN 检测 + 服务器启动期间轮询）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((HOST, PORT), timeout=0.5)
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


# ==================== 主入口 ====================

def main():
    parser = argparse.ArgumentParser(description="SSH Key Manager — 可视化 SSH 密钥管理工具")
    parser.add_argument("--dev", action="store_true",
                        help="开发模式：启用代码和模板热重载")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("  SSH Key Manager — 可视化 SSH 密钥管理工具")
    logger.info("=" * 50)
    logger.info(f"  服务地址: {APP_URL}")
    logger.info(f"  运行模式: {'开发（热重载）' if args.dev else '生产（Waitress）'}")
    logger.info(f"  按 Ctrl+C 停止服务")
    logger.info("=" * 50)

    app = create_app()

    if args.dev:
        # 开发模式：Flask 启动快，简单延迟打开浏览器
        # create_app() 返回的是 WSGI 中间件，需要提取内部的 Flask app
        flask_app = app._app if hasattr(app, '_app') else app
        threading.Timer(1.5, _open_browser).start()
        _serve_dev(flask_app)
    else:
        # 生产模式：后台线程启动 Waitress，等端口就绪后再打开浏览器
        server_thread = threading.Thread(
            target=_serve_with_restart, args=(app,), daemon=True
        )
        server_thread.start()

        if _wait_for_port():
            _open_browser()
        else:
            logger.warning("服务启动超时，请手动打开浏览器访问: " + APP_URL)

        # 主线程等待服务线程结束（Ctrl+C 退出）
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
