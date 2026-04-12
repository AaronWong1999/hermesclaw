#!/bin/bash
# fix_hermes_splitting.sh
# Patches Hermes Agent's weixin.py to stop splitting messages by newlines.

set -euo pipefail

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
    echo "Error: Could not find weixin.py"
    exit 1
fi

if grep -q "Return content as a single unit" "$WEIXIN_PY" 2>/dev/null; then
    echo "Already patched. No changes needed."
    exit 0
fi

BACKUP="${WEIXIN_PY}.bak.$(date +%Y%m%d_%H%M%S)"
cp "$WEIXIN_PY" "$BACKUP"
echo "Backup: $BACKUP"

python3 - "$WEIXIN_PY" <<'PYTHON'
import sys, re
from pathlib import Path

weixin_py = Path(sys.argv[1])
content = weixin_py.read_text()

pattern_1 = r'def _split_delivery_units_for_weixin\(content: str\) -> List\[str\]:.*?(?=\ndef |\nclass |\Z)'
replacement_1 = '''def _split_delivery_units_for_weixin(content: str) -> List[str]:
    """Return content as a single unit; splitting is handled by length limits only."""
    return [content] if content.strip() else []

'''

pattern_2 = r'def _split_text_for_weixin_delivery\(content: str, max_length: int\) -> List\[str\]:.*?(?=\ndef |\nclass |\Z)'
replacement_2 = '''def _split_text_for_weixin_delivery(content: str, max_length: int) -> List[str]:
    """Split content only when it exceeds max_length; no newline-based splitting."""
    if len(content) <= max_length:
        return [content]
    return _pack_markdown_blocks_for_weixin(content, max_length) or [content]

'''

content = re.sub(pattern_1, replacement_1, content, flags=re.DOTALL)
content = re.sub(pattern_2, replacement_2, content, flags=re.DOTALL)
weixin_py.write_text(content)
print("Patched successfully")
PYTHON

echo ""
echo "Restart Hermes: sudo systemctl restart hermes"
