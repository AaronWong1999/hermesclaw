#!/bin/bash
set -euo pipefail

# ── HermesClaw v3 installer ──────────────────────────────────────────────
# Detects Hermes Agent gateway + OpenClaw gateway + OpenCode CLI, configures
# all three to connect through HermesClaw's triple proxy/bridge, and installs
# the systemd service.

REPO_URL="${HERMESCLAW_REPO_URL:-https://github.com/AaronWong1999/hermesclaw.git}"
PROJECT_DIR="${HERMESCLAW_DIR:-${HOME}/hermesclaw}"
SERVICE_NAME="hermesclaw"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${PROJECT_DIR}/.env"
APP_FILE="${PROJECT_DIR}/hermesclaw.py"
HERMES_PROXY_PORT="${HERMES_PROXY_PORT:-19998}"
OPENCLAW_PROXY_PORT="${OPENCLAW_PROXY_PORT:-19999}"
# Set HERMESCLAW_YES=1 (or pass -y) to skip interactive confirmation prompts.
AUTO_YES="${HERMESCLAW_YES:-0}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}OK${NC}  $1"; }
warn() { echo -e "${YELLOW}WARN${NC} $1"; }
err()  { echo -e "${RED}ERR${NC}  $1"; }
info() { echo -e "${CYAN}INFO${NC} $1"; }

echo ""
echo -e "${CYAN}HermesClaw v3 installer${NC}"
echo -e "${CYAN}Triple-proxy gateway router for Hermes + OpenClaw + OpenCode on WeChat${NC}"
echo ""

# ── Helpers ───────────────────────────────────────────────────────────────

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        err "Missing required command: $1"
        exit 1
    }
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

need_cmd python3
need_cmd git

# Parse -y / --yes flag
for arg in "$@"; do
    case "$arg" in
        -y|--yes) AUTO_YES=1 ;;
    esac
done

# ── Bootstrap repo ────────────────────────────────────────────────────────

bootstrap_repo_if_needed() {
    if [ -f "${APP_FILE}" ] && [ -f "${PROJECT_DIR}/README.md" ]; then
        info "Repo already present — pulling latest changes."
        git -C "${PROJECT_DIR}" pull --ff-only 2>&1 | sed 's/^/  /' || \
            warn "git pull failed (local changes?). Continuing with existing code."
        return 0
    fi
    info "Cloning HermesClaw into ${PROJECT_DIR}."
    rm -rf "${PROJECT_DIR}"
    git clone "${REPO_URL}" "${PROJECT_DIR}"
}

# ── .env reader ───────────────────────────────────────────────────────────

read_env_value() {
    local key="$1" file="$2"
    [ -f "$file" ] || return 0
    python3 - "$key" "$file" <<'PY'
import pathlib, sys
key, path = sys.argv[1], pathlib.Path(sys.argv[2])
for raw in path.read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    lhs, rhs = line.split("=", 1)
    if lhs.strip() == key:
        print(rhs.strip())
        break
PY
}

# ── OpenClaw gateway detection ────────────────────────────────────────────

discover_oc_accounts_dirs() {
    local dirs=()
    for candidate in \
        "${HOME}/.openclaw/state/openclaw-weixin/accounts" \
        "${HOME}/.openclaw/openclaw-weixin/accounts" \
        "${HOME}/.config/openclaw/openclaw-weixin/accounts" \
        "${HOME}/openclaw-weixin/accounts"
    do
        [ -d "$candidate" ] && dirs+=("$candidate")
    done
    while IFS= read -r found; do
        [ -n "$found" ] && dirs+=("$found")
    done < <(find "${HOME}" -maxdepth 5 -type d -path "*/openclaw-weixin/accounts" 2>/dev/null || true)
    printf '%s\n' "${dirs[@]}" | awk 'NF && !seen[$0]++'
}

discover_oc_account_files() {
    local dir
    for dir in "$@"; do
        find "$dir" -maxdepth 1 -type f -name "*.json" \
            ! -name "*.context-tokens.json" \
            ! -name "*.sync.json" 2>/dev/null
    done | awk 'NF && !seen[$0]++'
}

scan_oc_accounts() {
    OC_ACCOUNT_DIRS=()
    OC_ACCOUNT_FILES=()
    mapfile -t OC_ACCOUNT_DIRS < <(discover_oc_accounts_dirs)
    if [ "${#OC_ACCOUNT_DIRS[@]}" -gt 0 ]; then
        mapfile -t OC_ACCOUNT_FILES < <(discover_oc_account_files "${OC_ACCOUNT_DIRS[@]}")
    fi
}

