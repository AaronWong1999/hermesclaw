"""HermesClaw v3: triple-gateway proxy router for WeChat.

Takes over one iLink token, polls for messages, and distributes them
to two independent proxy servers -- one for OpenClaw's clawbot and one
for Hermes Agent's WeChat gateway.  Each gateway believes it is talking
directly to the iLink API.
"""

import json
import logging
import os
import queue
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
from enum import Enum
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T, VO = 1, 3  # iLink message types: text, voice
ILINK_VER = "2.1.7"
ILINK_CV = "65547"
QUEUE_CAP = 200
DEFAULT_POLL_SEC = 35

log = logging.getLogger("hermesclaw")

# ---------------------------------------------------------------------------
# Route enum & persistent state
# ---------------------------------------------------------------------------


class Route(str, Enum):
    HERMES = "hermes"
    OPENCLAW = "openclaw"
    OPENCODE = "opencode"
    BOTH = "both"
    THREE = "three"


class State:
    """Per-user routing state, persisted to JSON."""

    def __init__(self, fp):
        self.fp = Path(fp)
        self.d = {}
        self.lock = threading.Lock()
        if self.fp.exists():
            try:
                self.d = json.loads(self.fp.read_text())
                log.info("Loaded %d route states", len(self.d))
            except Exception:
                pass

    def save(self):
        t = self.fp.with_suffix(".tmp")
        t.write_text(json.dumps(self.d, indent=2, ensure_ascii=False))
        t.replace(self.fp)

    def get(self, uid):
        with self.lock:
            if uid not in self.d:
                self.d[uid] = {"route": Route.HERMES.value, "status_shown": False}
            entry = self.d[uid]
            # Migrate v1 format {"b": ...} to v2 {"route": ...}
            raw = entry.get("route") or entry.get("b", Route.HERMES.value)
            return Route(raw)

    def should_show_status(self, uid):
        with self.lock:
            if uid not in self.d:
                self.d[uid] = {"route": Route.HERMES.value, "status_shown": False}
            return not self.d[uid].get("status_shown", False)

    def mark_status_shown(self, uid):
        with self.lock:
            if uid not in self.d:
                self.d[uid] = {"route": Route.HERMES.value, "status_shown": True}
            else:
                self.d[uid]["status_shown"] = True
            self.save()

    def set(self, uid, route):
        with self.lock:
            status_shown = self.d.get(uid, {}).get("status_shown", False)
            self.d[uid] = {"route": route.value, "status_shown": status_shown}
            self.save()
            log.info("User %s -> %s", uid[:16], route.value)


# ---------------------------------------------------------------------------
# Text extraction (voice -> transcription)
# ---------------------------------------------------------------------------


def extract_text(items):
    """Return combined text from iLink items.

    Voice items contribute only their iLink transcription by design.
    """
    parts = []
    for it in items:
        tp = it.get("type", 0)
        if tp == T:
            x = it.get("text_item", {}).get("text", "")
            if x:
                parts.append(x)
        elif tp == VO:
            x = it.get("voice_item", {}).get("text", "")
            if x:
                parts.append(
                    f'[The user sent a voice message. Here\'s what they said: "{x}"]'
                )
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# OpenCode ACP bridge
# ---------------------------------------------------------------------------


