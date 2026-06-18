"""
SSH Key Manager — 主入口
启动 Flask Web 服务，自动打开浏览器
"""

import os
import sys
import signal
import socket
import webbrowser
import threading
import logging
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

# ==================== 优雅关闭 ====================
_running = True

def _shutdown(signum, frame):
    global _running
    logger.info("收到终止信号，正在关闭服务...")
    _running = False
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

# ==================== 启动 ====================
def open_browser():
    """延迟打开浏览器"""
    webbrowser.open(APP_URL)

def main():
    logger.info("=" * 50)
    logger.info("  SSH Key Manager — 可视化 SSH 密钥管理工具")
    logger.info("=" * 50)
    logger.info(f"  服务地址: {APP_URL}")
    logger.info(f"  按 Ctrl+C 停止服务")
    logger.info("=" * 50)

    # 1 秒后自动打开浏览器
    threading.Timer(1.0, open_browser).start()

    app = create_app()
    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        use_reloader=False,
    )

if __name__ == "__main__":
    main()