extract_json_field() {
    python3 - "$1" "$2" <<'PY'
import json, pathlib, sys
path, key = pathlib.Path(sys.argv[1]), sys.argv[2]
try:
    print(json.loads(path.read_text()).get(key, ""))
except Exception:
    pass
PY
}

# ── Hermes gateway detection ─────────────────────────────────────────────

discover_hermes_weixin_accounts() {
    local dirs=()
    for candidate in \
        "${HOME}/.hermes/weixin/accounts" \
        "${HOME}/.hermes/hermes-agent/weixin/accounts"
    do
        [ -d "$candidate" ] && dirs+=("$candidate")
    done
    while IFS= read -r found; do
        [ -n "$found" ] && dirs+=("$found")
    done < <(find "${HOME}/.hermes" -maxdepth 4 -type d -name accounts -path "*/weixin/*" 2>/dev/null || true)
    local files=()
    for d in "${dirs[@]}"; do
        while IFS= read -r f; do
            files+=("$f")
        done < <(find "$d" -maxdepth 1 -name "*.json" -type f 2>/dev/null)
    done
    printf '%s\n' "${files[@]}" | awk 'NF && !seen[$0]++'
}

detect_hermes_env_file() {
    for candidate in \
        "${HOME}/.hermes/.env" \
        "${HOME}/.hermes/hermes-agent/.env"
    do
        [ -f "$candidate" ] && { echo "$candidate"; return 0; }
    done
    return 1
}

# ── Token extraction (from OC or Hermes account files) ────────────────────

extract_first_token() {
    local file
    for file in "$@"; do
        local tok
        tok="$(extract_json_field "$file" "token")"
        if [ -n "$tok" ]; then
            echo "$tok"
            return 0
        fi
    done
}

# ── Patching ──────────────────────────────────────────────────────────────

patch_oc_account_file() {
    local file="$1" proxy_url="$2"
    python3 - "$file" "$proxy_url" <<'PY'
import json, pathlib, sys
path, proxy_url = pathlib.Path(sys.argv[1]), sys.argv[2]
data = json.loads(path.read_text())
changed = False
for key in ("baseUrl", "base_url", "apiBaseUrl", "serverUrl"):
    if key in data and data[key] != proxy_url:
        data[key] = proxy_url
        changed = True
if "baseUrl" not in data:
    data["baseUrl"] = proxy_url
    changed = True
if changed:
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        bak.write_text(path.read_text())
    path.write_text(json.dumps(data, indent=2) + "\n")
    print("patched")
else:
    print("unchanged")
PY
}

patch_hermes_env_base_url() {
    local env_file="$1" proxy_url="$2"
    python3 - "$env_file" "$proxy_url" <<'PY'
import pathlib, sys
path, proxy_url = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = path.read_text().splitlines()
found = False
out = []
for line in lines:
    stripped = line.strip()
    if stripped.startswith("WEIXIN_BASE_URL="):
        out.append(f"WEIXIN_BASE_URL={proxy_url}")
        found = True
    else:
        out.append(line)
if not found:
    out.append(f"WEIXIN_BASE_URL={proxy_url}")
bak = path.with_suffix(".bak")
if not bak.exists():
    bak.write_text(path.read_text())
path.write_text("\n".join(out) + "\n")
print("patched" if found else "added")
PY
}

# ── Write .env ────────────────────────────────────────────────────────────

write_env_file() {
    local token="$1" hermes_on="$2" oc_on="$3"
    cat > "$ENV_FILE" <<EOF
ILINK_BASE_URL=https://ilinkai.weixin.qq.com
ILINK_TOKEN=${token}
HERMES_PROXY_PORT=${HERMES_PROXY_PORT}
OPENCLAW_PROXY_PORT=${OPENCLAW_PROXY_PORT}
HERMES_ENABLED=${hermes_on}
OPENCLAW_ENABLED=${oc_on}
OPENCODE_ENABLED=${OPENCODE_ENABLED}
OPENCODE_CMD=${OPENCODE_CMD}
OPENCODE_MODEL=${OPENCODE_MODEL}
STATE_FILE=${PROJECT_DIR}/router_state.json
LOG_FILE=${PROJECT_DIR}/hermesclaw.log
LONG_POLL_TIMEOUT=35
EOF
    chmod 600 "$ENV_FILE"
}

# ── Python deps ───────────────────────────────────────────────────────────

