"""
modules/common.py - Shared utilities and global state
Provides SSE broadcast, unified error response, progress callbacks, etc.
"""

import json
import queue
import threading
from datetime import datetime
from typing import Any, Callable, Optional, List
from flask import jsonify, Response


# ==================== Unified Error Response ====================

def error_response(
    message: str,
    code: Optional[str] = None,
    suggestion: Optional[str] = None,
    status: int = 400
) -> tuple[Response, int]:
    """
    Return unified JSON error response.
    Format: {"success": false, "error": "...", "code": "...", "suggestion": "..."}
    """
    payload: dict[str, Any] = {"success": False, "error": message}
    if code:
        payload["code"] = code
    if suggestion:
        payload["suggestion"] = suggestion
    return jsonify(payload), status


# ==================== SSE Infrastructure ====================

# SSE message queues (global, one queue per request)
_sse_queues: List[queue.Queue] = []
_sse_lock = threading.Lock()

# Store recently generated keys (session-level)
_current_keys: dict[str, Any] = {}


def _sse_broadcast(event: str, data: dict[str, Any]) -> None:
    """Broadcast message to all connected SSE clients"""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_lock:
        queues = list(_sse_queues)
    dead_queues: List[queue.Queue] = []
    for q in queues:
        try:
            q.put_nowait(msg)
        except queue.Full:
            dead_queues.append(q)
    if dead_queues:
        with _sse_lock:
            for q in dead_queues:
                if q in _sse_queues:
                    _sse_queues.remove(q)


def _create_progress_callback() -> Callable[[str], None]:
    """Create a callback function that pushes progress to SSE"""
    def callback(message: str) -> None:
        _sse_broadcast("progress", {"message": message, "time": datetime.now().strftime("%H:%M:%S")})
    return callback


def _sse_cleanup_stale() -> int:
    """
    Manually clean up dead/stale SSE queues (full queue is considered stale).
    Returns the number of queues cleaned up.
    """
    removed = 0
    stale: List[queue.Queue] = []
    with _sse_lock:
        for q in list(_sse_queues):
            if q.full():
                stale.append(q)
        for q in stale:
            try:
                _sse_queues.remove(q)
                removed += 1
            except ValueError:
                pass
    return removed


def get_sse_queue_count() -> int:
    """Return current SSE queue count (thread-safe)."""
    with _sse_lock:
        return len(_sse_queues)
