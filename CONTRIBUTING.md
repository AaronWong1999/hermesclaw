# Contributing to HermesClaw

Thanks for your interest. HermesClaw is a small project — one Python file, ~760 lines — so contributions stay focused.

## Ground rules

1. **Keep it minimal.** HermesClaw is a router. It should not start managing memory, rewriting prompts, or competing with Hermes Agent / OpenClaw. If a feature belongs upstream in either of those, file it there.
2. **Keep it one file.** `hermesclaw.py` is intentionally a single file. Don't split it into a package without a strong reason.
3. **Keep it MIT.** All contributions are MIT licensed.

## What we welcome

- Bug reports with reproducible steps (please include `journalctl -u hermesclaw -n 100` output if relevant)
- Bug fixes
- Documentation fixes, especially for the bilingual README
- New iLink protocol fields support (the protocol is undocumented; PRs that add real-world coverage are welcome)
- New install paths (other Linux distros, BSD, Termux, etc.)
- Test additions

## What we don't want (yet)

- New runtime dependencies beyond `requests` and `pycryptodome`
- Web UI / dashboard / config GUI
- Database backends — `router_state.json` is enough
- Anything that requires running as root beyond the systemd install

## Development setup

```bash
git clone https://github.com/AaronWong1999/hermesclaw
cd hermesclaw
python3 -m venv .venv && source .venv/bin/activate
pip install requests pycryptodome pytest
# Edit hermesclaw.py
# Run tests if any exist:
pytest -q
```

To test locally without WeChat, mock `send_msg_ilink` and call `proc_msg(...)` directly with hand-crafted iLink message dicts.

## Pull request checklist

- [ ] My change is small enough to review in one sitting
- [ ] I've explained the *why*, not just the *what*, in the PR description
- [ ] I've tested it on a real machine, not just in my head
- [ ] I haven't added a new top-level dependency (or I've justified it)
- [ ] If my change touches the README, I've updated both the English and the Chinese paragraphs

## Reporting security issues

Email **aaronwong1999@icloud.com** instead of opening a public issue.

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). The short version: be kind, assume good faith, no slurs.
