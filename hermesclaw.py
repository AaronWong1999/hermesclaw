#!/usr/bin/env python3
"""HermesClaw: WeChat router for Hermes and OpenClaw."""

import base64
import hashlib
import json
import logging
import os
import mimetypes
import re
import secrets
import signal
import sys
import tempfile
import threading
import time
from enum import Enum
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

import requests
from Crypto.Cipher import AES
from requests import exceptions as req_exc

# Constants
T, IM, VO, FI, VI = 1, 2, 3, 4, 5
ILINK_VER = "2.1.7"
ILINK_CV = "65547"
MAX_MEDIA = 100 * 1024 * 1024
OC_QUEUE_CAP = 200
AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".wav", ".m4a"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Runtime configuration
ILINK_BASE_URL = ""
ILINK_TOKEN = ""
CDN_BASE_URL = ""
HERMES_API = ""
OC_API = ""
PROXY_PORT = 19999
STATE_FILE = ""
LOG_FILE = ""
POLL_SEC = 35
MAX_FAILS = 3
BACKOFF = 30
HERMES_CONNECT_TIMEOUT = 10
HERMES_READ_TIMEOUT = 600

log = logging.getLogger("hermesclaw")

class B(str, Enum):
    HERMES = "hermes"
    OC = "openclaw"
    BOTH = "both"

class State:
    def __init__(self, fp):
        self.fp = Path(fp)
        self.d = {}
        self.lock = threading.Lock()
        if self.fp.exists():
            try:
                self.d = json.loads(self.fp.read_text())
                log.info("Loaded %d states", len(self.d))
            except Exception:
                pass
    def save(self):
        t = self.fp.with_suffix(".tmp")
        t.write_text(json.dumps(self.d, indent=2, ensure_ascii=False))
        t.replace(self.fp)
    def get(self, uid):
        with self.lock:
            if uid not in self.d:
                self.d[uid] = {"b": B.HERMES.value, "status_shown": False}
            return B(self.d[uid]["b"])
    def should_show_status(self, uid):
        with self.lock:
            if uid not in self.d:
                self.d[uid] = {"b": B.HERMES.value, "status_shown": False}
            return not self.d[uid].get("status_shown", False)
    def mark_status_shown(self, uid):
        with self.lock:
            if uid not in self.d:
                self.d[uid] = {"b": B.HERMES.value, "status_shown": True}
            else:
                self.d[uid]["status_shown"] = True
            self.save()
    def set(self, uid, b):
        with self.lock:
            status_shown = self.d.get(uid, {}).get("status_shown", False)
            self.d[uid] = {"b": b.value, "status_shown": status_shown}
            self.save()
            log.info("User %s -> %s", uid[:16], b.value)
st = None  # Initialized in main(); tests patch this directly.

# Message queue used by openclaw-weixin polling.
oc_msg_queue = {}  # uid -> list of messages
oc_buf_lock = threading.Lock()

def hdrs(tok, body=""):
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode())),
        "iLink-App-Id": "",
        "iLink-App-ClientVersion": ILINK_CV,
        "Authorization": "Bearer " + tok if tok else "",
    }

def ilink_post(ep, bd, tok, to=30):
    url = ILINK_BASE_URL.rstrip("/") + "/" + ep.lstrip("/")
    bs = json.dumps(bd)
    r = requests.post(url, headers=hdrs(tok, bs), data=bs.encode(), timeout=to)
    r.raise_for_status()
    return r.json()

def get_typing_ticket(tok, user_id):
    try:
        resp = ilink_post("ilink/bot/getconfig",
            {"ilink_user_id": user_id, "base_info": {"channel_version": ILINK_VER}}, tok, 10)
        return resp.get("typing_ticket", "")
    except Exception as e:
        log.debug("get_typing_ticket failed: %s", e)
        return ""

def get_updates_real(tok, buf="", to=None):
    if to is None:
        to = POLL_SEC
    try:
        return ilink_post("ilink/bot/getupdates",
            {"get_updates_buf": buf, "base_info": {"channel_version": ILINK_VER}}, tok, to)
    except requests.exceptions.Timeout:
        return {"ret": 0, "msgs": [], "get_updates_buf": buf}
    except Exception as e:
        log.warning("getUpdates: %s", e)
        return {"ret": -1, "msgs": [], "get_updates_buf": buf}

