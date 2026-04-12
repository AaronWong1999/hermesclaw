# HermesClaw Troubleshooting Guide

This document covers common issues and their solutions.

---

## Issue 1: Hermes Agent splits long messages into multiple WeChat messages

### Symptoms
- Hermes Agent sends one long response as multiple separate WeChat messages
- Each paragraph or line break becomes a new message
- Your WeChat interface gets flooded with many messages at once
- OpenClaw and Telegram adapter don't have this problem

### Root Cause
This is **intentional behavior** in Hermes Agent's WeChat adapter (`gateway/platforms/weixin.py`). The `_split_text_for_weixin_delivery` function splits messages by newlines to "improve chat readability" according to the code comments.

### Solution
Modify `~/.hermes/hermes-agent/gateway/platforms/weixin.py`:

**1. Backup the file first:**
```bash
cp ~/.hermes/hermes-agent/gateway/platforms/weixin.py \
   ~/.hermes/hermes-agent/gateway/platforms/weixin.py.bak
```

**2. Find and replace `_split_delivery_units_for_weixin`:**

**Before:**
```python
def _split_delivery_units_for_weixin(content: str) -> List[str]:
    """Split content into delivery units for WeChat.
    
    Weixin can render Markdown, but chat readability is better when
    top-level line breaks become separate messages.
    """
    # ... complex splitting logic by paragraphs/newlines ...
```

**After:**
```python
def _split_delivery_units_for_weixin(content: str) -> List[str]:
    """Return content as a single unit; splitting is handled by length limits only."""
    return [content] if content.strip() else []
```

**3. Find and replace `_split_text_for_weixin_delivery`:**

**Before:**
```python
def _split_text_for_weixin_delivery(content: str, max_length: int) -> List[str]:
    if len(content) <= max_length and "\n" not in content:
        return [content]
    # ... splits by newlines even for short messages ...
```

**After:**
```python
def _split_text_for_weixin_delivery(content: str, max_length: int) -> List[str]:
    """Split content only when it exceeds max_length; no newline-based splitting."""
    if len(content) <= max_length:
        return [content]
    return _pack_markdown_blocks_for_weixin(content, max_length) or [content]
```

**4. Restart Hermes Agent:**
```bash
sudo systemctl restart hermes  # or however you run Hermes
```

**5. Test:**
Send a message to your WeChat bot and verify that Hermes replies with a single message (unless it exceeds 4000 characters).

### Notes
- This modification keeps the length-based splitting (4000 character limit) but removes newline-based splitting
- Long messages (>4000 chars) will still be split, but only by length, not by paragraphs
- This change is local to your installation and will be overwritten if you reinstall Hermes Agent

---

## Issue 2: OpenClaw sends "[OpenClaw] ⚠️ 📝 Edit: in ~/.openclaw/openclaw.json failed"

### Symptoms
- OpenClaw repeatedly sends error messages about failing to edit `openclaw.json`
- The error appears as a WeChat message from OpenClaw
- File permissions look correct (0600, owned by the same user running openclaw)

### Root Cause
This is **not a permissions issue**. The error message is OpenClaw's own notification that it failed to write to its config file, likely due to:

1. **Invalid configuration values** — OpenClaw tries to write back config but encounters validation errors
2. **Concurrent write conflicts** — Both `openclaw` and `openclaw-gateway` processes try to write simultaneously
3. **Invalid model/provider configuration** — Deprecated or unknown providers (e.g., `manifest/auto`) cause repeated failures

### Solution

**1. Check OpenClaw logs:**
```bash
journalctl -u openclaw -n 200 --no-pager
# or
systemctl --user status openclaw
```

**2. Inspect the config file:**
```bash
cat ~/.openclaw/openclaw.json | python3 -m json.tool
```

**3. Common fixes:**

**Fix A: Remove invalid model providers**

If you see errors about `manifest/auto` or unknown providers:

```bash
# Backup first
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak

# Edit the file and remove:
# - "manifest/auto" from models list
# - "models.providers.manifest" section
# - "channels.manifest" section
# - Invalid fields in "auth.profiles" (e.g., baseUrl, apiKey for manifest)
```

**Fix B: Set a valid primary model**

Ensure `agents.defaults.model.primary` points to a working model:

```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "openrouter/openai/gpt-oss-120b:free"
      }
    }
  }
}
```

**Fix C: Restart OpenClaw**

```bash
systemctl --user restart openclaw
# or
sudo systemctl restart openclaw  # if running as system service
```

**4. Verify the fix:**

Check logs again — you should see:
- No more "Edit failed" errors
- "openclaw-weixin plugin loaded successfully"
- "Gateway started on port XXXXX"

### Prevention
- Don't manually edit `openclaw.json` while OpenClaw is running
- Use OpenClaw's built-in config commands when possible
- Keep backups before making config changes

---

## Issue 3: "BrokenPipeError" in hermesclaw logs

### Symptoms
```
BrokenPipe on write-back (benign): ilink/bot/sendmessage
```

### Root Cause
The gateway client disconnected before HermesClaw could write back the response. This happens when:
- Network hiccups between gateway and proxy
- Gateway timeout (e.g., Hermes has a 15s timeout)
- The upstream iLink request **already succeeded** — the message was sent

### Solution
**This is benign and requires no action.** The message was successfully sent to WeChat; only the response write-back failed. HermesClaw logs this at DEBUG level.

If you see this frequently:
1. Check network stability between gateway and HermesClaw
2. Ensure gateways aren't timing out too aggressively
3. Verify HermesClaw is responding quickly (check CPU/memory)

