"""
Flask Server — REST API + SSE Real-time Push
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
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from modules.common import _sse_queues, _sse_lock, error_response
from modules.key_generator import KEY_TYPES

logger = logging.getLogger(__name__)

# --- Flask App Initialization ---

if getattr(sys, "frozen", False):
    # PyInstaller --onefile mode: resources in temp extraction directory
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.config['TEMPLATES_AUTO_RELOAD'] = True

# --- Rate Limiter Configuration ---
# In-memory limiter using client IP as key
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per minute"],
    storage_uri="memory://",
)


def get_real_remote_address() -> str:
    """Get real client IP, considering proxy headers"""
    # Check X-Forwarded-For header (for reverse proxy setups)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take the first IP (original client)
        return forwarded.split(",")[0].strip()
    return get_remote_address()

# ============ Request Logging & Global Error Handlers ============

@app.before_request
def _log_request_start():
    """Log request start time and basic info"""
    g.request_start = _time.time()
    g.request_path = request.path
    # Skip SSE and long-polling endpoints (too frequent)
    if not request.path.startswith('/api/events') and not request.path.startswith('/api/webssh/recv'):
        logger.info(f"[REQUEST] {request.method} {request.path} - started")

@app.after_request
def _log_request_end(response):
    """Log request duration and status"""
    if hasattr(g, 'request_start'):
        duration = round((_time.time() - g.request_start) * 1000, 2)
        # Only log slow requests (>500ms) or errors
        if duration > 500 or response.status_code >= 400:
            logger.warning(f"[REQUEST] {g.request_path} - {response.status_code} (took {duration}ms)")
    return response

@app.errorhandler(500)
def _handle_500(e):
    """Global 500 error handler: return JSON instead of HTML"""
    logger.exception("[500] Unhandled exception")
    return jsonify({
        "success": False,
        "error": "Internal server error, please check logs",
        "code": "INTERNAL_ERROR"
    }), 500

@app.errorhandler(429)
def _handle_429(e):
    """Rate limit exceeded handler"""
    return jsonify({
        "success": False,
        "error": "Rate limit exceeded. Please slow down your requests.",
        "code": "RATE_LIMIT_EXCEEDED"
    }), 429

# Note: Exempt SSE and WebSSH polling from rate limiting
# (Applied directly to route decorators below)

# Register WebSSH HTTP API routes (using standard HTTP instead of SocketIO)
try:
    from modules.webssh import register_webssh_routes, cleanup_all_sessions
    register_webssh_routes(app)
    logger.info("  [WebSSH] HTTP API routes registered")
except Exception as e:
    logger.warning(f"  [WebSSH] Registration failed: {e}")

# ==================== Page Routes ====================

@app.route("/")
def index():
    """Main page"""
    return render_template("index.html", key_types=KEY_TYPES)

# ==================== SSE Endpoint ====================
# Note: SSE connections are exempt from rate limiting
# because EventSource maintains long-lived connections

@app.route("/api/events")
def sse_events():
    """SSE event stream with time-limited connections to prevent thread exhaustion"""
    q: queue.Queue = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_queues.append(q)
    _sse_start = _time.time()
    _SSE_MAX_LIFETIME = 120  # Max 120 seconds per connection, frontend will auto-reconnect

    def generate():
        try:
            # Send initial connection confirmation
            yield f"event: connected\ndata: {json.dumps({'message': 'SSE connected'})}\n\n"
            while True:
                # Exit if max lifetime exceeded (frontend EventSource will auto-reconnect)
                if _time.time() - _sse_start > _SSE_MAX_LIFETIME:
                    yield f"event: reconnect\ndata: {json.dumps({'message': 'Please reconnect'})}\n\n"
                    break
                try:
                    msg = q.get(timeout=5)
                    if msg is None:
                        # Received close sentinel, exit gracefully
                        break
                    yield msg
                except queue.Empty:
                    # Send heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
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

# ==================== SSE Admin API ====================

@app.route("/api/admin/sse-cleanup", methods=["POST"])
@limiter.limit("10 per minute")
def sse_cleanup():
    """Manually clean up stale SSE queues (full queues are considered stale)"""
    from modules.common import _sse_cleanup_stale, get_sse_queue_count
    removed = _sse_cleanup_stale()
    return jsonify({
        "success": True,
        "removed": removed,
        "remaining": get_sse_queue_count(),
    })


@app.route("/api/admin/sse-status", methods=["GET"])
@limiter.limit("30 per minute")
def sse_status():
    """Return current SSE queue status"""
    from modules.common import get_sse_queue_count
    return jsonify({
        "success": True,
        "queue_count": get_sse_queue_count(),
    })

# ==================== App Factory ====================

def create_app():
    """
    Create Flask application instance.
    Returns app for main.py to use.
    (WebSSH routes registered at module level, Blueprints registered here)
    """
    from modules.routes.keys import keys_bp
    from modules.routes.ssh_config import ssh_config_bp
    from modules.routes.connections import connections_bp
    from modules.routes.platform import platform_bp
    from modules.routes.filesync import filesync_bp

    app.register_blueprint(keys_bp)
    app.register_blueprint(ssh_config_bp)
    app.register_blueprint(connections_bp)
    app.register_blueprint(platform_bp)
    app.register_blueprint(filesync_bp)

    return app
