"""
Flask 服务端 — 提供 REST API + SSE 实时推送
"""

import os
import sys
import json
import queue
import threading
import logging
import time as _time
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify,
    Response, stream_with_context, g,
)

from modules.common import _sse_queues, error_response
from modules.key_generator import KEY_TYPES

logger = logging.getLogger(__name__)

# --- Flask App 初始化 ---

if getattr(sys, "frozen", False):
    # PyInstaller --onefile 模式：资源在临时解压目录中
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ============ 请求日志 & 全局错误处理器 ============

@app.before_request
def _log_request_start():
    """记录请求开始时间和基本信息"""
    g.request_start = _time.time()
    g.request_path = request.path
    # 不记录 SSE 和长轮询（太频繁）
    if not request.path.startswith('/api/events') and not request.path.startswith('/api/webssh/recv'):
        logger.info(f"[REQUEST] {request.method} {request.path} — 开始")

@app.after_request
def _log_request_end(response):
    """记录请求耗时和状态"""
    if hasattr(g, 'request_start'):
        duration = round((_time.time() - g.request_start) * 1000, 2)
        # 只记录耗时超过 500ms 的请求，或错误请求
        if duration > 500 or response.status_code >= 400:
            logger.warning(f"[REQUEST] {g.request_path} — {response.status_code} （耗时 {duration}ms）")
    return response

@app.errorhandler(500)
def _handle_500(e):
    """全局 500 错误处理器：返回 JSON 而非 HTML"""
    logger.exception("[500] 未捕获的异常")
    return jsonify({
        "success": False,
        "error": "服务器内部错误，请查看日志",
        "code": "INTERNAL_ERROR"
    }), 500

# 注册 WebSSH HTTP API 路由（不再使用 SocketIO）
try:
    from modules.webssh import register_webssh_routes, cleanup_all_sessions
    register_webssh_routes(app)
    logger.info("  [WebSSH] HTTP API 路由已注册")
except Exception as e:
    logger.warning(f"  [WebSSH] 注册失败: {e}")

# ==================== 页面路由 ====================

@app.route("/")
def index():
    """主页面"""
    return render_template("index.html", key_types=KEY_TYPES)

# ==================== SSE 端点 ====================

@app.route("/api/events")
def sse_events():
    """SSE 事件流（限时连接，防止占满线程）"""
    q: queue.Queue = queue.Queue(maxsize=100)
    _sse_queues.append(q)
    _sse_start = _time.time()
    _SSE_MAX_LIFETIME = 120  # 单次连接最多 120 秒，断开后前端自动重连

    def generate():
        try:
            # 发送初始连接确认
            yield f"event: connected\ndata: {json.dumps({'message': 'SSE 已连接'})}\n\n"
            while True:
                # 超过最大生存时间，主动断开（前端 EventSource 会自动重连）
                if _time.time() - _sse_start > _SSE_MAX_LIFETIME:
                    yield f"event: reconnect\ndata: {json.dumps({'message': '请重连'})}\n\n"
                    break
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            if q in _sse_queues:
                _sse_queues.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

# ==================== 启动 ====================

def create_app():
    """
    创建 Flask 应用实例。
    返回 app，供 main.py 使用。
    （WebSSH 路由已在模块级别注册，Blueprint 在此注册）
    """
    from modules.routes.keys import keys_bp
    from modules.routes.ssh_config import ssh_config_bp
    from modules.routes.connections import connections_bp
    from modules.routes.platform import platform_bp

    app.register_blueprint(keys_bp)
    app.register_blueprint(ssh_config_bp)
    app.register_blueprint(connections_bp)
    app.register_blueprint(platform_bp)

    return app
