#!/bin/bash
set -euo pipefail

REPO_URL="${HERMESCLAW_REPO_URL:-https://github.com/AaronWong1999/hermesclaw.git}"
PROJECT_DIR="${HERMESCLAW_DIR:-${HOME}/hermesclaw}"
SERVICE_NAME="hermesclaw"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${PROJECT_DIR}/.env"
APP_FILE="${PROJECT_DIR}/hermesclaw.py"
DEFAULT_PROXY_PORT="${PROXY_PORT:-19999}"
CLAWBOT_INSTALL_CMD="npx -y @tencent-weixin/openclaw-weixin-cli@latest install"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok() { echo -e "${GREEN}OK${NC}  $1"; }
warn() { echo -e "${YELLOW}WARN${NC} $1"; }
err() { echo -e "${RED}ERR${NC}  $1"; }
info() { echo -e "${CYAN}INFO${NC} $1"; }

echo ""
echo -e "${CYAN}HermesClaw installer${NC}"
echo -e "${CYAN}One-command setup for Hermes + OpenClaw WeChat routing${NC}"
echo ""

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        err "Missing required command: $1"
        exit 1
    }
}

need_cmd python3
need_cmd curl
need_cmd git

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

bootstrap_repo_if_needed() {
    if [ -f "${APP_FILE}" ] && [ -f "${PROJECT_DIR}/README.md" ]; then
        return 0
    fi
    info "Bootstrapping HermesClaw repository into ${PROJECT_DIR}."
    rm -rf "${PROJECT_DIR}"
    git clone "${REPO_URL}" "${PROJECT_DIR}"
}