def send_msg_ilink(tok, to, items, ctx=None):
    m = {"from_user_id": "", "to_user_id": to, "client_id": "hc-" + secrets.token_hex(8),
         "message_type": 2, "message_state": 2, "item_list": items}
    if ctx: m["context_token"] = ctx
    return ilink_post("ilink/bot/sendmessage",
        {"msg": m, "base_info": {"channel_version": ILINK_VER}}, tok)

def typing_ilink(tok, uid, tkt, status=1):
    try:
        ilink_post("ilink/bot/sendtyping",
            {"ilink_user_id": uid, "typing_ticket": tkt, "status": status,
             "base_info": {"channel_version": ILINK_VER}}, tok, 10)
    except Exception as e:
        log.debug("typing: %s", e)

def parse_ak(b64):
    d = base64.b64decode(b64)
    if len(d) == 16: return d
    if len(d) == 32:
        try: return bytes.fromhex(d.decode())
        except Exception: pass
    raise ValueError("bad aes key len=%d" % len(d))

def aes_dec(ct, k):
    c = AES.new(k, AES.MODE_ECB)
    pt = c.decrypt(ct)
    pad = pt[-1]
    if pad < 1 or pad > 16 or pt[-pad:] != bytes([pad]) * pad:
        raise ValueError("bad PKCS#7 padding")
    return pt[:-pad]

def aes_enc(pt, k):
    p = 16 - (len(pt) % 16); return AES.new(k, AES.MODE_ECB).encrypt(pt + bytes([p]*p))

def dl_dec(eqp, ak64, fu=None):
    k = parse_ak(ak64)
    url = fu or (CDN_BASE_URL.rstrip("/") + "/" + eqp.lstrip("?") if CDN_BASE_URL else None)
    if not url: raise ValueError("no cdn url")
    r = requests.get(url, timeout=60); r.raise_for_status()
    return aes_dec(r.content, k)

def upload_cdn(data, mtl):
    fk = "hc-" + secrets.token_hex(12)
    mt = {"image":1,"video":2,"file":3,"voice":4}.get(mtl, 3)
    ak = secrets.token_bytes(16); ak64 = base64.b64encode(ak).decode()
    ct = aes_enc(data, ak)
    ur = ilink_post("ilink/bot/getuploadurl",
        {"filekey":fk,"media_type":mt,"to_user_id":"","rawsize":len(data),
         "rawfilemd5":hashlib.md5(data).hexdigest(),"filesize":len(ct),"aeskey":ak64,
         "thumb_rawsize":0,"thumb_rawfilemd5":"","thumb_filesize":0,"no_need_thumb":True,
         "base_info":{"channel_version":ILINK_VER}}, ILINK_TOKEN)
    uu = ur.get("upload_full_url","")
    if not uu: raise RuntimeError("no upload url")
    r = requests.post(uu, headers={"Content-Type":"application/octet-stream"}, data=ct, timeout=60)
    r.raise_for_status()
    dp = r.headers.get("x-encrypted-param","")
    if not dp: raise RuntimeError("no x-encrypted-param")
    return {"download_param":dp,"aes_key":ak64,"filekey":fk}

def extract_text(items):
    """Extract text from iLink items.

    Voice items contribute only their iLink transcription by design.
    """
    txs = []
    for it in items:
        tp = it.get("type", 0)
        if tp == T:
            x = it.get("text_item", {}).get("text", "")
            if x: txs.append(x)
        elif tp == VO:
            x = it.get("voice_item", {}).get("text", "")
            if x:
                txs.append(f'[The user sent a voice message. Here\'s what they said: "{x}"]')
    return "\n".join(txs).strip()