install_python_deps() {
    info "Installing Python dependencies (requests, python-dotenv)."
    pip3 install --user -q requests python-dotenv 2>/dev/null || \
    pip3 install --user --break-system-packages -q requests python-dotenv 2>/dev/null || \
    sudo pip3 install -q requests python-dotenv
    ok "Python dependencies ready."
}

# ── systemd ───────────────────────────────────────────────────────────────

install_systemd_service() {
    [ "$(uname)" = "Linux" ] || {
        warn "systemd not available. Run manually: python3 ${APP_FILE}"
        return 0
    }
    need_cmd systemctl
    info "Installing ${SERVICE_NAME}.service."
    cat > /tmp/hermesclaw.service <<EOF
[Unit]
Description=HermesClaw v3 Triple-Proxy Router
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=$(command -v python3) ${APP_FILE}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${PROJECT_DIR}

[Install]
WantedBy=multi-user.target
EOF
    sudo cp /tmp/hermesclaw.service "${SERVICE_FILE}"
    rm -f /tmp/hermesclaw.service
    sudo systemctl daemon-reload
    sudo systemctl enable "${SERVICE_NAME}" >/dev/null
    sudo systemctl restart "${SERVICE_NAME}"
    sudo systemctl is-active "${SERVICE_NAME}" >/dev/null 2>&1 || {
        err "${SERVICE_NAME}.service failed to start."
        echo "Run: journalctl -u ${SERVICE_NAME} --no-pager -n 100"
        exit 1
    }
    ok "${SERVICE_NAME}.service is running."
}

# ── AI-assisted install/uninstall prompt ──────────────────────────────────

print_ai_prompt() {
    cat <<'EOF'

AI-assisted install prompt (paste this to an AI agent):
  Run: curl -fsSL https://raw.githubusercontent.com/AaronWong1999/hermesclaw/main/install.sh | bash

  Then read README.md and install.sh from the installed HermesClaw directory.
  Detect whether Hermes Agent, OpenClaw, their WeChat gateways, python3,
  pip3, and systemd are present.  At least one gateway must be configured.
  Extract the iLink token from the first available gateway account file.
  Patch openclaw-weixin baseUrl to http://127.0.0.1:19999 and Hermes
  WEIXIN_BASE_URL to http://127.0.0.1:19998.  Install dependencies and
  systemd service.

  Also detect OpenCode CLI: run `command -v opencode` or check
  ~/.npm-global/bin/opencode.  If not found, suggest installing with:
    npm install -g opencode-ai
  Set OPENCODE_CMD in .env to the detected path (or empty if not found).
  OPENCODE_MODEL defaults to opencode/minimax-m2.5-free (free, no API key).

  If Hermes Agent WeChat gateway is detected, accept the installer's offer
  to patch weixin.py to disable newline-based message splitting
  (recommended) — or run fix_hermes_splitting.sh manually after install.

  Restart gateways.  Verify /whoami works in WeChat.

AI-assisted uninstall prompt:
  Stop and disable the hermesclaw systemd service.  Restore
  openclaw-weixin account .bak files.  Remove WEIXIN_BASE_URL
  override from ~/.hermes/.env (or restore .bak).  Optionally
  restore weixin.py from its .bak if the message-splitting fix
  was applied.  Optionally remove ~/hermesclaw directory.
EOF
}

# ══════════════════════════════════════════════════════════════════════════
# Main flow
# ══════════════════════════════════════════════════════════════════════════

bootstrap_repo_if_needed

# 1) Detect gateways.
HAS_OC_GW=false
HAS_HERMES_GW=false
HAS_OPENCLAW=false
HAS_HERMES=false

# OpenClaw presence.
if command_exists openclaw || [ -d "${HOME}/.openclaw" ]; then
    HAS_OPENCLAW=true
fi

# OpenClaw gateway (clawbot / openclaw-weixin).
scan_oc_accounts
[ "${#OC_ACCOUNT_FILES[@]}" -gt 0 ] && HAS_OC_GW=true

# Hermes presence.
if command_exists hermes || [ -d "${HOME}/.hermes" ]; then
    HAS_HERMES=true
fi

# Hermes WeChat gateway.
HERMES_WX_FILES=()
mapfile -t HERMES_WX_FILES < <(discover_hermes_weixin_accounts)
[ "${#HERMES_WX_FILES[@]}" -gt 0 ] && HAS_HERMES_GW=true

HERMES_ENV_FILE=""
HERMES_ENV_FILE="$(detect_hermes_env_file 2>/dev/null || true)"