read_env_value() {
    local key="$1"
    local file="$2"
    [ -f "$file" ] || return 0
    python3 - "$key" "$file" <<'PY'
import pathlib
import sys

key = sys.argv[1]
path = pathlib.Path(sys.argv[2])
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

discover_accounts_dirs() {
    local dirs=()
    if [ -n "${OPENCLAW_WEIXIN_ACCOUNTS_DIR:-}" ] && [ -d "${OPENCLAW_WEIXIN_ACCOUNTS_DIR}" ]; then
        dirs+=("${OPENCLAW_WEIXIN_ACCOUNTS_DIR}")
    fi
    for candidate in \
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

discover_account_files() {
    local dir
    for dir in "$@"; do
        find "$dir" -maxdepth 1 -type f -name "*.json" \
            ! -name "*.context-tokens.json" \
            ! -name "*.sync.json" 2>/dev/null
    done | awk 'NF && !seen[$0]++'
}

scan_accounts() {
    ACCOUNT_DIRS=()
    ACCOUNT_FILES=()
    mapfile -t ACCOUNT_DIRS < <(discover_accounts_dirs)
    if [ "${#ACCOUNT_DIRS[@]}" -gt 0 ]; then
        mapfile -t ACCOUNT_FILES < <(discover_account_files "${ACCOUNT_DIRS[@]}")
    fi
}

extract_first_token() {
    local file
    for file in "$@"; do
        python3 - "$file" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    value = json.loads(path.read_text()).get("token", "")
except Exception:
    value = ""
if value:
    print(value)
PY
    done | awk 'NF { print; exit }'
}

extract_baseurl_report() {
    local file
    for file in "$@"; do
        python3 - "$file" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
except Exception:
    raise SystemExit(0)
for key in ("baseUrl", "base_url", "apiBaseUrl", "serverUrl"):
    value = data.get(key)
    if value:
        print(f"{path}:{key}={value}")
PY
    done
}

install_clawbot_if_needed() {
    scan_accounts
    if [ "${#ACCOUNT_FILES[@]}" -gt 0 ]; then
        ok "Detected existing clawbot/openclaw-weixin account files."
        return 0
    fi

    need_cmd npx
    info "No clawbot account config found. Installing clawbot first."
    eval "${CLAWBOT_INSTALL_CMD}"
    scan_accounts
    if [ "${#ACCOUNT_FILES[@]}" -eq 0 ]; then
        err "Clawbot install did not produce any account files."
        echo "Run the clawbot login flow first, then rerun install.sh."
        exit 1
    fi
    ok "Clawbot install completed and account files were found."
}

detect_openclaw_present() {
    if command_exists openclaw; then
        return 0
    fi
    [ -d "${HOME}/.openclaw" ]
}

detect_hermes_present() {
    if command_exists hermes; then
        return 0
    fi
    curl -fsS -m 2 "http://127.0.0.1:8642/health" >/dev/null 2>&1
}

detect_hermes_url() {
    local from_env
    from_env="${HERMES_API_URL:-$(read_env_value HERMES_API_URL "$ENV_FILE")}"
    if [ -n "$from_env" ] && curl -fsS -m 2 "${from_env%/}/health" >/dev/null 2>&1; then
        echo "$from_env"
        return 0
    fi
    if curl -fsS -m 2 "http://127.0.0.1:8642/health" >/dev/null 2>&1; then
        echo "http://127.0.0.1:8642"
        return 0
    fi
    echo "${from_env:-http://127.0.0.1:8642}"
}

detect_openclaw_url() {
    local from_env
    from_env="${OPENCLAW_API_URL:-$(read_env_value OPENCLAW_API_URL "$ENV_FILE")}"
    if [ -n "$from_env" ] && curl -fsS -m 2 "$from_env" >/dev/null 2>&1; then
        echo "$from_env"
        return 0
    fi
    if curl -fsS -m 2 "http://127.0.0.1:18789" >/dev/null 2>&1; then
        echo "http://127.0.0.1:18789"
        return 0
    fi
    echo "${from_env:-http://127.0.0.1:18789}"
}

patch_account_file() {
    local file="$1"
    local proxy_url="$2"
    python3 - "$file" "$proxy_url" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
proxy_url = sys.argv[2]
data = json.loads(path.read_text())
changed = False
for key in ("baseUrl", "base_url", "apiBaseUrl", "serverUrl"):
    if key in data and data.get(key) != proxy_url:
        data[key] = proxy_url
        changed = True
if "baseUrl" not in data:
    data["baseUrl"] = proxy_url
    changed = True
if changed:
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_text(path.read_text())
    path.write_text(json.dumps(data, indent=2) + "\n")
    print("patched")
else:
    print("unchanged")
PY
}

write_env_file() {
    local token="$1"
    cat > "$ENV_FILE" <<EOF
ILINK_BASE_URL=https://ilinkai.weixin.qq.com
ILINK_TOKEN=${token}
CDN_BASE_URL=
HERMES_API_URL=${HERMES_URL}
OPENCLAW_API_URL=${OPENCLAW_URL}
PROXY_PORT=${PROXY_PORT}
STATE_FILE=${PROJECT_DIR}/router_state.json
LOG_FILE=${PROJECT_DIR}/hermesclaw.log
LONG_POLL_TIMEOUT=35
HERMES_CONNECT_TIMEOUT=10
HERMES_READ_TIMEOUT=600
EOF
    chmod 600 "$ENV_FILE"
}

install_python_deps() {
    info "Installing Python dependencies."
    pip3 install --user -q pycryptodome requests python-dotenv 2>/dev/null || \
    pip3 install --user --break-system-packages -q pycryptodome requests python-dotenv 2>/dev/null || \
    sudo pip3 install -q pycryptodome requests python-dotenv
    ok "Python dependencies are ready."
}

install_systemd_service() {
    [ "$(uname)" = "Linux" ] || {
        warn "systemd is not available. Run: python3 ${APP_FILE}"
        return 0
    }
    need_cmd systemctl
    info "Installing ${SERVICE_NAME}.service."
    cat > /tmp/hermesclaw.service <<EOF
[Unit]
Description=HermesClaw Bridge
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
print_ai_fallback() {
    cat <<'EOF'
AI fallback prompt:
Read README.md and install.sh in this repository. Detect whether Hermes, OpenClaw, clawbot/openclaw-weixin, python3, pip3, node, npx, and systemd are installed. Reuse existing config when found. If clawbot is missing, install it. If Hermes or OpenClaw is missing, stop and print exact next actions instead of guessing. Patch clawbot to point to HermesClaw only after clawbot works. Keep iterating until /whoami works in WeChat.
EOF
}

bootstrap_repo_if_needed

scan_accounts
HAS_OPENCLAW=false
HAS_HERMES=false
HAS_CLAWBOT=false
HAS_NPX=false
command_exists npx && HAS_NPX=true
detect_openclaw_present && HAS_OPENCLAW=true || true
detect_hermes_present && HAS_HERMES=true || true
[ "${#ACCOUNT_FILES[@]}" -gt 0 ] && HAS_CLAWBOT=true

if ! ${HAS_CLAWBOT}; then
    if ${HAS_NPX}; then
        warn "No clawbot config was found."
        install_clawbot_if_needed
        HAS_CLAWBOT=true
    else
        err "clawbot/openclaw-weixin is missing and npx is not available."
        print_ai_fallback
        exit 1
    fi
fi

HERMES_URL="$(detect_hermes_url)"
OPENCLAW_URL="$(detect_openclaw_url)"
PROXY_PORT="${DEFAULT_PROXY_PORT}"
PROXY_URL="http://127.0.0.1:${PROXY_PORT}"
ILINK_TOKEN_VALUE="${ILINK_TOKEN:-$(read_env_value ILINK_TOKEN "$ENV_FILE")}"
if [ -z "$ILINK_TOKEN_VALUE" ] && [ "${#ACCOUNT_FILES[@]}" -gt 0 ]; then
    ILINK_TOKEN_VALUE="$(extract_first_token "${ACCOUNT_FILES[@]}")"
fi

echo "Discovery summary"
echo "  App:         ${APP_FILE}"
echo "  python3:     yes"
echo "  npx:         ${HAS_NPX}"
echo "  Hermes:      ${HAS_HERMES}"
echo "  OpenClaw:    ${HAS_OPENCLAW}"
echo "  clawbot:     ${HAS_CLAWBOT}"
echo "  Hermes API:  ${HERMES_URL}"
echo "  OpenClaw:    ${OPENCLAW_URL}"
echo "  Proxy:       ${PROXY_URL}"
echo "  Accounts:    ${#ACCOUNT_FILES[@]}"
if [ "${#ACCOUNT_FILES[@]}" -gt 0 ]; then
    extract_baseurl_report "${ACCOUNT_FILES[@]}" | sed 's/^/  Plugin:      /'
fi
echo ""

if [ -z "$ILINK_TOKEN_VALUE" ]; then
    err "Could not discover ILINK_TOKEN from .env or clawbot account files."
    print_ai_fallback
    exit 1
fi

if ! ${HAS_HERMES}; then
    err "Hermes was not detected."
    echo "Set HERMES_API_URL to a working Hermes endpoint or install Hermes first."
    print_ai_fallback
    exit 1
fi

if ! ${HAS_OPENCLAW}; then
    warn "OpenClaw was not detected. Hermes-only routing can still work, but /openclaw and /both will not be useful until OpenClaw is installed."
fi

if [ "${#ACCOUNT_FILES[@]}" -eq 0 ]; then
    err "No clawbot account files were found after detection."
    print_ai_fallback
    exit 1
fi

read -r -p "Continue? [Y/n] " REPLY
if [[ "${REPLY:-Y}" =~ ^[Nn]$ ]]; then
    echo "Aborted."
    exit 0
fi

install_python_deps
write_env_file "$ILINK_TOKEN_VALUE"
ok "Wrote ${ENV_FILE}."

info "Patching clawbot config to point at HermesClaw."
for account_file in "${ACCOUNT_FILES[@]}"; do
    result="$(patch_account_file "$account_file" "$PROXY_URL")"
    ok "${account_file}: ${result}"
done

install_systemd_service

echo ""
echo "Next steps"
echo "  1. In WeChat, send: /whoami"
echo "  2. If needed, inspect: systemctl status hermesclaw"
echo "  3. If needed, inspect: journalctl -u hermesclaw -n 100 --no-pager"
echo ""