def extract_media(items):
    """Download + decrypt first media item. Returns (path, label) or (None, None)."""
    other_dir = Path(__file__).parent
    other_dir.mkdir(parents=True, exist_ok=True)
    for it in items:
        tp = it.get("type", 0)
        if tp == IM:
            ig = it.get("image_item", {}); md = ig.get("media", {})
            ak = base64.b64encode(bytes.fromhex(ig.get("aeskey", ""))).decode() if ig.get("aeskey") else md.get("aes_key", "")
            eq, fu = md.get("encrypt_query_param", ""), md.get("full_url", "")
            if ak and (eq or fu):
                try:
                    d = dl_dec(eq, ak, fu)
                    if len(d) <= MAX_MEDIA:
                        suffix = mimetypes.guess_extension(ig.get("content_type", "")) or ".jpg"
                        f = tempfile.NamedTemporaryFile(dir=other_dir, suffix=suffix, delete=False, prefix="img_")
                        f.write(d); f.close()
                        return f.name, "image"
                except Exception as e: log.error("img dl: %s", e)
        elif tp == VI:
            vi = it.get("video_item", {}); md = vi.get("media", {})
            ak, eq, fu = md.get("aes_key", ""), md.get("encrypt_query_param", ""), md.get("full_url", "")
            if ak and (eq or fu):
                try:
                    d = dl_dec(eq, ak, fu)
                    if len(d) <= MAX_MEDIA:
                        f = tempfile.NamedTemporaryFile(dir=other_dir, suffix=".mp4", delete=False, prefix="wx_")
                        f.write(d); f.close()
                        return f.name, "video"
                except Exception as e: log.error("vid dl: %s", e)
        elif tp == FI:
            fi = it.get("file_item", {}); md = fi.get("media", {})
            ak, eq, fu = md.get("aes_key", ""), md.get("encrypt_query_param", ""), md.get("full_url", "")
            fn = Path(fi.get("file_name", "file")).name
            suffix = Path(fn).suffix or ".bin"
            if ak and (eq or fu):
                try:
                    d = dl_dec(eq, ak, fu)
                    if len(d) <= MAX_MEDIA:
                        prefix = "doc_" + Path(fn).stem[:32] + "_"
                        f = tempfile.NamedTemporaryFile(dir=other_dir, suffix=suffix, delete=False, prefix=prefix)
                        f.write(d); f.close()
                        return f.name, "file"
                except Exception as e: log.error("file dl: %s", e)
    return None, None

def has_media_items(items):
    """Check if message contains media items (without downloading)."""
    return any(it.get("type", 0) in (IM, VI, FI) for it in items)

def reply_wx(to, txt, ctx=None):
    if txt:
        send_msg_ilink(ILINK_TOKEN, to, [{"type":T,"text_item":{"text":txt}}], ctx)
        log.info("WX reply -> %s... len=%d", to[:16], len(txt))

def reply_wx_media(to, txt, media_path, media_label, ctx=None, emit_error_text=False):
    items = []
    if txt:
        items.append({"type":T, "text_item":{"text":txt}})
    if media_path:
        try:
            data = Path(media_path).read_bytes()
            cdn = upload_cdn(data, media_label or "file")
            fn = Path(media_path).name
            if media_label == "image":
                items.append({"type":IM, "image_item":{
                    "media":{"encrypt_query_param":cdn["download_param"],"aes_key":cdn["aes_key"],"encrypt_type":1},
                    "mid_size":len(data)}})
            elif media_label == "video":
                items.append({"type":VI, "video_item":{
                    "media":{"encrypt_query_param":cdn["download_param"],"aes_key":cdn["aes_key"],"encrypt_type":1},
                    "video_size":len(data)}})
            elif media_label == "voice":
                items.append({"type":VO, "voice_item":{
                    "media":{"encrypt_query_param":cdn["download_param"],"aes_key":cdn["aes_key"],"encrypt_type":1},
                    "encode_type":1}})
            else:
                items.append({"type":FI, "file_item":{
                    "media":{"encrypt_query_param":cdn["download_param"],"aes_key":cdn["aes_key"],"encrypt_type":1},
                    "file_name":fn,"len":str(len(data))}})
            log.info("WX media reply -> %s... %s", to[:16], media_label)
        except Exception as e:
            log.error("Media upload failed: %s", e)
            if emit_error_text:
                items.append({"type":T, "text_item":{"text":"Media upload failed: " + str(e)[:100]}})
    if items:
        send_msg_ilink(ILINK_TOKEN, to, items, ctx)
        return True
    return False

