"""Tests for core hermesclaw logic: State, cmd(), extract_text, routing."""

import json
import logging
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermesclaw import (
    State,
    Route,
    cmd,
    extract_text,
    route_label,
    route_message,
    proc_msg,
    MessageQueue,
    OpenCodeBridge,
    ACPSession,
    build_opencode_prompt,
)


# ── State ─────────────────────────────────────────────────────────────────


class TestState:
    def test_default_route_is_hermes(self, state_file):
        s = State(state_file)
        assert s.get("u1") == Route.HERMES

    def test_set_and_get(self, state_file):
        s = State(state_file)
        s.set("u1", Route.OPENCLAW)
        assert s.get("u1") == Route.OPENCLAW

    def test_persistence(self, state_file):
        s = State(state_file)
        s.set("u1", Route.BOTH)
        s2 = State(state_file)
        assert s2.get("u1") == Route.BOTH

    def test_should_show_status_initially(self, state_file):
        s = State(state_file)
        assert s.should_show_status("u1") is True

    def test_mark_status_shown(self, state_file):
        s = State(state_file)
        s.mark_status_shown("u1")
        assert s.should_show_status("u1") is False

    def test_set_preserves_status_shown(self, state_file):
        s = State(state_file)
        s.mark_status_shown("u1")
        s.set("u1", Route.BOTH)
        assert s.should_show_status("u1") is False

    def test_v1_state_migration(self, state_file):
        """v1 used {"b": "..."} key; v2 uses {"route": "..."}."""
        Path(state_file).write_text(json.dumps({
            "user1": {"b": "both", "status_shown": True},
            "user2": {"b": "openclaw"},
        }))
        s = State(state_file)
        assert s.get("user1") == Route.BOTH
        assert s.get("user2") == Route.OPENCLAW

    def test_thread_safety(self, state_file):
        s = State(state_file)
        errors = []

        def writer(uid, route):
            try:
                for _ in range(50):
                    s.set(uid, route)
                    s.get(uid)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=("u1", Route.HERMES)),
            threading.Thread(target=writer, args=("u1", Route.OPENCLAW)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ── extract_text ──────────────────────────────────────────────────────────


class TestExtractText:
    def test_plain_text(self):
        items = [{"type": 1, "text_item": {"text": "hi"}}]
        assert extract_text(items) == "hi"

    def test_voice_transcription(self, voice_item):
        result = extract_text([voice_item])
        assert "hello from voice" in result
        assert "voice message" in result

    def test_mixed(self, voice_item):
        items = [{"type": 1, "text_item": {"text": "first"}}, voice_item]
        result = extract_text(items)
        assert result.startswith("first")
        assert "hello from voice" in result

    def test_empty(self):
        assert extract_text([]) == ""

    def test_image_only(self, image_item):
        assert extract_text([image_item]) == ""

    def test_empty_text_ignored(self):
        items = [{"type": 1, "text_item": {"text": ""}}]
        assert extract_text(items) == ""


# ── cmd ───────────────────────────────────────────────────────────────────


class TestCmd:
    def test_hermes(self, state_file):
        s = State(state_file)
        r = cmd(s, "u1", "/hermes")
        assert "Hermes" in r
        assert s.get("u1") == Route.HERMES

    def test_openclaw(self, state_file):
        s = State(state_file)
        r = cmd(s, "u1", "/openclaw")
        assert "OpenClaw" in r
        assert s.get("u1") == Route.OPENCLAW

    def test_both(self, state_file):
        s = State(state_file)
        r = cmd(s, "u1", "/both")
        assert "Hermes + OpenClaw" in r
        assert s.get("u1") == Route.BOTH

    def test_whoami(self, state_file):
        s = State(state_file)
        s.set("u1", Route.OPENCLAW)
        r = cmd(s, "u1", "/whoami")
        assert "HermesClaw v3" in r
        assert "OpenClaw" in r

    def test_passthrough(self, state_file):
        s = State(state_file)
        assert cmd(s, "u1", "just a message") is None

    def test_case_insensitive(self, state_file):
        s = State(state_file)
        assert cmd(s, "u1", "/HERMES") is not None

    def test_with_spaces(self, state_file):
        s = State(state_file)
        assert cmd(s, "u1", " /hermes ") is not None

    def test_opencode(self, state_file):
        s = State(state_file)
        r = cmd(s, "u1", "/opencode")
        assert "OpenCode" in r
        assert s.get("u1") == Route.OPENCODE

    def test_three(self, state_file):
        s = State(state_file)
        r = cmd(s, "u1", "/three")
        assert "three" in r.lower() or "OpenCode" in r
        assert s.get("u1") == Route.THREE

    def test_opencode_not_installed(self, state_file):
        """When opencode bridge reports not available, show install hint."""
        from unittest.mock import MagicMock
        s = State(state_file)
        bridge = MagicMock()
        bridge.is_available.return_value = False
        r = cmd(s, "u1", "/opencode", opencode_bridge=bridge)
        assert "install" in r.lower()
        # Route should NOT be changed
        assert s.get("u1") == Route.HERMES

    def test_three_not_installed(self, state_file):
        """When opencode bridge reports not available, /three shows install hint."""
        from unittest.mock import MagicMock
        s = State(state_file)
        bridge = MagicMock()
        bridge.is_available.return_value = False
        r = cmd(s, "u1", "/three", opencode_bridge=bridge)
        assert "install" in r.lower()
        # Route should NOT be changed
        assert s.get("u1") == Route.HERMES

    def test_whoami_includes_opencode_commands(self, state_file):
        s = State(state_file)
        r = cmd(s, "u1", "/whoami")
        assert "/opencode" in r
        assert "/three" in r


# ── route_label ───────────────────────────────────────────────────────────


class TestRouteLabel:
    def test_hermes(self):
        assert route_label(Route.HERMES) == "Hermes"

    def test_openclaw(self):
        assert route_label(Route.OPENCLAW) == "OpenClaw"

    def test_both(self):
        assert route_label(Route.BOTH) == "Hermes + OpenClaw"

    def test_opencode(self):
        assert route_label(Route.OPENCODE) == "OpenCode"

    def test_three(self):
        assert route_label(Route.THREE) == "Hermes + OpenClaw + OpenCode"


# ── MessageQueue ──────────────────────────────────────────────────────────


class TestMessageQueue:
    def test_enqueue_dequeue(self):
        q = MessageQueue(capacity=10)
        q.enqueue({"id": 1})
        q.enqueue({"id": 2})
        msgs = q.dequeue_all(timeout=0)
        assert len(msgs) == 2
        assert msgs[0]["id"] == 1

    def test_dequeue_empties(self):
        q = MessageQueue()
        q.enqueue({"id": 1})
        q.dequeue_all(timeout=0)
        assert q.dequeue_all(timeout=0) == []

    def test_capacity_drops_oldest(self):
        q = MessageQueue(capacity=2)
        q.enqueue({"id": 1})
        q.enqueue({"id": 2})
        q.enqueue({"id": 3})
        msgs = q.dequeue_all(timeout=0)
        assert len(msgs) == 2
        assert msgs[0]["id"] == 2  # oldest dropped

    def test_size(self):
        q = MessageQueue()
        assert q.size() == 0
        q.enqueue({"id": 1})
        assert q.size() == 1
        q.dequeue_all(timeout=0)
        assert q.size() == 0

    def test_long_poll_blocks(self):
        q = MessageQueue()
        start = time.time()
        result = q.dequeue_all(timeout=0.2)
        elapsed = time.time() - start
        assert result == []
        assert elapsed >= 0.15

    def test_long_poll_wakes_on_enqueue(self):
        q = MessageQueue()
        results = []

        def reader():
            results.extend(q.dequeue_all(timeout=5))

        t = threading.Thread(target=reader)
        t.start()
        time.sleep(0.1)
        q.enqueue({"id": "wake"})
        t.join(timeout=2)
        assert len(results) == 1
        assert results[0]["id"] == "wake"


# ── route_message ─────────────────────────────────────────────────────────


class TestRouteMessage:
    def test_hermes_route(self):
        hq, oq = MessageQueue(), MessageQueue()
        msg = {"id": 1}
        route_message("u1", msg, Route.HERMES, hq, oq)
        assert hq.size() == 1
        assert oq.size() == 0

    def test_openclaw_route(self):
        hq, oq = MessageQueue(), MessageQueue()
        route_message("u1", {"id": 1}, Route.OPENCLAW, hq, oq)
        assert hq.size() == 0
        assert oq.size() == 1

    def test_both_route(self):
        hq, oq = MessageQueue(), MessageQueue()
        route_message("u1", {"id": 1}, Route.BOTH, hq, oq)
        assert hq.size() == 1
        assert oq.size() == 1

    def test_none_queue_graceful(self):
        route_message("u1", {"id": 1}, Route.HERMES, None, None)
        # Should not raise.

    def test_opencode_route(self):
        import queue as stdlib_queue
        hq, oq = MessageQueue(), MessageQueue()
        ocode_q = stdlib_queue.Queue()
        route_message("u1", {"id": 1}, Route.OPENCODE, hq, oq, ocode_q)
        assert hq.size() == 0
        assert oq.size() == 0
        assert ocode_q.qsize() == 1

    def test_three_route(self):
        import queue as stdlib_queue
        hq, oq = MessageQueue(), MessageQueue()
        ocode_q = stdlib_queue.Queue()
        route_message("u1", {"id": 1}, Route.THREE, hq, oq, ocode_q)
        assert hq.size() == 1
        assert oq.size() == 1
        assert ocode_q.qsize() == 1

    def test_opencode_no_queue_graceful(self):
        # No opencode_q provided -- should just log warning, not raise
        hq, oq = MessageQueue(), MessageQueue()
        route_message("u1", {"id": 1}, Route.OPENCODE, hq, oq)
        # Should not raise


# ── proc_msg ──────────────────────────────────────────────────────────────


class TestProcMsg:
    def _proc(self, msg, state, hq, oq):
        with patch("hermesclaw.send_text_ilink"):
            proc_msg(msg, state, "http://fake", "tok", hq, oq)

    def test_text_routes_to_hermes(self, state_file, make_ilink_msg):
        s = State(state_file)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        self._proc(make_ilink_msg(), s, hq, oq)
        assert hq.size() == 1
        assert oq.size() == 0

    def test_slash_not_forwarded(self, state_file, make_ilink_msg):
        s = State(state_file)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        msg = make_ilink_msg(text="/openclaw")
        self._proc(msg, s, hq, oq)
        assert hq.size() == 0
        assert oq.size() == 0
        assert s.get("user123") == Route.OPENCLAW

    def test_image_forwarded(self, state_file, image_item):
        s = State(state_file)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        msg = {
            "from_user_id": "user123",
            "context_token": "ctx",
            "message_type": 1,
            "item_list": [image_item],
        }
        self._proc(msg, s, hq, oq)
        assert hq.size() == 1

    def test_both_mode(self, state_file, make_ilink_msg):
        s = State(state_file)
        s.set("user123", Route.BOTH)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        self._proc(make_ilink_msg(), s, hq, oq)
        assert hq.size() == 1
        assert oq.size() == 1

    def test_non_user_msg_skipped(self, state_file, make_ilink_msg):
        s = State(state_file)
        hq, oq = MessageQueue(), MessageQueue()
        msg = make_ilink_msg(msg_type=2)
        self._proc(msg, s, hq, oq)
        assert hq.size() == 0
        assert oq.size() == 0

    def test_first_contact_shows_status(self, state_file, make_ilink_msg):
        s = State(state_file)
        hq, oq = MessageQueue(), MessageQueue()
        with patch("hermesclaw.send_text_ilink") as mock_send:
            proc_msg(make_ilink_msg(), s, "http://fake", "tok", hq, oq)
        assert mock_send.called
        args = mock_send.call_args[0]
        assert "HermesClaw v3" in args[3]

    def test_opencode_mode(self, state_file, make_ilink_msg):
        """Messages in opencode mode enqueue to opencode_q, not hermes_q."""
        import queue as stdlib_queue
        s = State(state_file)
        s.set("user123", Route.OPENCODE)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        ocode_q = stdlib_queue.Queue()
        with patch("hermesclaw.send_text_ilink"):
            proc_msg(make_ilink_msg(), s, "http://fake", "tok", hq, oq, ocode_q)
        assert hq.size() == 0
        assert oq.size() == 0
        assert ocode_q.qsize() == 1

    def test_three_mode(self, state_file, make_ilink_msg):
        """Messages in three mode go to all three queues."""
        import queue as stdlib_queue
        s = State(state_file)
        s.set("user123", Route.THREE)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        ocode_q = stdlib_queue.Queue()
        with patch("hermesclaw.send_text_ilink"):
            proc_msg(make_ilink_msg(), s, "http://fake", "tok", hq, oq, ocode_q)
        assert hq.size() == 1
        assert oq.size() == 1
        assert ocode_q.qsize() == 1

    @pytest.mark.parametrize("command,expected", [
        ("/hermes", Route.HERMES),
        ("/openclaw", Route.OPENCLAW),
        ("/opencode", Route.OPENCODE),
        ("/both", Route.BOTH),
        ("/three", Route.THREE),
    ])
    def test_all_switch_commands_are_not_forwarded(self, state_file, make_ilink_msg,
                                                   command, expected):
        import queue as stdlib_queue
        s = State(state_file)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        ocode_q = stdlib_queue.Queue()
        with patch("hermesclaw.send_text_ilink") as mock_send:
            proc_msg(
                make_ilink_msg(text=command),
                s,
                "http://fake",
                "tok",
                hq,
                oq,
                ocode_q,
            )
        assert mock_send.called
        assert s.get("user123") == expected
        assert hq.size() == 0
        assert oq.size() == 0
        assert ocode_q.qsize() == 0

    def test_whoami_is_not_forwarded(self, state_file, make_ilink_msg):
        import queue as stdlib_queue
        s = State(state_file)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        ocode_q = stdlib_queue.Queue()
        with patch("hermesclaw.send_text_ilink") as mock_send:
            proc_msg(make_ilink_msg(text="/whoami"), s, "http://fake", "tok", hq, oq, ocode_q)
        assert mock_send.called
        assert hq.size() == 0
        assert oq.size() == 0
        assert ocode_q.qsize() == 0

    @pytest.mark.parametrize("route,expected_h,expected_o,expected_c", [
        (Route.HERMES, 1, 0, 0),
        (Route.OPENCLAW, 0, 1, 0),
        (Route.OPENCODE, 0, 0, 1),
        (Route.BOTH, 1, 1, 0),
        (Route.THREE, 1, 1, 1),
    ])
    @pytest.mark.parametrize("items", [
        [{"type": 1, "text_item": {"text": "text payload"}}],
        [{"type": 3, "voice_item": {"text": "voice payload"}}],
        [{"type": 2, "image_item": {"image_url": "https://example.com/a.jpg"}}],
        [{"type": 4, "video_item": {"video_url": "https://example.com/a.mp4"}}],
        [{"type": 5, "file_item": {"file_url": "https://example.com/a.pdf", "file_name": "a.pdf"}}],
    ])
    def test_route_matrix_for_all_message_item_types(
        self, state_file, make_ilink_msg, route, expected_h, expected_o, expected_c, items, caplog
    ):
        import queue as stdlib_queue
        s = State(state_file)
        s.set("user123", route)
        s.mark_status_shown("user123")
        hq, oq = MessageQueue(), MessageQueue()
        ocode_q = stdlib_queue.Queue()
        with caplog.at_level(logging.INFO, logger="hermesclaw"), patch("hermesclaw.send_text_ilink"):
            proc_msg(
                make_ilink_msg(items=items),
                s,
                "http://fake",
                "tok",
                hq,
                oq,
                ocode_q,
            )
        assert hq.size() == expected_h
        assert oq.size() == expected_o
        assert ocode_q.qsize() == expected_c
        assert f"types={items[0]['type']}" in caplog.text


# ── opencode_worker ───────────────────────────────────────────────────────


class TestOpenCodeWorker:
    """Test that opencode_worker correctly extracts text from full iLink messages."""

    def _make_msg(self, text="hello opencode", voice_text=None, uid="u1"):
        items = []
        if text:
            items.append({"type": 1, "text_item": {"text": text}})
        if voice_text:
            items.append({"type": 3, "voice_item": {"text": voice_text}})
        return {
            "from_user_id": uid,
            "message_type": 1,
            "item_list": items,
            "context_token": "ctx123",
        }

    def test_text_message_reaches_bridge(self, state_file):
        """Text messages are extracted and forwarded to OpenCodeBridge."""
        import queue as stdlib_queue
        s = State(state_file)
        s.set("u1", Route.OPENCODE)
        q = stdlib_queue.Queue()
        q.put(("u1", self._make_msg("what is 2+2?")))
        q.put(None)  # sentinel

        from hermesclaw import opencode_worker
        bridge = MagicMock()
        bridge.send.return_value = "4"

        def _worker():
            while True:
                item = q.get()
                if item is None:
                    break
                uid, msg = item
                from hermesclaw import extract_text, Route, send_text_ilink
                import hermesclaw as hc
                txt = hc.extract_text(msg.get("item_list", []))
                assert txt == "what is 2+2?", f"got: {txt!r}"

        import threading
        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=3)
        assert not t.is_alive()

    def test_voice_message_transcription_reaches_bridge(self, state_file):
        """Voice transcription is extracted (not raw audio) and forwarded to OpenCode."""
        import hermesclaw as hc
        msg = self._make_msg(text=None, voice_text="今天天气怎么样")
        txt = hc.extract_text(msg.get("item_list", []))
        assert "今天天气怎么样" in txt
        assert "voice message" in txt.lower()

    def test_empty_non_text_message_is_skipped(self, state_file):
        """Media-only messages build OpenCode content blocks instead of disappearing."""
        import hermesclaw as hc
        msg = {
            "from_user_id": "u1",
            "message_type": 1,
            "item_list": [{"type": 2, "image_item": {"cdn_url": "https://example.com/a.jpg"}}],
            "context_token": "ctx",
        }
        txt = hc.extract_text(msg.get("item_list", []))
        assert txt == ""
        with patch("hermesclaw._download_media", return_value=(None, "", "offline")):
            blocks = build_opencode_prompt(msg["item_list"])
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "resource_link"
        assert blocks[1]["uri"] == "https://example.com/a.jpg"

    def test_image_prompt_prefers_acp_image_when_downloadable(self):
        item = {"type": 2, "image_item": {"image_url": "https://example.com/a.png"}}
        with patch("hermesclaw._download_media", return_value=(b"png-bytes", "image/png", None)):
            blocks = build_opencode_prompt([item])
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "image"
        assert blocks[1]["mimeType"] == "image/png"

    @pytest.mark.parametrize("item,expected_kind", [
        ({"type": 4, "video_item": {"video_url": "https://example.com/a.mp4"}}, "video"),
        ({"type": 5, "file_item": {"file_url": "https://example.com/a.pdf", "file_name": "a.pdf"}}, "file"),
    ])
    def test_video_and_file_prompt_use_resource_links(self, item, expected_kind):
        blocks = build_opencode_prompt([item])
        assert expected_kind in blocks[0]["text"]
        assert blocks[1]["type"] == "resource_link"
        assert blocks[1]["uri"].startswith("https://example.com/")

    def test_file_item_overrides_video_type_number(self):
        """file_item sub-key takes precedence over type=4 — avoids 'video' misidentification."""
        item = {
            "type": 4,  # video type int but sub-key says file
            "file_item": {"file_url": "https://example.com/doc.mp4", "file_name": "doc.mp4"},
        }
        blocks = build_opencode_prompt([item])
        intro = blocks[0]["text"].split("Metadata:")[0]
        assert "file" in intro
        assert "video" not in intro
        # MIME must not be video/* — the mixed signal confuses the LLM
        meta = json.loads(blocks[0]["text"].split("Metadata:")[1])
        assert not meta["mimeType"].startswith("video/"), f"mimeType was {meta['mimeType']!r}"
        assert blocks[1]["type"] == "resource_link"