class ACPSession:
    """A single ACP session backed by an opencode acp subprocess."""

    def __init__(self, opencode_cmd, cwd, model):
        self.model = model
        self.session_id = None
        self.alive = False
        self._req_id = 0
        self._lock = threading.Lock()
        self._pending = {}         # req_id -> threading.Event
        self._results = {}         # req_id -> msg
        self._text_buf = []        # accumulates text chunks for current prompt
        self._active_req_id = None # req_id of the in-flight session/prompt

        self._proc = subprocess.Popen(
            [opencode_cmd, "acp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        # ACP handshake
        r = self._send_wait("initialize", {
            "protocolVersion": 1,
            "clientInfo": {
                "name": "hermesclaw",
                "title": "HermesClaw",
                "version": "0.3.0",
            },
            "clientCapabilities": {},
        }, timeout=15)
        if not r or "error" in r:
            raise RuntimeError(f"ACP initialize failed: {r}")

        r = self._send_wait("session/new", {
            "cwd": cwd,
            "mcpServers": [],
        }, timeout=15)
        if not r or "error" in r or "result" not in r:
            raise RuntimeError(f"ACP session/new failed: {r}")
        self.session_id = r["result"]["sessionId"]
        self.alive = True

    def _read_loop(self):
        while True:
            try:
                line = self._proc.stdout.readline()
                if not line:
                    break
                line = line.decode().strip()
                if not line:
                    continue
                msg = json.loads(line)
            except Exception:
                break

            if "id" in msg:
                with self._lock:
                    self._results[msg["id"]] = msg
                    ev = self._pending.pop(msg["id"], None)
                if ev:
                    ev.set()
            elif msg.get("method") == "session/update":
                upd = msg.get("params", {}).get("update", {})
                if upd.get("sessionUpdate") == "agent_message_chunk":
                    content = upd.get("content", {})
                    if content.get("type") == "text":
                        with self._lock:
                            if self._active_req_id is not None:
                                self._text_buf.append(content["text"])

        # EOF or error — mark dead and wake all pending waiters so they
        # return immediately instead of blocking until their timeout expires.
        log.warning("ACP read loop exited (subprocess may have died)")
        with self._lock:
            self.alive = False
            dead_error = {"jsonrpc": "2.0", "error": {"code": -32000, "message": "ACP process died"}}
            for req_id, ev in list(self._pending.items()):
                self._results[req_id] = dead_error
                ev.set()
            self._pending.clear()

    def _send_wait(self, method, params, timeout=10):
        with self._lock:
            self._req_id += 1
            req_id = self._req_id
            ev = threading.Event()
            self._pending[req_id] = ev
        try:
            raw = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            self._proc.stdin.write((raw + "\n").encode())
            self._proc.stdin.flush()
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
                self.alive = False
            log.warning("ACP stdin write failed (%s): %s", method, e)
            return {"error": {"code": -32000, "message": f"stdin write failed: {e}"}}
        if ev.wait(timeout):
            with self._lock:
                return self._results.pop(req_id, None)
        log.warning("ACP timeout waiting for %s (id=%d)", method, req_id)
        with self._lock:
            self._pending.pop(req_id, None)
        return None

    def prompt(self, text, timeout=120):
        """Send a text prompt; return accumulated response text."""
        with self._lock:
            self._req_id += 1
            req_id = self._req_id
            self._active_req_id = req_id
            self._text_buf.clear()
            ev = threading.Event()
            self._pending[req_id] = ev
        try:
            raw = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "session/prompt",
                               "params": {"sessionId": self.session_id,
                                          "prompt": [{"type": "text", "text": text}],
                                          "model": self.model}})
            self._proc.stdin.write((raw + "\n").encode())
            self._proc.stdin.flush()
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
                self._active_req_id = None
                self.alive = False
            return f"[OpenCode error: stdin write failed: {e}]"
        if ev.wait(timeout):
            with self._lock:
                self._active_req_id = None
                r = self._results.pop(req_id, None)
                text_out = "".join(self._text_buf).strip()
            if r is None or "error" in (r or {}):
                err = (r or {}).get("error", {}).get("message", "unknown error")
                return f"[OpenCode error: {err}]"
            return text_out
        with self._lock:
            self._active_req_id = None
            self._pending.pop(req_id, None)
        log.warning("ACP timeout on session/prompt (id=%d)", req_id)
        return "[OpenCode: timeout]"

    def close(self):
        self.alive = False
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


class OpenCodeBridge:
    """Manages per-user ACPSession instances for OpenCode integration."""

    def __init__(self, opencode_cmd, model="opencode/minimax-m2.5-free", cwd=None):
        self.opencode_cmd = opencode_cmd
        self.model = model
        self.cwd = cwd or str(Path.home())
        self._sessions = {}   # uid -> ACPSession
        self._lock = threading.Lock()

    def is_available(self):
        """Return True if the opencode binary is found."""
        return bool(shutil.which(self.opencode_cmd) or os.path.isfile(self.opencode_cmd))

    def send(self, uid, text, timeout=120):
        """Route text to this user's ACP session; return response."""
        session = self._get_or_create(uid)
        return session.prompt(text, timeout)

    def _get_or_create(self, uid):
        with self._lock:
            s = self._sessions.get(uid)
            if s is None or not s.alive:
                log.info("Creating OpenCode session for %s", uid[:16])
                s = ACPSession(self.opencode_cmd, self.cwd, self.model)
                self._sessions[uid] = s
        return s

    def close_session(self, uid):
        with self._lock:
            s = self._sessions.pop(uid, None)
        if s:
            s.close()

    def close_all(self):
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s.close()