def route_label(backend):
    if backend == B.HERMES:
        return "Hermes Agent"
    if backend == B.OC:
        return "OpenClaw"
    return "Both Hermes Agent & OpenClaw"

def hermes_health_summary(timeout=2):
    """Return a short Hermes health status string for user-facing diagnostics."""
    if not HERMES_API:
        return "not configured"
    try:
        resp = requests.get(HERMES_API.rstrip("/") + "/health", timeout=timeout)
        if resp.ok:
            return "reachable"
        return f"error ({resp.status_code})"
    except Exception:
        return "unreachable"


def build_hermes_error_message(exc, elapsed):
    """Convert Hermes request exceptions into concise user-facing text."""
    seconds = max(1, int(round(elapsed)))
    if isinstance(exc, req_exc.ReadTimeout):
        return f"Hermes timed out after {seconds}s."
    if isinstance(exc, req_exc.ConnectTimeout):
        return "Hermes connection timed out."
    if isinstance(exc, req_exc.ConnectionError):
        return "Hermes is unreachable."
    if isinstance(exc, req_exc.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", "unknown")
        if status == 413:
            return "Hermes rejected the media payload as too large."
        if status == 500:
            return "Hermes failed to process this media payload."
        return f"Hermes returned HTTP {status}."
    return "Hermes request failed."


def extract_hermes_media(content):
    """Extract MEDIA:<path> tags and [[audio_as_voice]] markers from Hermes text."""
    if not content:
        return [], ""
    cleaned = content
    media = []
    has_voice_tag = "[[audio_as_voice]]" in content
    cleaned = cleaned.replace("[[audio_as_voice]]", "")
    media_re = re.compile(
        r'''[`"']?MEDIA:\s*(?P<path>`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|(?:~/|/)\S+(?:[^\S\n]+\S+)*|\S+)[`"']?'''
    )
    for match in media_re.finditer(content):
        path = match.group("path").strip()
        if len(path) >= 2 and path[0] == path[-1] and path[0] in "`\"'":
            path = path[1:-1].strip()
        path = path.lstrip("`\"'").rstrip("`\"',.;:)}]")
        if path:
            media.append((os.path.expanduser(path), has_voice_tag))
    if media:
        cleaned = media_re.sub("", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return media, cleaned


def extract_hermes_local_files(content):
    """Extract bare local file paths, excluding code spans."""
    if not content:
        return [], ""
    ext_part = r"[A-Za-z0-9]{1,8}"
    path_re = re.compile(
        r'(?<![/:\w.])(?:~/|/)(?:[\w .\-]+/)*[\w .\-]+\.' + ext_part + r'\b'
    )
    code_spans = []
    for match in re.finditer(r"```[^\n]*\n.*?```", content, re.DOTALL):
        code_spans.append((match.start(), match.end()))
    for match in re.finditer(r"`[^`\n]+`", content):
        code_spans.append((match.start(), match.end()))

    def in_code(pos):
        return any(start <= pos < end for start, end in code_spans)

    found = []
    for match in path_re.finditer(content):
        if in_code(match.start()):
            continue
        raw = match.group(0)
        expanded = os.path.expanduser(raw)
        if os.path.isfile(expanded):
            found.append((raw, expanded))

    cleaned = content
    extracted = []
    seen = set()
    for raw, expanded in found:
        if expanded in seen:
            continue
        seen.add(expanded)
        extracted.append(expanded)
        cleaned = cleaned.replace(raw, "")
    if extracted:
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return extracted, cleaned


def classify_hermes_media(path, is_voice=False):
    ext = Path(path).suffix.lower()
    if is_voice or ext in AUDIO_EXTS:
        return "voice"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "file"


def parse_hermes_response(content):
    """Turn Hermes response text into visible text + native WeChat attachments."""
    media_files, cleaned = extract_hermes_media(content)
    local_files, cleaned = extract_hermes_local_files(cleaned)
    attachments = []
    seen = set()
    for path, is_voice in media_files:
        if not os.path.isfile(path) or path in seen:
            continue
        seen.add(path)
        attachments.append((path, classify_hermes_media(path, is_voice)))
    for path in local_files:
        if path in seen:
            continue
        seen.add(path)
        attachments.append((path, classify_hermes_media(path, False)))
    return cleaned.strip(), attachments


def send_hermes_reply(uid, content, ctx=None, text_prefix=None):
    visible_text, attachments = parse_hermes_response(content)
    if visible_text and text_prefix:
        visible_text = f"{text_prefix}\n{visible_text}"
    sent_any = False
    if visible_text:
        reply_wx(uid, visible_text, ctx)
        sent_any = True
    for media_path, media_label in attachments:
        sent_any = reply_wx_media(uid, None, media_path, media_label, ctx) or sent_any
    if not sent_any and content:
        fallback = f"{text_prefix}\n{content}" if text_prefix else content
        reply_wx(uid, fallback, ctx)


def call_hermes(uid, txt, media_path=None, media_label=None):
    ct = txt
    if media_path and media_label:
        media_file = Path(media_path)
        if media_label == "image":
            ct = (
                (txt + "\n\n" if txt else "")
                + "[The user sent an image. Use vision tools if needed.]\n\n"
                + f"[image_url: {media_path}]"
            )
        elif media_label == "file" and media_file.suffix.lower() in {".txt", ".md"}:
            try:
                body = media_file.read_text(errors="replace")
                body = body[:100 * 1024]
                ct = (
                    f"[Content of {media_file.name}]:\n{body}"
                    + (f"\n\n{txt}" if txt else "")
                )
            except Exception:
                ct = (
                    (txt + "\n\n" if txt else "")
                    + f"[The user sent a file: {media_file.name}]\n"
                    + f"[file_path: {media_path}]"
                )
        else:
            ct = (
                (txt + "\n\n" if txt else "")
                + f"[The user sent a {media_label}: {media_file.name}]\n"
                + f"[file_path: {media_path}]"
            )
    session_id = "wx-" + uid
    started = time.monotonic()
    try:
        r = requests.post(HERMES_API.rstrip("/")+"/v1/chat/completions",
            headers={"Authorization":"Bearer dummy","Content-Type":"application/json",
                     "X-Hermes-Session-Id": session_id},
            json={"model":"hermes-agent","messages":[{"role":"user","content":ct}]},
            timeout=(HERMES_CONNECT_TIMEOUT, HERMES_READ_TIMEOUT))
        r.raise_for_status()
        elapsed = time.monotonic() - started
        usage = r.json().get("usage", {})
        log.info(
            "Hermes reply in %.2fs for %s session=%s prompt_tokens=%s",
            elapsed, uid[:16], session_id, usage.get("prompt_tokens", "?"),
        )
        return r.json().get("choices",[{}])[0].get("message",{}).get("content","(no reply)")
    except Exception as e:
        elapsed = time.monotonic() - started
        log.error("Hermes request failed after %.2fs for %s session=%s: %s", elapsed, uid[:16], session_id, e)
        return f"Hermes error: {build_hermes_error_message(e, elapsed)}"

def cmd(uid, c):
    c = c.strip().lower()
    if c == "/hermes":
        st.set(uid, B.HERMES); return "Switched to Hermes."
    if c == "/openclaw":
        st.set(uid, B.OC); return "Switched to OpenClaw."
    if c == "/both":
        st.set(uid, B.BOTH); return "Switched to both mode."
    if c == "/whoami":
        backend = st.get(uid)
        return (
            f"HermesClaw\n"
            f"Current route: {route_label(backend)}\n"
            f"/openclaw -> switch to OpenClaw\n"
            f"/hermes -> switch to Hermes Agent\n"
            f"/both -> both reply\n"
            f"/whoami -> show current status\n"
            f"All other messages are passed through to the active agent."
        )
    return None  # Pass through non-router commands.

def keep_typing(uid, tkt, stop_event):
    while not stop_event.is_set():
        typing_ilink(ILINK_TOKEN, uid, tkt, 1)
        stop_event.wait(4)

def queue_oc_msg(uid, msg):
    """Queue a message for openclaw-weixin, with global cap."""
    with oc_buf_lock:
        total = sum(len(v) for v in oc_msg_queue.values())
        if total >= OC_QUEUE_CAP:
            for k in list(oc_msg_queue.keys()):
                if oc_msg_queue[k]:
                    oc_msg_queue[k].pop(0)
                    if not oc_msg_queue[k]:
                        del oc_msg_queue[k]
                    log.warning("OC queue full (%d), dropped oldest from %s", OC_QUEUE_CAP, k[:16])
                    break
        if uid not in oc_msg_queue:
            oc_msg_queue[uid] = []
        oc_msg_queue[uid].append(msg)
    log.info("Msg queued for openclaw-weixin: %s", uid[:16])

def proc_msg(msg):
    uid = msg.get("from_user_id", "")
    ctx = msg.get("context_token", "")
    items = msg.get("item_list", [])
    if msg.get("message_type", 1) != 1: return
    log.info("Msg from=%s... n=%d", uid[:16], len(items))

    txt = extract_text(items)
    has_media = has_media_items(items)
    if not txt and not has_media: return

    if st.should_show_status(uid) and not (txt.startswith("/") and not has_media):
        reply_wx(uid, cmd(uid, "/whoami"), ctx)
        st.mark_status_shown(uid)

    # Router commands are text-only.
    if txt.startswith("/") and not has_media:
        r = cmd(uid, txt)
        if r: reply_wx(uid, r, ctx); return

    be = st.get(uid)
    tkt = msg.get("typing_ticket", "") or get_typing_ticket(ILINK_TOKEN, uid)
    typing_stop = threading.Event()
    if tkt:
        t = threading.Thread(target=keep_typing, args=(uid, tkt, typing_stop), daemon=True)
        t.start()

    if be == B.HERMES:
        mp, ml = extract_media(items)
        mt = txt
        if mp: mt = (txt + "\n\n[Attachment: " + ml + "]") if txt else "[Attachment: " + ml + "]"
        res = call_hermes(uid, mt, mp, ml)
        typing_stop.set()
        if mp and Path(mp).exists():
            try: Path(mp).unlink()
            except Exception: pass
        send_hermes_reply(uid, res, ctx)

    elif be == B.OC:
        # Do not download media in OpenClaw mode; openclaw-weixin handles CDN access.
        typing_stop.set()
        queue_oc_msg(uid, msg)

    elif be == B.BOTH:
        mp, ml = extract_media(items)
        mt = txt
        if mp: mt = (txt + "\n\n[Attachment: " + ml + "]") if txt else "[Attachment: " + ml + "]"
        def do_hermes():
            res = call_hermes(uid, mt, mp, ml)
            typing_stop.set()
            if mp and Path(mp).exists():
                try: Path(mp).unlink()
                except Exception: pass
            send_hermes_reply(uid, res, ctx, text_prefix="[Hermes Agent]")
        queue_oc_msg(uid, msg)
        t = threading.Thread(target=do_hermes)
        t.start()

PROXY_ALLOWLIST = frozenset([
    "ilink/bot/getupdates",
    "ilink/bot/sendmessage",
    "ilink/bot/getuploadurl",
    "ilink/bot/sendtyping",
    "ilink/bot/getconfig",
    "ilink/bot/get_bot_qrcode",
    "ilink/bot/get_qrcode_status",
])

class ILinkProxyHandler(BaseHTTPRequestHandler):
    """Proxy selected iLink endpoints for openclaw-weixin."""

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b''
        ep = urlparse(self.path).path.lstrip("/")

        if ep not in PROXY_ALLOWLIST:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"ret":-1,"errmsg":"not allowed"}')
            log.warning("Proxy blocked: %s", ep)
            return

        if ep == "ilink/bot/getupdates":
            self._handle_getupdates(body)
        else:
            self._proxy_to_ilink(body)

    def _handle_getupdates(self, body):
        try:
            bd = json.loads(body) if body else {}
            oc_buf = bd.get("get_updates_buf", "")
            with oc_buf_lock:
                all_msgs = []
                for uid_msgs in oc_msg_queue.values():
                    all_msgs.extend(uid_msgs)
                oc_msg_queue.clear()
            resp = {"ret": 0, "msgs": all_msgs, "get_updates_buf": oc_buf}
            resp_bytes = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            if all_msgs:
                log.info("Proxy getUpdates -> %d msgs", len(all_msgs))
        except Exception as e:
            log.error("Proxy getUpdates error: %s", e)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"ret":0,"msgs":[],"get_updates_buf":""}).encode())

    def _proxy_to_ilink(self, body):
        try:
            resp = requests.post(
                ILINK_BASE_URL.rstrip("/") + "/" + urlparse(self.path).path.lstrip("/"),
                headers={"Content-Type":"application/json",
                         "AuthorizationType":"ilink_bot_token",
                         "iLink-App-Id":"", "iLink-App-ClientVersion":ILINK_CV,
                         "Authorization": self.headers.get("Authorization","")},
                data=body, timeout=30
            )
            resp_bytes = resp.content
            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() not in ('transfer-encoding', 'content-encoding'):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp_bytes)
        except Exception as e:
            log.error("Proxy error: %s", e)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b'{"ret":-1}')

    def log_message(self, format, *args):
        pass