# ── OpenCode detection ────────────────────────────────────────────────────
if command -v opencode &>/dev/null || [ -f "$HOME/.npm-global/bin/opencode" ]; then
    OPENCODE_CMD=$(command -v opencode 2>/dev/null || echo "$HOME/.npm-global/bin/opencode")
    info "OpenCode found: $OPENCODE_CMD"
    OPENCODE_ENABLED=true
else
    warn "OpenCode not found; /opencode and /three will not work until installed."
    warn "To install: npm install -g opencode-ai"
    OPENCODE_ENABLED=false
    OPENCODE_CMD=""
fi

OPENCODE_MODEL="${OPENCODE_MODEL:-opencode/minimax-m2.5-free}"

# 2) Gate: at least one gateway must be configured.
if ! ${HAS_OC_GW} && ! ${HAS_HERMES_GW}; then
    err "No WeChat gateway configured."
    echo ""
    echo "HermesClaw requires at least one of:"
    echo "  - OpenClaw clawbot (openclaw-weixin) with an account file"
    echo "  - Hermes Agent WeChat gateway with an account file"
    echo ""
    echo "Install them first, then rerun this script."
    print_ai_prompt
    exit 1
fi

# 3) Find iLink token.
ILINK_TOKEN_VALUE="${ILINK_TOKEN:-$(read_env_value ILINK_TOKEN "$ENV_FILE" 2>/dev/null || true)}"
if [ -z "$ILINK_TOKEN_VALUE" ] && [ "${#OC_ACCOUNT_FILES[@]}" -gt 0 ]; then
    ILINK_TOKEN_VALUE="$(extract_first_token "${OC_ACCOUNT_FILES[@]}" || true)"
fi
if [ -z "$ILINK_TOKEN_VALUE" ] && [ "${#HERMES_WX_FILES[@]}" -gt 0 ]; then
    ILINK_TOKEN_VALUE="$(extract_first_token "${HERMES_WX_FILES[@]}" || true)"
fi

if [ -z "$ILINK_TOKEN_VALUE" ]; then
    err "Could not find iLink token from gateway account files or .env."
    print_ai_prompt
    exit 1
fi

# 4) Summary.
echo "Discovery summary"
echo "  Hermes Agent:    ${HAS_HERMES}"
echo "  Hermes WX GW:    ${HAS_HERMES_GW}  (${#HERMES_WX_FILES[@]} account files)"
echo "  OpenClaw:        ${HAS_OPENCLAW}"
echo "  OpenClaw WX GW:  ${HAS_OC_GW}  (${#OC_ACCOUNT_FILES[@]} account files)"
echo "  OpenCode:        ${OPENCODE_ENABLED}  (${OPENCODE_CMD:-not found})"
echo "  iLink token:     ${ILINK_TOKEN_VALUE:0:16}..."
echo "  Hermes proxy:    :${HERMES_PROXY_PORT}"
echo "  OpenClaw proxy:  :${OPENCLAW_PROXY_PORT}"
if [ -n "$HERMES_ENV_FILE" ]; then
    echo "  Hermes .env:     ${HERMES_ENV_FILE}"
fi
echo ""

if ! ${HAS_OC_GW}; then
    warn "OpenClaw gateway not found. OpenClaw routing will be disabled."
fi
if ! ${HAS_HERMES_GW}; then
    warn "Hermes gateway not found. Hermes routing will be disabled."
fi

if [[ "$AUTO_YES" == "1" ]]; then
    info "Auto-yes mode — skipping confirmation."