# ---------------------------------------------------------------------------
# Router commands
# ---------------------------------------------------------------------------


def route_label(r):
    if r == Route.HERMES:
        return "Hermes"
    if r == Route.OPENCLAW:
        return "OpenClaw"
    if r == Route.OPENCODE:
        return "OpenCode"
    if r == Route.BOTH:
        return "Hermes + OpenClaw"
    return "Hermes + OpenClaw + OpenCode"


def cmd(state, uid, text, opencode_bridge=None):
    """Process a slash command.  Returns reply text or None for passthrough."""
    c = text.strip().lower()
    if c == "/hermes":
        state.set(uid, Route.HERMES)
        return "Switched to **Hermes**."
    if c == "/openclaw":
        state.set(uid, Route.OPENCLAW)
        return "Switched to **OpenClaw**."
    if c == "/opencode":
        if opencode_bridge is not None and not opencode_bridge.is_available():
            return (
                "❌ OpenCode is not installed.\n"
                "Please install it with:\n"
                "  npm install -g opencode-ai\n"
                "Then restart HermesClaw and try /opencode again."
            )
        state.set(uid, Route.OPENCODE)
        return "Switched to **OpenCode** 🤖"
    if c == "/both":
        state.set(uid, Route.BOTH)
        return "Switched to **Hermes + OpenClaw**."
    if c == "/three":
        if opencode_bridge is not None and not opencode_bridge.is_available():
            return (
                "❌ OpenCode is not installed — /three requires it.\n"
                "Please install it with:\n"
                "  npm install -g opencode-ai\n"
                "Then restart HermesClaw and try /three again.\n"
                "Tip: use /both for Hermes + OpenClaw only."
            )
        state.set(uid, Route.THREE)
        return "Switched to **Hermes + OpenClaw + OpenCode** 🔱"
    if c == "/whoami":
        route = state.get(uid)
        return (
            f"**HermesClaw v3** by X @AaronYonW\n"
            f"**Current route**: **{route_label(route)}**\n"
            f"**/hermes** → Hermes only\n"
            f"**/openclaw** → OpenClaw only\n"
            f"**/opencode** → OpenCode only\n"
            f"**/both** → Hermes + OpenClaw\n"
            f"**/three** → all three\n"
            f"**/whoami** → this status"
        )
    return None


# ---------------------------------------------------------------------------
# iLink helpers
# ---------------------------------------------------------------------------


def hdrs(tok, body=""):
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode())),
        "iLink-App-Id": "",
        "iLink-App-ClientVersion": ILINK_CV,
        "Authorization": "Bearer " + tok if tok else "",
    }


def ilink_post(base_url, ep, bd, tok, to=30):
    url = base_url.rstrip("/") + "/" + ep.lstrip("/")
    bs = json.dumps(bd)
    r = requests.post(url, headers=hdrs(tok, bs), data=bs.encode(), timeout=to)
    r.raise_for_status()
    return r.json()


def get_updates_real(base_url, tok, buf="", to=None):
    if to is None:
        to = DEFAULT_POLL_SEC
    try:
        return ilink_post(
            base_url,
            "ilink/bot/getupdates",
            {"get_updates_buf": buf, "base_info": {"channel_version": ILINK_VER}},
            tok,
            to + 5,  # HTTP timeout slightly longer than iLink long-poll
        )
    except requests.exceptions.Timeout:
        return {"ret": 0, "msgs": [], "get_updates_buf": buf}
    except Exception as e:
        log.warning("getUpdates: %s", e)
        return {"ret": -1, "msgs": [], "get_updates_buf": buf}


def send_text_ilink(base_url, tok, to_user, text, ctx=None):
    """Send a plain text reply through iLink."""
    m = {
        "from_user_id": "",
        "to_user_id": to_user,
        "client_id": "hc-" + secrets.token_hex(8),
        "message_type": 2,
        "message_state": 2,
        "item_list": [{"type": T, "text_item": {"text": text}}],
    }
    if ctx:
        m["context_token"] = ctx
    return ilink_post(
        base_url,
        "ilink/bot/sendmessage",
        {"msg": m, "base_info": {"channel_version": ILINK_VER}},
        tok,
    )


# ---------------------------------------------------------------------------
# Message queue with event-based long-poll
# ---------------------------------------------------------------------------