def poll_loop():
    buf = ""; fails = 0
    while True:
        try:
            resp = get_updates_real(ILINK_TOKEN, buf, POLL_SEC)
            ret, ec = resp.get("ret"), resp.get("errcode")
            if ret not in (0, None) or ec not in (0, None):
                log.warning("getUpdates err: ret=%s ec=%s", ret, ec)
                fails += 1
                if fails >= MAX_FAILS: time.sleep(BACKOFF); fails = 0
                else: time.sleep(2)
                continue
            fails = 0
            if resp.get("get_updates_buf"): buf = resp["get_updates_buf"]
            for m in resp.get("msgs", []):
                try: proc_msg(m)
                except Exception as e: log.error("proc: %s", e, exc_info=True)
        except Exception as e:
            log.error("Loop: %s", e, exc_info=True)
            fails += 1
            if fails >= MAX_FAILS: time.sleep(BACKOFF); fails = 0
            else: time.sleep(2)

def main():
    global ILINK_BASE_URL, ILINK_TOKEN, CDN_BASE_URL, HERMES_API, OC_API
    global PROXY_PORT, STATE_FILE, LOG_FILE, POLL_SEC, st
    global HERMES_CONNECT_TIMEOUT, HERMES_READ_TIMEOUT

    ILINK_BASE_URL = os.getenv("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com")
    ILINK_TOKEN = os.getenv("ILINK_TOKEN", "")
    CDN_BASE_URL = os.getenv("CDN_BASE_URL", "")
    HERMES_API = os.getenv("HERMES_API_URL", "http://127.0.0.1:8642")
    OC_API = os.getenv("OPENCLAW_API_URL", "http://127.0.0.1:18789")
    PROXY_PORT = int(os.getenv("PROXY_PORT", "19999"))
    STATE_FILE = os.getenv("STATE_FILE", str(Path(__file__).parent / "router_state.json"))
    LOG_FILE = os.getenv("LOG_FILE", str(Path(__file__).parent / "hermesclaw.log"))
    POLL_SEC = int(os.getenv("LONG_POLL_TIMEOUT", "35"))
    HERMES_CONNECT_TIMEOUT = int(os.getenv("HERMES_CONNECT_TIMEOUT", "10"))
    HERMES_READ_TIMEOUT = int(os.getenv("HERMES_READ_TIMEOUT", "600"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )

    if not ILINK_TOKEN: log.error("No ILINK_TOKEN!"); sys.exit(1)
    st = State(STATE_FILE)

    log.info("=" * 60)
    log.info("HermesClaw")
    log.info("iLink: %s", ILINK_BASE_URL)
    log.info("Hermes API: %s", HERMES_API)
    log.info("OpenClaw proxy: :%d", PROXY_PORT)
    log.info("Default: %s", B.HERMES.value)
    log.info("=" * 60)

    proxy_server = HTTPServer(("127.0.0.1", PROXY_PORT), ILinkProxyHandler)
    proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_thread.start()
    log.info("iLink proxy started on :%d", PROXY_PORT)

    poll_thread = threading.Thread(target=poll_loop, daemon=True)
    poll_thread.start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda s, f: stop.set())
    signal.signal(signal.SIGTERM, lambda s, f: stop.set())

    while not stop.is_set():
        time.sleep(1)

    log.info("Shutting down...")
    proxy_server.shutdown()
    log.info("Stopped")

if __name__ == "__main__":
    main()
