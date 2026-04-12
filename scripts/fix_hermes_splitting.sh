#!/bin/bash
# fix_hermes_splitting.sh
# Patches Hermes Agent's weixin.py to stop splitting messages by newlines.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }

echo ""
echo "Hermes Agent WeChat Message Splitting Fix"
echo "=========================================="
echo ""

# Find weixin.py
WEIXIN_PY=""
for candidate in \
    "$HOME/.hermes/hermes-agent/gateway/platforms/weixin.py" \
    "$HOME/.hermes/gateway/platforms/weixin.py"
do
    if [ -f "$candidate" ]; then
        WEIXIN_PY="$candidate"
        break
    fi
done

if [ -z "$WEIXIN_PY" ]; then
    err "Could not find weixin.py in ~/.hermes/"
    echo "Please ensure Hermes Agent is installed."
    exit 1
fi

ok "Found weixin.py: $WEIXIN_PY"

# Check if already patched
if grep -q "Return content as a single unit; splitting is handled by length limits only" "$WEIXIN_PY" 2>/dev/null; then
    ok "Already patched! No changes needed."
    exit 0
fi

# Backup
BACKUP="${WEIXIN_PY}.bak.$(date +%Y%m%d_%H%M%S)"
cp "$WEIXIN_PY" "$BACKUP"
ok "Backup created: $BACKUP"

# Apply patch
python3 - "$WEIXIN_PY" <<'PYTHON'
import sys
from pathlib import Path

weixin_py = Path(sys.argv[1])
content = weixin_py.read_text()

# Patch 1: _split_delivery_units_for_weixin
old_func_1 = '''def _split_delivery_units_for_weixin(content: str) -> List[str]:
    """Split content into delivery units for WeChat.
    
    Weixin can render Markdown, but chat readability is better when
    top-level line breaks become separate messages.
    """'''

new_func_1 = '''def _split_delivery_units_for_weixin(content: str) -> List[str]:
    """Return content as a single unit; splitting is handled by length limits only."""
    return [content] if content.strip() else []'''

# Find the function and replace it (including the body)
import re

# Pattern to match the entire function
pattern_1 = r'def _split_delivery_units_for_weixin\(content: str\) -> List\[str\]:.*?(?=\ndef |\nclass |\Z)'

replacement_1 = '''def _split_delivery_units_for_weixin(content: str) -> List[str]:
    """Return content as a single unit; splitting is handled by length limits only."""
    return [content] if content.strip() else []

'''

content = re.sub(pattern_1, replacement_1, content, flags=re.DOTALL)

# Patch 2: _split_text_for_weixin_delivery
pattern_2 = r'def _split_text_for_weixin_delivery\(content: str, max_length: int\) -> List\[str\]:.*?(?=\ndef |\nclass |\Z)'

replacement_2 = '''def _split_text_for_weixin_delivery(content: str, max_length: int) -> List[str]:
    """Split content only when it exceeds max_length; no newline-based splitting."""
    if len(content) <= max_length:
        return [content]
    return _pack_markdown_blocks_for_weixin(content, max_length) or [content]

'''

content = re.sub(pattern_2, replacement_2, content, flags=re.DOTALL)

weixin_py.write_text(content)
print("Patched successfully")
PYTHON

if [ $? -eq 0 ]; then
    ok "Patch applied successfully"
else
    err "Patch failed"
    echo "Restoring backup..."
    cp "$BACKUP" "$WEIXIN_PY"
    exit 1
fi

echo ""
echo "Next steps:"
echo "  1. Restart Hermes Agent:"
echo "     sudo systemctl restart hermes"
echo "     # or however you run Hermes"
echo ""
echo "  2. Test in WeChat:"
echo "     Send a message and verify Hermes replies with a single message"
echo ""
echo "To revert:"
echo "  cp $BACKUP $WEIXIN_PY"
echo "  sudo systemctl restart hermes"
echo ""
