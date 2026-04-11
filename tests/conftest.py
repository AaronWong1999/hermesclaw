"""Shared fixtures for HermesClaw v2 tests."""

import json
import tempfile
from pathlib import Path

import pytest

# Ensure the project root is importable.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "test_state.json")


@pytest.fixture
def make_ilink_msg():
    """Factory: build a minimal iLink inbound message."""
    def _make(uid="user123", text="hello", msg_type=1, ctx="ctx-abc", items=None):
        if items is None:
            items = [{"type": 1, "text_item": {"text": text}}]
        return {
            "from_user_id": uid,
            "to_user_id": "bot_id",
            "context_token": ctx,
            "message_type": msg_type,
            "item_list": items,
        }
    return _make


@pytest.fixture
def voice_item():
    """A voice iLink item with transcription."""
    return {
        "type": 3,
        "voice_item": {
            "text": "hello from voice",
            "voice_url": "https://cdn.example.com/voice.silk",
        },
    }


@pytest.fixture
def image_item():
    """An image iLink item (no text)."""
    return {
        "type": 2,
        "image_item": {
            "image_url": "https://cdn.example.com/img.jpg",
        },
    }
