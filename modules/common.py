"""
modules/common.py — 共享工具和全局状态
提供 SSE 广播、统一错误响应、进度回调等基础设施。
"""

import json
import queue
from datetime import datetime
from flask import jsonify


# ==================== 统一错误响应 ====================

def error_response(message, code=None, suggestion=None, status=400):
    """
    返回统一的 JSON 错误响应。
    格式: {"success": false, "error": "...", "code": "...", "suggestion": "..."}
    """
    payload = {"success": False, "error": message}
    if code:
        payload["code"] = code
    if suggestion:
        payload["suggestion"] = suggestion
    return jsonify(payload), status


# ==================== SSE 基础设施 ====================

# SSE 消息队列 (全局，每个请求一个队列)
_sse_queues = []

# 存储最近生成的密钥（会话级）
_current_keys = {}


def _sse_broadcast(event: str, data: dict):
    """向所有已连接的 SSE 客户端广播消息"""
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    dead_queues = []
    for q in _sse_queues:
        try:
            q.put_nowait(msg)
        except queue.Full:
            dead_queues.append(q)
    for q in dead_queues:
        _sse_queues.remove(q)


def _create_progress_callback():
    """创建一个向 SSE 推送进度的回调函数"""
    def callback(message: str):
        _sse_broadcast("progress", {"message": message, "time": datetime.now().strftime("%H:%M:%S")})
    return callback
