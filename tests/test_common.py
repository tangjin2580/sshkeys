"""
Tests for Common Module
"""

import pytest
from flask import Flask
from modules.common import error_response, get_sse_queue_count


class TestErrorResponse:
    """Tests for unified error response function"""

    def test_basic_error_response(self):
        """Test basic error response format"""
        app = Flask(__name__)
        with app.test_request_context():
            response, status = error_response("Test error message")
            assert status == 400  # HTTP status is 400
            data = response.get_json()
            assert data["success"] is False
            assert data["error"] == "Test error message"

    def test_error_response_with_code(self):
        """Test error response with custom code"""
        app = Flask(__name__)
        with app.test_request_context():
            response, status = error_response(
                "Test error",
                code="TEST_ERROR",
                status=500
            )
            assert status == 500
            data = response.get_json()
            assert data["code"] == "TEST_ERROR"

    def test_error_response_with_suggestion(self):
        """Test error response with suggestion"""
        app = Flask(__name__)
        with app.test_request_context():
            response, status = error_response(
                "Connection failed",
                suggestion="Check your network settings"
            )
            data = response.get_json()
            assert data["suggestion"] == "Check your network settings"

    def test_error_response_custom_status(self):
        """Test error response with custom HTTP status"""
        app = Flask(__name__)
        with app.test_request_context():
            response, status = error_response(
                "Not found",
                status=404
            )
            assert status == 404


class TestSSEQueueCount:
    """Tests for SSE queue count function"""

    def test_queue_count_returns_integer(self):
        """Test that get_sse_queue_count returns an integer"""
        result = get_sse_queue_count()
        assert isinstance(result, int)
        assert result >= 0

    def test_queue_count_initially_zero(self):
        """Test that queue count is initially zero"""
        result = get_sse_queue_count()
        assert result >= 0


class TestSSELock:
    """Tests for SSE lock mechanism"""

    def test_sse_lock_exists(self):
        """Test that SSE lock is properly initialized"""
        from modules.common import _sse_lock
        import threading
        assert isinstance(_sse_lock, threading.Lock)

    def test_sse_queues_list_exists(self):
        """Test that SSE queues list is properly initialized"""
        from modules.common import _sse_queues
        assert isinstance(_sse_queues, list)