class MessageQueue:
    """Thread-safe queue with blocking dequeue for long-poll simulation."""

    def __init__(self, capacity=QUEUE_CAP):
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.msgs = []
        self.capacity = capacity

    def enqueue(self, msg):
        with self.lock:
            if len(self.msgs) >= self.capacity:
                self.msgs.pop(0)
                log.warning("Queue full (%d), dropped oldest", self.capacity)
            self.msgs.append(msg)
        self.event.set()

    def dequeue_all(self, timeout=None):
        """Return all queued messages, blocking up to *timeout* seconds."""
        if timeout is not None and timeout > 0:
            self.event.wait(timeout=timeout)
        with self.lock:
            batch = list(self.msgs)
            self.msgs.clear()
            self.event.clear()
        return batch

    def size(self):
        with self.lock:
            return len(self.msgs)


# ---------------------------------------------------------------------------
# Gateway proxy handler
# ---------------------------------------------------------------------------

PROXY_ALLOWLIST = frozenset([
    "ilink/bot/getupdates",
    "ilink/bot/sendmessage",
    "ilink/bot/getuploadurl",
    "ilink/bot/sendtyping",
    "ilink/bot/getconfig",
    "ilink/bot/get_bot_qrcode",
    "ilink/bot/get_qrcode_status",
])


def make_proxy_handler(queue, ilink_base_url, ilink_token, state, tag):
    """Factory: create a request handler class bound to a specific queue.

    *state* -- State instance for route lookup.
    *tag*   -- e.g. "[Hermes Agent]"; prepended to text items in
    sendmessage when the destination user's route is /both.
    """

    class GatewayProxyHandler(BaseHTTPRequestHandler):

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            ep = urlparse(self.path).path.lstrip("/")

            if ep not in PROXY_ALLOWLIST:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"ret":-1,"errmsg":"not allowed"}')
                log.warning("Proxy blocked: %s", ep)
                return

            try:
                if ep == "ilink/bot/getupdates":
                    self._handle_getupdates(body)
                elif ep == "ilink/bot/sendmessage":
                    self._handle_sendmessage(body)
                else:
                    self._proxy_passthrough(body)
            except BrokenPipeError:
                log.debug("Client disconnected (BrokenPipeError)")
            except ConnectionResetError:
                log.debug("Client disconnected (ConnectionResetError)")

        # -- getupdates: return from queue with long-poll ----------------

        def _handle_getupdates(self, body):
            try:
                bd = json.loads(body) if body else {}
            except Exception:
                bd = {}
            client_buf = bd.get("get_updates_buf", "")

            msgs = queue.dequeue_all(timeout=DEFAULT_POLL_SEC)
            resp = {"ret": 0, "msgs": msgs, "get_updates_buf": client_buf}
            self._write_json(200, resp)
            if msgs:
                log.info("Proxy [%s] getupdates -> %d msgs", tag or "?", len(msgs))

        # -- sendmessage: forward to real iLink with optional tagging ----

        def _handle_sendmessage(self, body):
            try:
                bd = json.loads(body) if body else {}
            except Exception:
                bd = {}

            # Tag text items only in /both mode for attribution.
            if tag:
                msg_obj = bd.get("msg", {})
                to_user = msg_obj.get("to_user_id", "")
                try:
                    route = state.get(to_user) if to_user else Route.HERMES
                except Exception:
                    route = Route.HERMES
                if route in (Route.BOTH, Route.THREE):
                    for item in msg_obj.get("item_list", []):
                        if item.get("type") == T:
                            ti = item.get("text_item", {})
                            original = ti.get("text", "")
                            if original:
                                ti["text"] = f"{tag} {original}"

            self._forward_to_ilink(
                "ilink/bot/sendmessage",
                json.dumps(bd).encode() if bd else body,
            )

        # -- other endpoints: passthrough to real iLink ------------------

        def _proxy_passthrough(self, body):
            ep = urlparse(self.path).path.lstrip("/")
            self._forward_to_ilink(ep, body)

        def _forward_to_ilink(self, ep, body):
            url = ilink_base_url.rstrip("/") + "/" + ep
            try:
                resp = requests.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "AuthorizationType": "ilink_bot_token",
                        "iLink-App-Id": "",
                        "iLink-App-ClientVersion": ILINK_CV,
                        "Authorization": "Bearer " + ilink_token,
                    },
                    data=body,
                    timeout=30,
                )
                self.send_response(resp.status_code)
                for k, v in resp.headers.items():
                    if k.lower() not in (
                        "transfer-encoding", "content-encoding", "connection",
                    ):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.content)
            except BrokenPipeError:
                # Gateway disconnected before we could write back the response.
                # The upstream request already succeeded; nothing to retry.
                log.debug("BrokenPipe on write-back (benign): %s", ep)
            except Exception as e:
                log.error("Proxy forward error: %s", e)
                try:
                    err = json.dumps({"ret": -1, "errmsg": str(e)}).encode()
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
                except BrokenPipeError:
                    log.debug("BrokenPipe writing error response (benign)")


        def _write_json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass  # Suppress default HTTP log noise.

    return GatewayProxyHandler


