#!/usr/bin/env python3
"""issue_scout.py — find GitHub issues where users ask about WeChat support
in Hermes Agent / OpenClaw, and generate per-issue *draft* comments for manual posting.

This is intentionally a SEMI-AUTO tool. It does not post anything by itself.
You manually click "comment" on each issue and paste the draft.

Why semi-auto and not full auto?

GitHub flags accounts that post N similar comments across M repos in a short
window — even if every comment is hand-typed by a human. The flag is *behavioral*,
not text-based: timing, repo diversity, link patterns, account age. Once flagged,
the comments are silently hidden, the repo gets de-prioritized in search, and the
account can't be unbanned. Throwing away the launch you're trying to do.

The bottleneck of issue outreach is not typing — it's *finding the right issue
and writing a comment that's actually relevant to that specific user*. This script
solves the bottleneck without crossing the spam line.

Usage:
    python3 scripts/issue_scout.py
    python3 scripts/issue_scout.py --max-per-repo 5

Output: scripts/leads.md  — one section per matched issue with:
    - issue URL + title
    - quoted excerpt of the user's pain point
    - a tailored draft comment
    - a "[ ] posted" checkbox you tick after manually posting

Requires: gh CLI authenticated.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Repos and search terms. Add more as you discover where users hang out.
SEARCH_TARGETS = [
    # (repo, query) — gh search issues syntax
    ("NousResearch/hermes-agent", "wechat"),
    ("NousResearch/hermes-agent", "weixin"),
    ("NousResearch/hermes-agent", "微信"),
    ("openclaw/openclaw", "wechat hermes"),
    ("openclaw/openclaw", "dual agent"),
    ("openclaw/openclaw", "微信 hermes"),
]

LEADS_FILE = Path(__file__).parent / "leads.md"
HERMESCLAW_URL = "https://github.com/AaronWong1999/hermesclaw"
SCANNED_FILE = Path(__file__).parent / ".scout_seen.json"


def gh_search(repo, query, limit=20):
    """Return list of issue dicts via gh search issues."""
    cmd = [
        "gh", "search", "issues",
        f"repo:{repo}", query,
        "--limit", str(limit),
        "--json", "url,title,body,author,createdAt,state,commentsCount",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(f"[warn] {repo} '{query}': {r.stderr.strip()}", file=sys.stderr)
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


def excerpt(body, max_len=280):
    """Pull the most relevant ~280 chars of the issue body."""
    if not body:
        return ""
    body = re.sub(r"```.*?```", "[code]", body, flags=re.DOTALL)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:max_len] + ("…" if len(body) > max_len else "")


WECHAT_KEYWORDS = ("wechat", "weixin", "微信", "ilink", "clawbot", "weixinclawbot", "wecom", "企业微信")


def is_wechat_relevant(title, body):
    """Stricter filter — title or body must mention WeChat *substantively*.

    Filters out false positives like "memory bug in gateway sessions" that
    happen to mention WeChat in passing.
    """
    text = ((title or "") + " " + (body or "")).lower()
    if not text.strip():
        return False
    hits = sum(text.count(k) for k in WECHAT_KEYWORDS)
    if hits == 0:
        return False
    # Title alone is enough — it's the user's main topic
    title_l = (title or "").lower()
    if any(k in title_l for k in WECHAT_KEYWORDS):
        return True
    # Otherwise need >= 2 mentions in body to filter passing references
    return hits >= 2


def is_chinese(text):
    if not text:
        return False
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cjk >= 5


def find_quote_snippet(body, max_len=120):
    """Pull one short sentence from the body that contains a WeChat keyword.

    Used for direct quoting in the draft so the comment proves I read the issue.
    """
    if not body:
        return ""
    body_clean = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    body_clean = re.sub(r"#+\s*", "", body_clean)
    # Split into sentences (works for both English and Chinese)
    sentences = re.split(r"(?<=[.!?。！？\n])\s+", body_clean)
    for sent in sentences:
        sent = sent.strip()
        if not sent or len(sent) > max_len * 2:
            continue
        if any(k in sent.lower() for k in WECHAT_KEYWORDS):
            return sent[:max_len].rstrip() + ("…" if len(sent) > max_len else "")
    return ""


def detect_topics(text):
    """Return a set of topical tags so the draft can be more specific."""
    text_l = (text or "").lower()
    topics = set()
    if "wecom" in text_l or "企业微信" in text:
        topics.add("wecom")
    if "voice" in text_l or "语音" in text:
        topics.add("voice")
    if "image" in text_l or "vision" in text_l or "图片" in text:
        topics.add("image")
    if "file" in text_l or "文件" in text:
        topics.add("file")
    if "feishu" in text_l or "飞书" in text or "lark" in text_l:
        topics.add("feishu")
    if "weixinclawbot" in text_l or "ilink" in text_l or "clawbot" in text_l:
        topics.add("clawbot_aware")
    return topics


def draft_comment(issue, repo):
    """Generate a per-issue draft. Each draft references the user's actual
    title/quote and language, so two leads never get identical text — which
    is what triggers anti-spam pattern detection.

    Still meant to be edited by hand before posting. Treat it as a starting
    point, not a finished comment.
    """
    title = issue.get("title", "") or ""
    body = issue.get("body", "") or ""
    author = issue.get("author", {}).get("login", "") or "there"
    state = issue.get("state", "open")
    chinese = is_chinese(title + " " + body)
    quote = find_quote_snippet(body)
    topics = detect_topics(title + " " + body)

    # Topic-specific addons
    addons_en = []
    addons_zh = []
    if "wecom" in topics:
        addons_en.append(
            "Heads-up: HermesClaw is built for personal WeChat (via the Clawbot / "
            "openclaw-weixin iLink path), not WeCom (企业微信). The transport layer is "
            "different. If you specifically need 企业微信, this won't help directly — "
            "but if a personal WeChat account works for your use case, it's a clean fit."
        )
        addons_zh.append(
            "提醒一下：HermesClaw 走的是个人微信（基于 Clawbot / openclaw-weixin 的 iLink 通道），"
            "不是企业微信。两边的协议不一样。如果你必须用企业微信，这个工具帮不上；"
            "但如果个人微信账号能满足你的场景，它正好对得上。"
        )
    if "feishu" in topics:
        addons_en.append(
            "(For Feishu specifically, HermesClaw doesn't cover that — it's WeChat-only. "
            "For Feishu I'd file a separate request upstream or look at OpenClaw's Feishu "
            "channel.)"
        )
    if "voice" in topics:
        addons_en.append(
            "Voice messages do work — HermesClaw forwards the iLink transcription to "
            "Hermes as `[The user sent a voice message. Here's what they said: \"...\"]` "
            "so the model can quote it cleanly."
        )
        addons_zh.append(
            "语音消息可以正常用：HermesClaw 会把 iLink 的转写文本以 "
            "`[The user sent a voice message. Here's what they said: \"...\"]` 的形式喂给 Hermes。"
        )
    if "image" in topics:
        addons_en.append(
            "Images / videos / files are downloaded, AES-decrypted from the iLink CDN, "
            "and handed to Hermes as local file paths — so vision tools can read them "
            "without base64 round-tripping."
        )
        addons_zh.append(
            "图片/视频/文件会从 iLink CDN 下载、AES 解密，再以本地路径交给 Hermes，避免 base64 来回转。"
        )
    if "clawbot_aware" in topics:
        # The user already knows about Clawbot/iLink, no need to over-explain
        clawbot_intro_en = (
            f"You linked the WeixinClawBot / clawbot integration in the issue — "
            f"HermesClaw plugs into exactly that path."
        )
        clawbot_intro_zh = (
            f"你提到了 WeixinClawBot / clawbot 这条路径 —— HermesClaw 就是接在这上面的。"
        )
    else:
        clawbot_intro_en = None
        clawbot_intro_zh = None

    # Compose
    if chinese:
        parts = [f"Hi @{author}，"]
        if quote:
            parts.append(f"看到你提到「{quote}」 ——")
        if clawbot_intro_zh:
            parts.append(clawbot_intro_zh)
        parts.append(
            f"我前段时间也踩了一样的坑（Hermes Agent 不支持微信），就写了一个小工具叫 "
            f"HermesClaw 来桥接：它复用 Clawbot / openclaw-weixin 已有的 iLink 通道，"
            f"在同一个微信 Clawbot 账号上同时跑 Hermes 和 OpenClaw，用 "
            f"`/hermes` `/openclaw` `/both` 切换路由。"
        )
        parts.extend(addons_zh)
        parts.append(
            f"仓库在 {HERMESCLAW_URL}，README 里有一行 `curl | bash` 安装。如果踩坑了 "
            f"在这里 at 我或者去 HermesClaw 仓库提 issue 都行。"
        )
        if state == "closed":
            parts.append("\n_（看到这个 issue 已关闭，如果你已经用别的办法解决了就忽略这条留言。）_")
        return "\n\n".join(p for p in parts if p)

    # English
    parts = [f"Hey @{author} —"]
    if quote:
        parts.append(f"saw you wrote: \"{quote}\"")
    if clawbot_intro_en:
        parts.append(clawbot_intro_en)
    parts.append(
        f"I hit the same wall (Hermes Agent has no WeChat gateway out of the box) "
        f"and ended up building a small bridge for it called HermesClaw. It piggybacks "
        f"on the Clawbot / openclaw-weixin iLink path, so if you're already running "
        f"OpenClaw on WeChat you don't need a second account. `/hermes` `/openclaw` "
        f"`/both` switches the route per user."
    )
    parts.extend(addons_en)
    parts.append(
        f"Repo: {HERMESCLAW_URL} — there's a one-line installer in the README. "
        f"Happy to help if you get stuck setting it up; ping me here or file an issue "
        f"on the HermesClaw repo."
    )
    if state == "closed":
        parts.append("\n_(I see this is closed — feel free to ignore if you already solved it.)_")
    return "\n\n".join(p for p in parts if p)


def load_seen():
    if SCANNED_FILE.exists():
        return set(json.loads(SCANNED_FILE.read_text()))
    return set()


def save_seen(seen):
    SCANNED_FILE.write_text(json.dumps(sorted(seen), indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-per-repo", type=int, default=10,
                   help="max issues to fetch per (repo, query)")
    p.add_argument("--include-seen", action="store_true",
                   help="re-include leads from previous runs")
    args = p.parse_args()

    seen = set() if args.include_seen else load_seen()
    new_leads = []

    for repo, query in SEARCH_TARGETS:
        print(f"[scan] {repo} '{query}'", file=sys.stderr)
        for issue in gh_search(repo, query, args.max_per_repo):
            url = issue.get("url", "")
            if not url or url in seen:
                continue
            if not is_wechat_relevant(issue.get("title", ""), issue.get("body", "")):
                continue
            new_leads.append((repo, issue))
            seen.add(url)

    print(f"[scan] found {len(new_leads)} new lead(s)", file=sys.stderr)

    if not new_leads:
        print("No new leads. (Use --include-seen to re-render previously seen ones.)")
        return

    ts = datetime.now(timezone.utc).isoformat(timespec="minutes")
    out = [
        f"# HermesClaw outreach leads — generated {ts}",
        "",
        "**Workflow**: for each lead below,",
        "1. Read the issue (don't trust the excerpt blindly — context matters)",
        "2. Edit the draft comment so it actually fits what the user is asking",
        "3. Post manually on GitHub",
        "4. Tick the `[ ] posted` checkbox so this entry doesn't reappear",
        "",
        "**Pace yourself**: max 5–8 comments/day, spread across hours. Comment "
        "bursts trigger anti-spam — even with hand-typed text.",
        "",
        "---",
        "",
    ]

    for repo, issue in new_leads:
        url = issue.get("url", "")
        title = issue.get("title", "")
        author = issue.get("author", {}).get("login", "?")
        state = issue.get("state", "open")
        comments = issue.get("commentsCount", 0)
        created = issue.get("createdAt", "")[:10]
        body_excerpt = excerpt(issue.get("body", ""))
        draft = draft_comment(issue, repo)

        out.append(f"## [{repo}] {title}")
        out.append("")
        out.append(f"- **URL**: {url}")
        out.append(f"- **Author**: @{author}  •  **State**: {state}  "
                   f"•  **Comments**: {comments}  •  **Created**: {created}")
        out.append("")
        out.append(f"> {body_excerpt}")
        out.append("")
        out.append("**Draft comment** (edit before posting):")
        out.append("")
        out.append("```")
        out.append(draft)
        out.append("```")
        out.append("")
        out.append("- [ ] posted")
        out.append("")
        out.append("---")
        out.append("")

    LEADS_FILE.write_text("\n".join(out))
    save_seen(seen)
    print(f"[done] wrote {len(new_leads)} lead(s) to {LEADS_FILE}")


if __name__ == "__main__":
    main()
