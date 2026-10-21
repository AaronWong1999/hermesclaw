"""Exercise configured models at the serialized ACP subprocess boundary."""

import io
import json
import queue
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

import hermesclaw


class ACPProcess:
    def __init__(self, reject_model=False):
        self.messages = []
        self.responses = queue.Queue()
        self.stdin = self
        self.stdout = self
        self.stderr = io.BytesIO()
        self.reject_model = reject_model
        self.terminated = False

    def write(self, raw):
        message = json.loads(raw.decode())
        self.messages.append(message)
        method = message["method"]
        result = {}
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {}}
        elif method == "session/new":
            result = {"sessionId": "test-session"}
        response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        if method == "session/set_model" and self.reject_model:
            response.pop("result")
            response["error"] = {"code": -32602, "message": "unsupported model"}
        self.responses.put((json.dumps(response) + "\n").encode())

    def flush(self):
        pass

    def readline(self):
        return self.responses.get(timeout=5)

    def terminate(self):
        self.terminated = True
        self.responses.put(b"")

    def wait(self, timeout=None):
        return 0


MODELS = [None, "opencode/minimax-m3", "minimax/MiniMax-M3.1-Flash",
          "minimax-cn/MiniMax-M3.1-Flash", "minimax/MiniMax-M3",
          "minimax-cn/MiniMax-M3", "opencode/minimax-m2.5-free",
          "minimax/MiniMax-M2.7", "custom/my-model"]


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("entry", ["constructor", "environment", "saved"])
def test_model_reaches_acp_transport(model, entry, tmp_path, monkeypatch):
    process = ACPProcess()
    blocks = [{"type": "text", "text": "hello"},
              {"type": "image", "mimeType": "image/png", "data": "cG5n"},
              {"type": "resource_link", "name": "clip", "uri": "https://example.com/a.mp4"}]
    expected = model or "opencode/minimax-m3.1-flash-free"
    original_bridge = hermesclaw.OpenCodeBridge

    def make_bridge(*args, **kwargs):
        bridge = original_bridge(*args, **kwargs)
        bridge.send_blocks("test-user", blocks)
        return bridge

    with patch("hermesclaw.subprocess.Popen", return_value=process):
        if entry == "constructor":
            bridge = make_bridge("opencode", **({"model": model} if model else {}))
            bridge.close_all()
        else:
            saved = tmp_path / "saved.env"
            saved.write_text(f"OPENCODE_MODEL={model}\n" if model and entry == "saved" else "")
            environment = {"ILINK_TOKEN": "offline-fixture", "HERMES_ENABLED": "false",
                           "OPENCLAW_ENABLED": "false", "LOG_FILE": str(tmp_path / "test.log"),
                           "STATE_FILE": str(tmp_path / "state.json")}
            if model and entry == "environment":
                environment["OPENCODE_MODEL"] = model
            with patch.dict("os.environ", environment, clear=True), \
                 patch("dotenv.load_dotenv", side_effect=lambda *a: load_dotenv(saved)), \
                 patch("hermesclaw.OpenCodeBridge", side_effect=make_bridge), \
                 patch.object(original_bridge, "is_available", return_value=False), \
                 patch("hermesclaw.poll_loop"), \
                 patch("hermesclaw.signal.signal", side_effect=lambda sig, callback: callback(sig, None)):
                hermesclaw.main()

    assert [m["method"] for m in process.messages] == [
        "initialize", "session/new", "session/set_model", "session/prompt"]
    assert process.messages[2]["params"] == {"sessionId": "test-session", "modelId": expected}
    assert process.messages[3]["params"] == {"sessionId": "test-session", "prompt": blocks}


def test_rejected_model_does_not_prompt_or_fall_back():
    process = ACPProcess(reject_model=True)
    with patch("hermesclaw.subprocess.Popen", return_value=process):
        with pytest.raises(RuntimeError, match="session/set_model failed"):
            hermesclaw.ACPSession("opencode", ".", "custom/unavailable")
    assert process.terminated
    assert all(m["method"] != "session/prompt" for m in process.messages)