# ---------------------------------------------------------------------------
# Message routing
# ---------------------------------------------------------------------------


def route_message(uid, msg, route, hermes_q, openclaw_q, opencode_q=None):
    """Enqueue *msg* to the correct proxy queue(s) based on *route*."""
    if route in (Route.HERMES, Route.BOTH, Route.THREE):
        if hermes_q:
            hermes_q.enqueue(msg)
        else:
            log.warning("Hermes queue not available for %s", uid[:16])
    if route in (Route.OPENCLAW, Route.BOTH, Route.THREE):
        if openclaw_q:
            openclaw_q.enqueue(msg)
        else:
            log.warning("OpenClaw queue not available for %s", uid[:16])
    if route in (Route.OPENCODE, Route.THREE):
        if opencode_q is not None:
            opencode_q.put((uid, msg))
        else:
            log.warning("OpenCode queue not available for %s", uid[:16])


def opencode_worker(bridge, q, base_url, token, state):
    """Dedicated thread: dequeue messages and send them to OpenCode."""
    while True:
        try:
            uid, msg = q.get()
            txt = extract_text(msg)
            if not txt:
                continue
            route = state.get(uid)
            try:
                reply = bridge.send(uid, txt)
                if reply:
                    tag = "[OpenCode] " if route == Route.THREE else ""
                    send_text_ilink(
                        base_url, token, uid,
                        tag + reply,
                        msg.get("context_token"),
                    )
            except Exception as e:
                log.error("OpenCode error for %s: %s", uid[:16], e)
                try:
                    send_text_ilink(base_url, token, uid,
                                    f"[OpenCode error: {e}]",
                                    msg.get("context_token"))
                except Exception:
                    pass
        except Exception as e:
            log.error("opencode_worker: %s", e, exc_info=True)


def proc_msg(msg, state, base_url, token, hermes_q, openclaw_q,
             opencode_q=None, opencode_bridge=None):
    """Process one inbound iLink message."""
    uid = msg.get("from_user_id", "")
    ctx = msg.get("context_token", "")
    items = msg.get("item_list", [])
    if msg.get("message_type", 1) != 1:
        return
    log.info("Msg from=%s... items=%d", uid[:16], len(items))

    txt = extract_text(items)
    has_any = txt or any(it.get("type", 0) != 0 for it in items)
    if not has_any:
        return

    # Show status on first contact.
    if state.should_show_status(uid) and not txt.startswith("/"):
        reply = cmd(state, uid, "/whoami", opencode_bridge)
        send_text_ilink(base_url, token, uid, reply, ctx)
        state.mark_status_shown(uid)

    # Slash commands are text-only; never forwarded to gateways.
    if txt.startswith("/"):
        r = cmd(state, uid, txt, opencode_bridge)
        if r:
            send_text_ilink(base_url, token, uid, r, ctx)
            return

    route = state.get(uid)
    route_message(uid, msg, route, hermes_q, openclaw_q, opencode_q)


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

MAX_FAILS = 3
BACKOFF = 30