---

## Issue 4: One gateway gets 403 errors

### Symptoms
- One gateway (Hermes or OpenClaw) logs 403 errors from iLink
- Messages are dropped or delayed
- The other gateway works fine

### Root Cause
The gateway is trying to connect directly to iLink instead of through HermesClaw's proxy.

### Solution

**For OpenClaw:**
```bash
# Check openclaw-weixin account file
cat ~/.openclaw/state/openclaw-weixin/accounts/*.json

# Should show:
# "baseUrl": "http://127.0.0.1:19999"

# If not, run the installer again or manually patch:
python3 << 'EOF'
import json
from pathlib import Path
for f in Path.home().glob(".openclaw/**/openclaw-weixin/accounts/*.json"):
    data = json.loads(f.read_text())
    data["baseUrl"] = "http://127.0.0.1:19999"
    f.write_text(json.dumps(data, indent=2))
EOF
```

**For Hermes:**
```bash
# Check Hermes .env
cat ~/.hermes/.env | grep WEIXIN_BASE_URL

# Should show:
# WEIXIN_BASE_URL=http://127.0.0.1:19998

# If not, add it:
echo "WEIXIN_BASE_URL=http://127.0.0.1:19998" >> ~/.hermes/.env
```

**Restart both gateways:**
```bash
sudo systemctl restart hermes
systemctl --user restart openclaw
```

---

## Issue 5: HermesClaw service won't start

### Symptoms
```bash
sudo systemctl status hermesclaw
# Shows: failed (code=exited, status=1)
```

### Solution

**1. Check logs:**
```bash
journalctl -u hermesclaw -n 50 --no-pager
```

**2. Common causes:**

**Missing ILINK_TOKEN:**
```bash
# Check .env file
cat ~/hermesclaw/.env | grep ILINK_TOKEN

# If empty, extract from gateway account file:
python3 << 'EOF'
import json
from pathlib import Path
for f in Path.home().glob(".openclaw/**/openclaw-weixin/accounts/*.json"):
    token = json.loads(f.read_text()).get("token")
    if token:
        print(f"ILINK_TOKEN={token}")
        break
EOF

# Add to .env
```

**Missing Python dependencies:**
```bash
pip3 install --user requests python-dotenv
# or
pip3 install --user --break-system-packages requests python-dotenv
```

**Port already in use:**
```bash
# Check if ports 19998/19999 are occupied
sudo lsof -i :19998
sudo lsof -i :19999

# If occupied, change ports in ~/hermesclaw/.env:
# HERMES_PROXY_PORT=29998
# OPENCLAW_PROXY_PORT=29999
```

**3. Restart:**
```bash
sudo systemctl restart hermesclaw
```

---

## Getting Help

If you encounter issues not covered here:

1. **Check logs:**
   ```bash
   journalctl -u hermesclaw -f --no-pager
   journalctl -u hermes -f --no-pager
   journalctl -u openclaw -f --no-pager
   ```

2. **Verify service status:**
   ```bash
   sudo systemctl status hermesclaw
   sudo systemctl status hermes
   systemctl --user status openclaw
   ```

3. **Test WeChat commands:**
   - Send `/whoami` — should show current route
   - Send `/hermes` — should switch to Hermes
   - Send `/openclaw` — should switch to OpenClaw

4. **Open a GitHub issue:**
   - Include relevant log excerpts
   - Describe what you expected vs. what happened
   - Mention your OS and Python version

---

## Debugging Tips

### Enable verbose logging

Edit `~/hermesclaw/hermesclaw.py`:

```python
logging.basicConfig(
    level=logging.DEBUG,  # Change from INFO to DEBUG
    # ...
)
```

Restart: `sudo systemctl restart hermesclaw`

### Monitor all services simultaneously

```bash
# Terminal 1
journalctl -u hermesclaw -f --no-pager

# Terminal 2
journalctl -u hermes -f --no-pager

# Terminal 3
journalctl -u openclaw -f --no-pager
```

### Test proxy connectivity

```bash
# Test Hermes proxy
curl -X POST http://127.0.0.1:19998/ilink/bot/getconfig \
  -H "Content-Type: application/json" \
  -d '{"base_info":{"channel_version":"2.1.7"}}'

# Test OpenClaw proxy
curl -X POST http://127.0.0.1:19999/ilink/bot/getconfig \
  -H "Content-Type: application/json" \
  -d '{"base_info":{"channel_version":"2.1.7"}}'
```

Both should return JSON responses (not 404 or connection refused).

---

## Known Limitations

1. **Voice messages** — HermesClaw forwards iLink's transcription text, not the raw audio. Each gateway receives the transcription as text.

2. **Media in /both mode** — Images/videos are forwarded to both gateways, so both agents see the same media. This may cause duplicate processing.

3. **Hermes Telegram cron jobs** — If you have Telegram cron jobs configured in Hermes, ensure `chat_id` is a numeric ID, not a username like `@Aaron0x10`. Hermes will log `ValueError: invalid literal for int()` otherwise.

4. **OpenClaw model requirements** — Some models (e.g., `openrouter/openai/gpt-oss-120b:free`) require reasoning mode. OpenClaw handles this automatically but may log warnings.

---

## Changelog of Fixes

### 2026-04-12
- **Fixed:** Hermes Agent splitting long messages by newlines (modified `weixin.py`)
- **Fixed:** OpenClaw "Edit failed" errors (cleaned up invalid config in `openclaw.json`)
- **Documented:** BrokenPipeError is benign (upstream request already succeeded)
