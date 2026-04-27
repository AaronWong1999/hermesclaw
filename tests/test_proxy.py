"""Tests for GatewayProxy HTTP handler: getupdates, sendmessage, tagging."""

import json
import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch

import pytest
import requests

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermesclaw import (
    MessageQueue,
    State,
    Route,
    make_proxy_handler,
    PROXY_ALLOWLIST,
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _MockILinkHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that records the last request and replies 200."""

    last_request = None  # class-level: shared across instances

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        _MockILinkHandler.last_request = {
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        }
        resp = b'{"ret":0}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


@pytest.fixture
def mock_ilink():
    """Start a mock iLink backend and return its base URL."""
    _MockILinkHandler.last_request = None
    port = _free_port()
    srv = HTTPServer(("127.0.0.1", port), _MockILinkHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture
def proxy_env(state_file, mock_ilink):
    """Spin up a proxy server backed by a mock iLink."""
    queue = MessageQueue(capacity=50)
    state = State(state_file)
    port = _free_port()
    handler = make_proxy_handler(
        queue,
        ilink_base_url=mock_ilink,
        ilink_token="test-tok-123",
        state=state,
        tag="[TestTag]",
    )
    server = HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {
        "queue": queue,
        "state": state,
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "server": server,
        "ilink_url": mock_ilink,
    }
    server.shutdown()


class TestGetUpdates:
    def test_returns_queued_messages(self, proxy_env):
        q = proxy_env["queue"]
        q.enqueue({"id": 1})
        q.enqueue({"id": 2})
        resp = requests.post(
            f"{proxy_env['url']}/ilink/bot/getupdates",
            json={"get_updates_buf": "abc"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["msgs"]) == 2
        assert data["get_updates_buf"] == "abc"

    def test_empty_queue_returns_empty(self, proxy_env):
        """With empty queue and very short timeout, returns empty."""
        # Override POLL_SEC for test speed via a patched constant.
        with patch("hermesclaw.DEFAULT_POLL_SEC", 0.3):
            handler = make_proxy_handler(
                proxy_env["queue"],
                "http://fake:9999", "tok",
                proxy_env["state"], "[T]",
            )
            port = _free_port()
            srv = HTTPServer(("127.0.0.1", port), handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                resp = requests.post(
                    f"http://127.0.0.1:{port}/ilink/bot/getupdates",
                    json={"get_updates_buf": "x"},
                    timeout=5,
                )
                assert resp.json()["msgs"] == []
            finally:
                srv.shutdown()

    def test_dequeue_is_destructive(self, proxy_env):
        proxy_env["queue"].enqueue({"id": 1})
        requests.post(
            f"{proxy_env['url']}/ilink/bot/getupdates",
            json={"get_updates_buf": ""},
            timeout=5,
        )
        # Second call should get nothing (with short timeout).
        with patch("hermesclaw.DEFAULT_POLL_SEC", 0.2):
            handler = make_proxy_handler(
                proxy_env["queue"],
                "http://fake:9999", "tok",
                proxy_env["state"], "[T]",
            )
            port = _free_port()
            srv = HTTPServer(("127.0.0.1", port), handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                resp = requests.post(
                    f"http://127.0.0.1:{port}/ilink/bot/getupdates",
                    json={},
                    timeout=5,
                )
                assert resp.json()["msgs"] == []
            finally:
                srv.shutdown()

    def test_buf_echoed(self, proxy_env):
        proxy_env["queue"].enqueue({"id": 1})
        resp = requests.post(
            f"{proxy_env['url']}/ilink/bot/getupdates",
            json={"get_updates_buf": "my-cursor-123"},
            timeout=5,
        )
        assert resp.json()["get_updates_buf"] == "my-cursor-123"


class TestSendMessage:
    def test_forward_to_ilink(self, proxy_env):
        proxy_env["state"].mark_status_shown("u1")

        payload = {
            "msg": {
                "to_user_id": "u1",
                "item_list": [{"type": 1, "text_item": {"text": "hi"}}],
            }
        }
        resp = requests.post(
            f"{proxy_env['url']}/ilink/bot/sendmessage",
            json=payload,
            timeout=5,
        )
        assert resp.status_code == 200
        # Verify the mock iLink backend received the request with our token.
        req = _MockILinkHandler.last_request
        assert req is not None
        assert "test-tok-123" in req["headers"].get("Authorization", req["headers"].get("authorization", ""))

    def test_both_mode_tags_text(self, proxy_env):
        proxy_env["state"].set("u1", Route.BOTH)
        proxy_env["state"].mark_status_shown("u1")

        payload = {
            "msg": {
                "to_user_id": "u1",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        }
        resp = requests.post(
            f"{proxy_env['url']}/ilink/bot/sendmessage",
            json=payload,
            timeout=5,
        )
        assert resp.status_code == 200
        req = _MockILinkHandler.last_request
        sent_data = json.loads(req["body"])
        text = sent_data["msg"]["item_list"][0]["text_item"]["text"]
        assert text.startswith("[TestTag]")

    def test_three_mode_tags_text(self, proxy_env):
        proxy_env["state"].set("u1", Route.THREE)
        proxy_env["state"].mark_status_shown("u1")

        payload = {
            "msg": {
                "to_user_id": "u1",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        }
        resp = requests.post(
            f"{proxy_env['url']}/ilink/bot/sendmessage",
            json=payload,
            timeout=5,
        )
        assert resp.status_code == 200
        req = _MockILinkHandler.last_request
        sent_data = json.loads(req["body"])
        text = sent_data["msg"]["item_list"][0]["text_item"]["text"]
        assert text.startswith("[TestTag]")

    def test_single_mode_no_tag(self, proxy_env):
        proxy_env["state"].set("u1", Route.HERMES)
        proxy_env["state"].mark_status_shown("u1")

        payload = {
            "msg": {
                "to_user_id": "u1",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        }
        resp = requests.post(
            f"{proxy_env['url']}/ilink/bot/sendmessage",
            json=payload,
            timeout=5,
        )
        req = _MockILinkHandler.last_request
        sent_data = json.loads(req["body"])
        text = sent_data["msg"]["item_list"][0]["text_item"]["text"]
        assert text == "hello"  # No tag in single mode.


class TestProxyAllowlist:
    def test_blocked_endpoint(self, proxy_env):
        resp = requests.post(
            f"{proxy_env['url']}/ilink/bot/deleteaccount",
            json={},
            timeout=5,
        )
        assert resp.status_code == 404

    def test_allowed_passthrough(self, proxy_env):
        resp = requests.post(
            f"{proxy_env['url']}/ilink/bot/sendtyping",
            json={"typing": True},
            timeout=5,
        )
        assert resp.status_code == 200

    def test_auth_header_replaced(self, proxy_env):
        requests.post(
            f"{proxy_env['url']}/ilink/bot/getconfig",
            json={},
            headers={"Authorization": "Bearer gateway-original-token"},
            timeout=5,
        )
        req = _MockILinkHandler.last_request
        assert req is not None
        auth = req["headers"].get("Authorization", req["headers"].get("authorization", ""))
        assert "test-tok-123" in auth
        assert "gateway-original-token" not in auth


class TestProxyError:
    def test_forward_error_returns_502(self, state_file):
        """When the iLink backend is unreachable, proxy returns 502."""
        queue = MessageQueue()
        state = State(state_file)
        port = _free_port()
        # Point to a port that's not listening.
        handler = make_proxy_handler(
            queue,
            ilink_base_url="http://127.0.0.1:1",
            ilink_token="tok",
            state=state,
            tag="[T]",
        )
        srv = HTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            resp = requests.post(
                f"http://127.0.0.1:{port}/ilink/bot/sendtyping",
                json={},
                timeout=10,
            )
            assert resp.status_code == 502
        finally:
            srv.shutdown()
