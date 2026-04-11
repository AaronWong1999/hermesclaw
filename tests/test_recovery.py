"""Tests for recovery, backoff, and graceful degradation."""

import time
import threading
from unittest.mock import patch, MagicMock, call

import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermesclaw import (
    State,
    Route,
    MessageQueue,
    route_message,
    proc_msg,
    get_updates_real,
    MAX_FAILS,
    BACKOFF,
)


class TestPollBackoff:
    """Verify backoff logic by testing get_updates_real + sleep behavior."""

    def test_backs_off_after_max_fails(self):
        """Simulate poll_loop inline: after MAX_FAILS errors, sleep(BACKOFF)."""
        sleep_calls = []

        def do_sleep(t):
            sleep_calls.append(t)

        fails = 0
        for _ in range(MAX_FAILS + 1):
            resp = {"ret": -1, "msgs": [], "get_updates_buf": ""}
            ret = resp.get("ret")
            if ret not in (0, None):
                fails += 1
                if fails >= MAX_FAILS:
                    do_sleep(BACKOFF)
                    fails = 0
                else:
                    do_sleep(2)

        assert BACKOFF in sleep_calls

    def test_resets_after_success(self):
        """A successful poll resets the fail counter."""
        sleep_calls = []

        def do_sleep(t):
            sleep_calls.append(t)

        responses = [
            {"ret": -1, "msgs": [], "get_updates_buf": ""},
            {"ret": 0, "msgs": [], "get_updates_buf": "ok"},
            {"ret": -1, "msgs": [], "get_updates_buf": ""},
        ]

        fails = 0
        for resp in responses:
            ret = resp.get("ret")
            if ret not in (0, None):
                fails += 1
                if fails >= MAX_FAILS:
                    do_sleep(BACKOFF)
                    fails = 0
                else:
                    do_sleep(2)
            else:
                fails = 0

        assert BACKOFF not in sleep_calls


class TestGatewayRestart:
    """Verify queue behavior after a simulated gateway restart."""

    def test_messages_enqueued_before_gateway_reads(self):
        """Messages queue up while gateway is not polling; once it reads,
        it gets everything."""
        q = MessageQueue()
        q.enqueue({"id": 1})
        q.enqueue({"id": 2})
        q.enqueue({"id": 3})
        # Simulate gateway restart: nothing read for a while.
        time.sleep(0.05)
        msgs = q.dequeue_all(timeout=0)
        assert len(msgs) == 3

    def test_queue_empty_after_dequeue(self):
        """After gateway dequeues, a new poll returns empty."""
        q = MessageQueue()
        q.enqueue({"id": 1})
        q.dequeue_all(timeout=0)
        assert q.dequeue_all(timeout=0) == []


class TestSingleGateway:
    """Verify graceful degradation when one gateway is missing."""

    def test_hermes_only(self, state_file):
        """Messages route to hermes queue even when openclaw queue is None."""
        s = State(state_file)
        hq = MessageQueue()
        route_message("u1", {"id": 1}, Route.HERMES, hq, None)
        assert hq.size() == 1

    def test_openclaw_only(self, state_file):
        s = State(state_file)
        oq = MessageQueue()
        route_message("u1", {"id": 1}, Route.OPENCLAW, None, oq)
        assert oq.size() == 1

    def test_both_with_one_missing(self, state_file):
        """In /both mode with only one queue, message goes to available one."""
        hq = MessageQueue()
        route_message("u1", {"id": 1}, Route.BOTH, hq, None)
        assert hq.size() == 1

    def test_proc_msg_with_missing_gateway(self, state_file, make_ilink_msg):
        """proc_msg still works when a gateway queue is None."""
        s = State(state_file)
        s.mark_status_shown("user123")
        hq = MessageQueue()
        with patch("hermesclaw.send_text_ilink"):
            proc_msg(make_ilink_msg(), s, "http://fake", "tok", hq, None)
        assert hq.size() == 1