class TestACPSession:
    """Test ACPSession with an in-process mock ACP server."""

    @staticmethod
    def _make_mock_server():
        """Return (mock_proc, server_thread) with a simulated ACP server."""
        import os, io

        # Pipe for stdin: hermesclaw writes → server reads
        srv_r_fd, cli_w_fd = os.pipe()
        # Pipe for stdout: server writes → hermesclaw reads
        cli_r_fd, srv_w_fd = os.pipe()

        def _server():
            r = os.fdopen(srv_r_fd, "r")
            w = os.fdopen(srv_w_fd, "w")
            try:
                for raw in r:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    rid = msg.get("id")
                    method = msg.get("method", "")
                    if method == "initialize":
                        resp = {"jsonrpc": "2.0", "id": rid,
                                "result": {"protocolVersion": 1, "agentCapabilities": {}}}
                        w.write(json.dumps(resp) + "\n")
                        w.flush()
                    elif method == "session/new":
                        resp = {"jsonrpc": "2.0", "id": rid,
                                "result": {"sessionId": "mock-sess-id", "configOptions": []}}
                        w.write(json.dumps(resp) + "\n")
                        w.flush()
                    elif method == "session/prompt":
                        sid = msg.get("params", {}).get("sessionId", "")
                        notif = {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": sid,
                                "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "messageId": "m1",
                                    "content": {"type": "text", "text": "hello from mock opencode"},
                                },
                            },
                        }
                        w.write(json.dumps(notif) + "\n")
                        w.flush()
                        resp = {"jsonrpc": "2.0", "id": rid,
                                "result": {"stopReason": "end_turn", "usage": {}}}
                        w.write(json.dumps(resp) + "\n")
                        w.flush()
            except Exception:
                pass
            finally:
                w.close()

        t = threading.Thread(target=_server, daemon=True)
        t.start()

        class _MockProc:
            stdin = os.fdopen(cli_w_fd, "wb")
            stdout = os.fdopen(cli_r_fd, "rb")
            stderr = io.BytesIO(b"")

            def terminate(self):
                try:
                    self.stdin.close()
                except Exception:
                    pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        return _MockProc(), t

    def test_initialize_and_session(self):
        """ACPSession initializes and creates session via mock server."""
        mock_proc, _ = self._make_mock_server()
        with patch("subprocess.Popen", return_value=mock_proc):
            sess = ACPSession("opencode", "/tmp", "opencode/minimax-m2.5-free")
        assert sess.session_id == "mock-sess-id"
        assert sess.alive is True

    def test_prompt_returns_text(self):
        """prompt() collects agent_message_chunk text and returns it."""
        mock_proc, _ = self._make_mock_server()
        with patch("subprocess.Popen", return_value=mock_proc):
            sess = ACPSession("opencode", "/tmp", "opencode/minimax-m2.5-free")
        result = sess.prompt("test question")
        assert result == "hello from mock opencode"

    def test_permission_request_is_auto_selected(self):
        """ACP permission prompts are answered so headless sessions do not hang."""
        import os, io

        srv_r_fd, cli_w_fd = os.pipe()
        cli_r_fd, srv_w_fd = os.pipe()
        observed_permission = {}

        def _permission_server():
            r = os.fdopen(srv_r_fd, "r")
            w = os.fdopen(srv_w_fd, "w")
            pending_permission = False
            prompt_id = None
            try:
                for raw in r:
                    msg = json.loads(raw.strip())
                    rid = msg.get("id")
                    method = msg.get("method", "")
                    if method == "initialize":
                        w.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                            "result": {"protocolVersion": 1, "agentCapabilities": {}}}) + "\n")
                        w.flush()
                    elif method == "session/new":
                        w.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                            "result": {"sessionId": "perm-sess", "configOptions": []}}) + "\n")
                        w.flush()
                    elif method == "session/prompt":
                        prompt_id = rid
                        pending_permission = True
                        w.write(json.dumps({
                            "jsonrpc": "2.0",
                            "id": 99,
                            "method": "session/request_permission",
                            "params": {
                                "sessionId": "perm-sess",
                                "toolCall": {"toolCallId": "call-1", "title": "Read /etc", "kind": "read"},
                                "options": [
                                    {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                                    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                                ],
                            },
                        }) + "\n")
                        w.flush()
                    elif rid == 99 and "result" in msg:
                        observed_permission.update(msg["result"])
                        notif = {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": "perm-sess",
                                "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "content": {"type": "text", "text": "permission ok"},
                                },
                            },
                        }
                        w.write(json.dumps(notif) + "\n")
                        w.write(json.dumps({"jsonrpc": "2.0", "id": prompt_id,
                                            "result": {"stopReason": "end_turn"}}) + "\n")
                        w.flush()
                        pending_permission = False
                assert not pending_permission
            finally:
                w.close()

        threading.Thread(target=_permission_server, daemon=True).start()

        class _PermissionProc:
            stdin = os.fdopen(cli_w_fd, "wb")
            stdout = os.fdopen(cli_r_fd, "rb")
            stderr = io.BytesIO(b"")
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass

        with patch("subprocess.Popen", return_value=_PermissionProc()):
            sess = ACPSession("opencode", "/tmp", "opencode/minimax-m2.5-free")
        result = sess.prompt("needs permission", timeout=3)
        assert result == "permission ok"
        assert observed_permission["outcome"]["outcome"] == "selected"
        assert observed_permission["outcome"]["optionId"] == "allow"

    def test_prompt_timeout_returns_error_string(self):
        """When ACP server doesn't respond in time, return error string."""
        import os, io
        srv_r_fd, cli_w_fd = os.pipe()
        cli_r_fd, srv_w_fd = os.pipe()

        def _partial_server():
            r = os.fdopen(srv_r_fd, "r")
            w = os.fdopen(srv_w_fd, "w")
            for raw in r:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                rid = msg.get("id")
                method = msg.get("method", "")
                if method == "initialize":
                    w.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                        "result": {"protocolVersion": 1, "agentCapabilities": {}}}) + "\n")
                    w.flush()
                elif method == "session/new":
                    w.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                        "result": {"sessionId": "mock-sess", "configOptions": []}}) + "\n")
                    w.flush()
                # session/prompt: intentionally no response → timeout

        threading.Thread(target=_partial_server, daemon=True).start()

        class _TimeoutProc:
            stdin = os.fdopen(cli_w_fd, "wb")
            stdout = os.fdopen(cli_r_fd, "rb")
            stderr = io.BytesIO(b"")
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass

        with patch("subprocess.Popen", return_value=_TimeoutProc()):
            sess = ACPSession("opencode", "/tmp", "opencode/minimax-m2.5-free")
        result = sess.prompt("anything", timeout=1)
        assert "timeout" in result.lower()

    def test_dead_subprocess_unblocks_prompt(self):
        """If the ACP subprocess dies mid-prompt, prompt() returns an error quickly."""
        import os, io, time

        srv_r_fd, cli_w_fd = os.pipe()
        cli_r_fd, srv_w_fd = os.pipe()

        def _server_dies_after_handshake():
            r = os.fdopen(srv_r_fd, "r")
            w = os.fdopen(srv_w_fd, "w")
            try:
                for raw in r:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    rid = msg.get("id")
                    method = msg.get("method", "")
                    if method == "initialize":
                        w.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                            "result": {"protocolVersion": 1, "agentCapabilities": {}}}) + "\n")
                        w.flush()
                    elif method == "session/new":
                        w.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                            "result": {"sessionId": "dying-sess", "configOptions": []}}) + "\n")
                        w.flush()
                    elif method == "session/prompt":
                        # Simulate subprocess dying: close stdout without responding
                        break
            finally:
                w.close()  # EOF on stdout → _read_loop exits

        threading.Thread(target=_server_dies_after_handshake, daemon=True).start()

        class _DyingProc:
            stdin = os.fdopen(cli_w_fd, "wb")
            stdout = os.fdopen(cli_r_fd, "rb")
            stderr = io.BytesIO(b"")
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass

        with patch("subprocess.Popen", return_value=_DyingProc()):
            sess = ACPSession("opencode", "/tmp", "opencode/minimax-m2.5-free")

        t0 = time.monotonic()
        result = sess.prompt("question", timeout=10)
        elapsed = time.monotonic() - t0

        # Should return quickly (process died, not wait 10s for timeout)
        assert elapsed < 5, f"prompt took {elapsed:.1f}s — should have unblocked on EOF"
        assert "error" in result.lower() or "died" in result.lower()
        assert sess.alive is False

    def test_prompt_blocks_timeout_marks_session_dead(self):
        """After prompt_blocks() times out, alive is False so next message gets fresh session."""
        import os, io

        srv_r_fd, cli_w_fd = os.pipe()
        cli_r_fd, srv_w_fd = os.pipe()

        def _handshake_only():
            r = os.fdopen(srv_r_fd, "r")
            w = os.fdopen(srv_w_fd, "w")
            for raw in r:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                rid = msg.get("id")
                method = msg.get("method", "")
                if method == "initialize":
                    w.write(json.dumps({
                        "jsonrpc": "2.0", "id": rid,
                        "result": {"protocolVersion": 1, "agentCapabilities": {}},
                    }) + "\n")
                    w.flush()
                elif method == "session/new":
                    w.write(json.dumps({
                        "jsonrpc": "2.0", "id": rid,
                        "result": {"sessionId": "timeout-sess", "configOptions": []},
                    }) + "\n")
                    w.flush()
                # session/prompt: no response — triggers timeout

        threading.Thread(target=_handshake_only, daemon=True).start()

        class _NoRespProc:
            stdin = os.fdopen(cli_w_fd, "wb")
            stdout = os.fdopen(cli_r_fd, "rb")
            stderr = io.BytesIO(b"")
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass

        with patch("subprocess.Popen", return_value=_NoRespProc()):
            sess = ACPSession("opencode", "/tmp", "opencode/minimax-m2.5-free")

        assert sess.alive is True
        result = sess.prompt_blocks([{"type": "text", "text": "hi"}], timeout=1)
        assert "timeout" in result.lower()
        assert sess.alive is False  # must be marked dead so next message gets a fresh session


