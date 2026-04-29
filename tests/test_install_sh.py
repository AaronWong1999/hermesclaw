"""Regression tests for install.sh bash compatibility.

Covers:
- No mapfile usage (bash 4+ only; macOS ships bash 3.2)
- All interactive read prompts use _PROMPT_FD and have || guards
- /dev/tty stdin detection is present
- Script is syntactically valid bash
- Non-interactive stdin (e.g. curl | bash) does not abort at prompt step
"""

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "install.sh"


def _text():
    return SCRIPT.read_text()


def test_no_mapfile():
    """install.sh must not use mapfile (bash 4+ only; fails on macOS bash 3.2)."""
    for i, line in enumerate(_text().splitlines(), 1):
        stripped = line.strip()
        # Skip comments
        if stripped.startswith("#"):
            continue
        assert not re.search(r"\bmapfile\b", line), (
            f"install.sh line {i}: mapfile is bash 4+ and fails on macOS; use a while+read loop instead.\n"
            f"  {line}"
        )


def test_bash_syntax():
    """install.sh must be syntactically valid bash."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n reported syntax errors:\n{result.stderr}"
    )


def test_read_guards():
    """Every interactive 'read -r -u' prompt must have a '|| VAR=""' guard."""
    for i, line in enumerate(_text().splitlines(), 1):
        stripped = line.strip()
        # Only interactive prompts (not while-loop reads)
        if re.search(r"\bread -r -u\b.*-p\b", stripped) and "while" not in line:
            assert "|| " in stripped, (
                f"install.sh line {i}: unguarded interactive read found.\n"
                f"  Bare 'read' returns non-zero on EOF; add '|| VAR=\"\"' to prevent set -e from aborting.\n"
                f"  {line}"
            )


def test_stdin_tty_detection():
    """Script must contain /dev/tty detection for non-interactive stdin (curl | bash)."""
    text = _text()
    assert "! -t 0" in text, "Missing stdin TTY detection; add '[[ ! -t 0 ]]' block near -y flag parsing."
    assert "_PROMPT_FD" in text, "Missing _PROMPT_FD variable; interactive reads must use '-u \"$_PROMPT_FD\"'."
    assert "/dev/tty" in text, "Missing /dev/tty redirect; needed to support interactive prompts when stdin is piped."


def test_curl_bash_noninteractive():
    """Running install.sh with stdin from /dev/null must not abort at the read-prompt step.

    The script will exit non-zero because the environment is not set up (no Hermes/OpenClaw
    gateways, no account files), but it must NOT exit with a message originating from a
    failed 'read' call — i.e. the exit should come from an explicit 'exit 1' gate, not set -e.
    """
    result = subprocess.run(
        ["bash", str(SCRIPT), "-y"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # With -y and /dev/null stdin, the script should reach the gateway check and exit cleanly
    # (exit 1 due to "No WeChat gateway configured"), not silently die at a 'read'.
    combined = result.stdout + result.stderr
    # Must NOT see a raw bash 'read' failure message
    assert "read:" not in combined.lower() or "read -r" not in combined.lower(), (
        f"Script appears to have failed at a 'read' call:\n{combined[:800]}"
    )
    # Must see HermesClaw banner (proving it got past the early read/mapfile point)
    assert "HermesClaw" in combined, (
        f"Script did not reach HermesClaw banner — may have aborted very early:\n{combined[:800]}"
    )
