"""Tests for core hermesclaw logic: State, cmd(), extract_text, routing."""

import json
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
        assert "HermesClaw v2" in r
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


# ── route_label ───────────────────────────────────────────────────────────


class TestRouteLabel:
    def test_hermes(self):
        assert route_label(Route.HERMES) == "Hermes"

    def test_openclaw(self):
        assert route_label(Route.OPENCLAW) == "OpenClaw"

    def test_both(self):
        assert route_label(Route.BOTH) == "Hermes + OpenClaw"


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
        assert "HermesClaw v2" in args[3]