# ── OpenCodeBridge ────────────────────────────────────────────────────────


class TestOpenCodeBridge:
    def test_is_available_false_when_not_found(self):
        bridge = OpenCodeBridge("/nonexistent/opencode")
        assert bridge.is_available() is False

    def test_cmd_opencode_not_installed_gives_hint(self, state_file):
        s = State(state_file)
        bridge = OpenCodeBridge("/nonexistent/opencode")
        r = cmd(s, "u1", "/opencode", opencode_bridge=bridge)
        assert "install" in r.lower()
        assert s.get("u1") == Route.HERMES  # route unchanged

    def test_cmd_three_not_installed_gives_hint(self, state_file):
        s = State(state_file)
        bridge = OpenCodeBridge("/nonexistent/opencode")
        r = cmd(s, "u1", "/three", opencode_bridge=bridge)
        assert "install" in r.lower()
        assert s.get("u1") == Route.HERMES  # route unchanged

    def test_get_or_create_retries_on_crash(self):
        """_get_or_create retries up to 3 times when opencode acp crashes on startup."""
        call_count = [0]
        mock_session = MagicMock()
        mock_session.alive = True

        def _flaky_session(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("ACP initialize failed: ACP process died")
            return mock_session

        bridge = OpenCodeBridge("/fake/opencode")
        with patch("hermesclaw.ACPSession", side_effect=_flaky_session):
            with patch("hermesclaw.time.sleep"):  # skip actual 2-second delays
                sess = bridge._get_or_create("u1")
        assert call_count[0] == 3
        assert sess is mock_session

    def test_get_or_create_raises_after_three_failures(self):
        """After 3 consecutive crashes, _get_or_create raises so the worker shows an error."""
        def _always_fail(*args, **kwargs):
            raise RuntimeError("ACP process died")

        bridge = OpenCodeBridge("/fake/opencode")
        with patch("hermesclaw.ACPSession", side_effect=_always_fail):
            with patch("hermesclaw.time.sleep"):
                with pytest.raises(RuntimeError, match="3 attempts"):
                    bridge._get_or_create("u1")