else
    read -r -p "Continue with installation? [Y/n] " REPLY
    if [[ "${REPLY:-Y}" =~ ^[Nn]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

# 5) Install deps.
install_python_deps

# 6) Write HermesClaw .env.
write_env_file "$ILINK_TOKEN_VALUE" "${HAS_HERMES_GW}" "${HAS_OC_GW}"
ok "Wrote ${ENV_FILE}"

# 7) Patch OpenClaw gateway -> proxy A.
if ${HAS_OC_GW}; then
    info "Patching openclaw-weixin to use proxy :${OPENCLAW_PROXY_PORT}"
    for f in "${OC_ACCOUNT_FILES[@]}"; do
        result="$(patch_oc_account_file "$f" "http://127.0.0.1:${OPENCLAW_PROXY_PORT}")"
        ok "  ${f}: ${result}"
    done
fi

# 8) Patch Hermes gateway -> proxy B.
if ${HAS_HERMES_GW} && [ -n "$HERMES_ENV_FILE" ]; then
    info "Patching Hermes WEIXIN_BASE_URL to use proxy :${HERMES_PROXY_PORT}"
    result="$(patch_hermes_env_base_url "$HERMES_ENV_FILE" "http://127.0.0.1:${HERMES_PROXY_PORT}")"
    ok "  ${HERMES_ENV_FILE}: ${result}"
elif ${HAS_HERMES_GW}; then
    warn "Could not find Hermes .env file to patch WEIXIN_BASE_URL."
    echo "  Manually set WEIXIN_BASE_URL=http://127.0.0.1:${HERMES_PROXY_PORT} in your Hermes config."
fi

# 8.5) Optional: Fix Hermes Agent newline-based message splitting.
if ${HAS_HERMES_GW}; then
    echo ""
    echo -e "${CYAN}Hermes Agent message splitting fix${NC}"
    echo "By default, Hermes Agent's WeChat adapter splits long messages by newlines,"
    echo "sending each paragraph as a separate WeChat message. This can flood your chat."
    echo ""
    echo "We can patch weixin.py to keep messages as single units (split by length only)."
    echo -e "${YELLOW}推荐 (Recommended): Apply this fix.${NC}"
    if [[ "$AUTO_YES" == "1" ]]; then
        APPLY_SPLIT_FIX="Y"
        info "Auto-yes: applying Hermes message splitting fix."
    else
        read -r -p "Apply Hermes message splitting fix? [Y/n] " APPLY_SPLIT_FIX
    fi
    if [[ "${APPLY_SPLIT_FIX:-Y}" =~ ^[Yy]$ ]] || [[ "${APPLY_SPLIT_FIX:-}" == "" ]]; then
        info "Applying Hermes Agent message splitting fix..."
        FIX_SCRIPT="${PROJECT_DIR}/fix_hermes_splitting.sh"
        if [ -f "$FIX_SCRIPT" ]; then
            bash "$FIX_SCRIPT" && ok "Message splitting fix applied. Restart Hermes to take effect." || warn "Patch failed, see TROUBLESHOOTING.md for manual fix."
        else
            warn "Fix script not found at ${FIX_SCRIPT}. See TROUBLESHOOTING.md for manual fix."
        fi
    else
        info "Skipped. You can apply this fix later: bash fix_hermes_splitting.sh"
    fi
fi

# 9) OpenClaw media symlink workaround.
#    OpenClaw saves inbound media to ~/.openclaw/media/ but its reply system
#    reads from ~/.openclaw/workspace/media/.  Bridge with a symlink.
if ${HAS_OC_GW}; then
    OC_MEDIA_SRC="${HOME}/.openclaw/media"
    OC_MEDIA_DST="${HOME}/.openclaw/workspace/media"
    if [ -d "$OC_MEDIA_SRC" ]; then
        if [ -L "$OC_MEDIA_DST" ]; then
            ok "OpenClaw media symlink already exists."
        elif [ -d "$OC_MEDIA_DST" ]; then
            # Real directory — move contents into source and replace with symlink
            warn "Replacing ${OC_MEDIA_DST} directory with symlink to ${OC_MEDIA_SRC}"
            cp -rn "${OC_MEDIA_DST}/." "${OC_MEDIA_SRC}/" 2>/dev/null || true
            rm -rf "${OC_MEDIA_DST}"
            ln -s "${OC_MEDIA_SRC}" "${OC_MEDIA_DST}"
            ok "OpenClaw media symlink created."
        else
            mkdir -p "$(dirname "$OC_MEDIA_DST")"
            ln -s "${OC_MEDIA_SRC}" "${OC_MEDIA_DST}"
            ok "OpenClaw media symlink created."
        fi
    else
        warn "OpenClaw media source dir not found (${OC_MEDIA_SRC}). Symlink skipped — will be created on first media receive."
    fi
fi

# 10) systemd service.
install_systemd_service

echo ""
echo -e "${GREEN}Installation complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Restart your gateways so they pick up the new config:"
if ${HAS_HERMES_GW}; then
    echo "     sudo systemctl restart hermes-gateway  (or however you run it)"
fi
if ${HAS_OC_GW}; then
    echo "     systemctl --user restart openclaw  (or however you run it)"
fi
echo "  2. Send /whoami in WeChat to verify routing"
echo "  3. Logs: journalctl -u hermesclaw -f --no-pager"
echo ""
print_ai_prompt