def poll_loop(base_url, token, state, hermes_q, openclaw_q,
              opencode_q=None, opencode_bridge=None, poll_sec=None):
    if poll_sec is None:
        poll_sec = DEFAULT_POLL_SEC
    buf = ""
    fails = 0
    while True:
        try:
            resp = get_updates_real(base_url, token, buf, poll_sec)
            ret, ec = resp.get("ret"), resp.get("errcode")
            if ret not in (0, None) or ec not in (0, None):
                log.warning("getUpdates err: ret=%s ec=%s", ret, ec)
                fails += 1
                if fails >= MAX_FAILS:
                    time.sleep(BACKOFF)
                    fails = 0
                else:
                    time.sleep(2)
                continue
            fails = 0
            if resp.get("get_updates_buf"):
                buf = resp["get_updates_buf"]
            for m in resp.get("msgs", []):
                try:
                    proc_msg(m, state, base_url, token, hermes_q, openclaw_q,
                             opencode_q, opencode_bridge)
                except Exception as e:
                    log.error("proc: %s", e, exc_info=True)
        except Exception as e:
            log.error("Loop: %s", e, exc_info=True)
            fails += 1
            if fails >= MAX_FAILS:
                time.sleep(BACKOFF)
                fails = 0
            else:
                time.sleep(2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")

    base_url = os.getenv("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com")
    token = os.getenv("ILINK_TOKEN", "")
    hermes_port = int(os.getenv("HERMES_PROXY_PORT", "19998"))
    oc_port = int(os.getenv("OPENCLAW_PROXY_PORT", "19999"))
    state_file = os.getenv("STATE_FILE", str(Path(__file__).parent / "router_state.json"))
    log_file = os.getenv("LOG_FILE", str(Path(__file__).parent / "hermesclaw.log"))
    poll_sec = int(os.getenv("LONG_POLL_TIMEOUT", "35"))
    hermes_on = os.getenv("HERMES_ENABLED", "true").lower() in ("true", "1", "yes")
    oc_on = os.getenv("OPENCLAW_ENABLED", "true").lower() in ("true", "1", "yes")
    opencode_on = os.getenv("OPENCODE_ENABLED", "true").lower() in ("true", "1", "yes")
    opencode_model = os.getenv("OPENCODE_MODEL", "opencode/minimax-m2.5-free")
    opencode_cmd = os.getenv("OPENCODE_CMD", "/home/ubuntu/.npm-global/bin/opencode")
    opencode_cwd = os.getenv("OPENCODE_CWD", str(Path.home()))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )

    if not token:
        log.error("No ILINK_TOKEN set!")
        sys.exit(1)

    state = State(state_file)
    hermes_q = MessageQueue() if hermes_on else None
    oc_q = MessageQueue() if oc_on else None
    opencode_q = None
    opencode_bridge = None

    log.info("=" * 60)
    log.info("HermesClaw v3 -- triple gateway proxy")
    log.info("iLink: %s", base_url)
    log.info("Hermes proxy: :%d (enabled=%s)", hermes_port, hermes_on)
    log.info("OpenClaw proxy: :%d (enabled=%s)", oc_port, oc_on)
    log.info("OpenCode bridge: enabled=%s", opencode_on)
    log.info("Default route: %s", Route.HERMES.value)
    log.info("=" * 60)

    servers = []

    if hermes_on:
        h_handler = make_proxy_handler(
            hermes_q, base_url, token, state, tag="[Hermes Agent]",
        )
        h_srv = ThreadingHTTPServer(("127.0.0.1", hermes_port), h_handler)
        threading.Thread(target=h_srv.serve_forever, daemon=True).start()
        servers.append(h_srv)
        log.info("Hermes proxy started on :%d", hermes_port)

    if oc_on:
        oc_handler = make_proxy_handler(
            oc_q, base_url, token, state, tag="[OpenClaw]",
        )
        oc_srv = ThreadingHTTPServer(("127.0.0.1", oc_port), oc_handler)
        threading.Thread(target=oc_srv.serve_forever, daemon=True).start()
        servers.append(oc_srv)
        log.info("OpenClaw proxy started on :%d", oc_port)

    if opencode_on:
        opencode_bridge = OpenCodeBridge(opencode_cmd, opencode_model, opencode_cwd)
        if opencode_bridge.is_available():
            opencode_q = queue.Queue()
            threading.Thread(
                target=opencode_worker,
                args=(opencode_bridge, opencode_q, base_url, token, state),
                daemon=True,
            ).start()
            log.info("OpenCode bridge ready (model=%s)", opencode_model)
        else:
            log.warning(
                "OpenCode binary not found at %s; /opencode and /three will "
                "prompt users to install it.", opencode_cmd,
            )

    poll_thread = threading.Thread(
        target=poll_loop,
        args=(base_url, token, state, hermes_q, oc_q, opencode_q, opencode_bridge, poll_sec),
        daemon=True,
    )
    poll_thread.start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda s, f: stop.set())
    signal.signal(signal.SIGTERM, lambda s, f: stop.set())

    while not stop.is_set():
        time.sleep(1)

    log.info("Shutting down...")
    if opencode_bridge:
        opencode_bridge.close_all()
    for s in servers:
        s.shutdown()
    log.info("Stopped")


if __name__ == "__main__":
    main()
