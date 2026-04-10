#!/usr/bin/env python3
"""multi_post.py — render one master Markdown post into platform-specific drafts.

Each platform has its own quirks:
  - Twitter/X: 280 char chunks, thread numbering, no markdown
  - Hacker News: plain text + naked URLs, no images, no markdown
  - Reddit: markdown OK, gallery in comments, no embeds
  - Dev.to: front matter required, full markdown
  - 掘金 (juejin): markdown, hero image at top
  - 知乎: HTML or rich text, no markdown headers in body
  - V2EX: very simple, paragraph breaks, no marketing tone
  - 小红书: 9-frame carousel — caption + 9 image captions

Usage:
    python3 scripts/multi_post.py master.md            # render all
    python3 scripts/multi_post.py master.md --only twitter,reddit
    python3 scripts/multi_post.py master.md --out drafts/

Master file format:
    ---
    title: <one-line title>
    tagline: <one-line subtitle>
    repo_url: https://github.com/AaronWong1999/hermesclaw
    image: docs/screenshots/01-whoami-hermes.jpg
    ---
    <body in plain markdown>
"""

import argparse
import re
import sys
from pathlib import Path


def parse_master(text):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        raise SystemExit("master file must start with --- frontmatter ---")
    fm_block, body = m.group(1), m.group(2)
    fm = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body.strip()


def split_for_twitter(body, limit=270):
    """Split markdown body into a tweet thread.

    Removes markdown formatting (Twitter doesn't render it), splits on paragraphs,
    bins into ~270-char chunks, prepends thread numbering.
    """
    plain = re.sub(r"```[\s\S]*?```", "", body)         # drop code blocks
    plain = re.sub(r"^#+\s*", "", plain, flags=re.M)    # drop headings
    plain = re.sub(r"\*\*(.*?)\*\*", r"\1", plain)      # drop bold
    plain = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1 \2", plain)  # link → text url
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()

    paragraphs = [p.strip() for p in plain.split("\n\n") if p.strip()]
    chunks = []
    cur = ""
    for p in paragraphs:
        if len(cur) + len(p) + 2 <= limit:
            cur = (cur + "\n\n" + p).strip()
        else:
            if cur:
                chunks.append(cur)
            if len(p) <= limit:
                cur = p
            else:
                # split a long paragraph by sentences
                sents = re.split(r"(?<=[.!?。！？])\s+", p)
                cur = ""
                for s in sents:
                    if len(cur) + len(s) + 1 <= limit:
                        cur = (cur + " " + s).strip()
                    else:
                        if cur:
                            chunks.append(cur)
                        cur = s
    if cur:
        chunks.append(cur)

    n = len(chunks)
    return [f"{i+1}/{n} {c}" for i, c in enumerate(chunks)]


def render_twitter(fm, body):
    out = [f"# Twitter / X thread\n"]
    out.append(f"**Pin tweet (post first, with the GIF/screenshots):**\n")
    out.append(f"{fm.get('title','')}\n\n{fm.get('tagline','')}\n\n{fm.get('repo_url','')}\n")
    out.append("\n---\n\n**Reply chain:**\n")
    for tweet in split_for_twitter(body):
        out.append(f"```\n{tweet}\n```\n")
    return "\n".join(out)


def render_hn(fm, body):
    plain = re.sub(r"```[\s\S]*?```", "", body)
    plain = re.sub(r"^#+\s*", "", plain, flags=re.M)
    plain = re.sub(r"\*\*(.*?)\*\*", r"\1", plain)
    plain = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1 (\2)", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    return (
        f"# Show HN draft\n\n"
        f"**Title** (80 char max — HN truncates):\n\n"
        f"```\nShow HN: {fm.get('title','')} – {fm.get('tagline','')}\n```\n\n"
        f"**URL**: {fm.get('repo_url','')}\n\n"
        f"**First comment** (post immediately so people land on it):\n\n"
        f"```\n{plain}\n```\n\n"
        f"**Posting tips:**\n"
        f"- Best window: Tue–Thu 8–10am ET\n"
        f"- Reply to every comment within 10 min for the first 2 hours\n"
        f"- Do NOT ask friends to upvote (HN flags vote rings)\n"
    )


def render_reddit(fm, body, sub):
    return (
        f"# Reddit — r/{sub}\n\n"
        f"**Title:**\n\n```\n{fm.get('title','')} — {fm.get('tagline','')}\n```\n\n"
        f"**Body** (paste this — link the repo in a top-level comment, not the title):\n\n"
        f"```\n{body}\n\n---\n\nGitHub: {fm.get('repo_url','')}\n```\n\n"
        f"**Tips for r/{sub}:**\n"
        f"- Embed the screenshots/GIF natively (Reddit demotes link posts)\n"
        f"- Reply to every comment within an hour\n"
        f"- Do not crosspost — file separate posts per sub\n"
    )


def render_devto(fm, body):
    fm_block = (
        "---\n"
        f"title: {fm.get('title','')}\n"
        "published: false\n"
        "tags: opensource, python, ai, wechat\n"
        f"cover_image: {fm.get('image','')}\n"
        "---\n\n"
    )
    return f"# Dev.to draft\n\n```markdown\n{fm_block}{body}\n\n---\n\nRepo: {fm.get('repo_url','')}\n```\n"


def render_juejin(fm, body):
    return (
        f"# 掘金（juejin.cn）草稿\n\n"
        f"**标题**：{fm.get('title','')} —— {fm.get('tagline','')}\n\n"
        f"**封面图**：`{fm.get('image','')}`\n\n"
        f"**正文**：\n\n```markdown\n{body}\n\n---\n\n仓库地址：{fm.get('repo_url','')}\n```\n"
    )


def render_zhihu(fm, body):
    return (
        f"# 知乎草稿（专栏 + 相关问题回答）\n\n"
        f"**标题**：{fm.get('title','')} —— {fm.get('tagline','')}\n\n"
        f"**正文**：\n\n```\n{body}\n\n仓库地址：{fm.get('repo_url','')}\n```\n\n"
        f"**怎么用：**\n"
        f"1. 发到自己的专栏\n"
        f"2. 搜「Hermes Agent 微信」「OpenClaw 微信」相关问题，把这篇浓缩 2-3 段回答上去\n"
        f"3. 不要在多个无关问题下贴同样的内容（知乎也有反 spam）\n"
    )


def render_v2ex(fm, body):
    plain = re.sub(r"^#+\s*", "", body, flags=re.M)
    plain = re.sub(r"\*\*(.*?)\*\*", r"\1", plain)
    return (
        f"# V2EX 草稿（节点：分享发现 / 创意 / 程序员）\n\n"
        f"**标题**：[分享] {fm.get('title','')} —— {fm.get('tagline','')}\n\n"
        f"**正文**：\n\n```\n{plain}\n\n仓库：{fm.get('repo_url','')}\n```\n\n"
        f"**V2EX 注意事项：**\n"
        f"- V2EX 反感营销腔，写得越像「我自己踩坑后做的工具」越好\n"
        f"- 别 @ 大 V，别求 star\n"
        f"- 回复每一条评论\n"
    )


def render_xiaohongshu(fm, body):
    return (
        f"# 小红书草稿（9 帧 carousel）\n\n"
        f"**标题**：{fm.get('title','')}｜{fm.get('tagline','')}\n\n"
        f"**正文**：\n\n```\n一个微信号同时跑两个 AI agent，亲测可用 ✅\n\n"
        f"踩坑半天才发现 Hermes Agent 居然不支持微信。\n"
        f"找了一圈没人做，自己写了个桥接工具：HermesClaw。\n\n"
        f"一行命令装完，/hermes /openclaw /both 切换路由。\n\n"
        f"语音、图片、视频、文件全部能转发。\n\n"
        f"开源 MIT，仓库链接放评论区了。\n\n"
        f"#AI #微信 #自部署 #开源 #开发者\n```\n\n"
        f"**9 帧建议**：\n"
        f"1. 封面：HermesClaw + 一个微信号 + 两个 AI 大脑\n"
        f"2. 痛点：Hermes 不支持微信？\n"
        f"3. 解法：HermesClaw 桥接\n"
        f"4. 截图：/whoami\n"
        f"5. 截图：/hermes\n"
        f"6. 截图：/openclaw + 语音\n"
        f"7. 截图：/both + 图片\n"
        f"8. 安装命令\n"
        f"9. GitHub 二维码\n"
    )


RENDERERS = {
    "twitter": lambda fm, body: render_twitter(fm, body),
    "hn": lambda fm, body: render_hn(fm, body),
    "reddit-localllama": lambda fm, body: render_reddit(fm, body, "LocalLLaMA"),
    "reddit-selfhosted": lambda fm, body: render_reddit(fm, body, "selfhosted"),
    "devto": lambda fm, body: render_devto(fm, body),
    "juejin": lambda fm, body: render_juejin(fm, body),
    "zhihu": lambda fm, body: render_zhihu(fm, body),
    "v2ex": lambda fm, body: render_v2ex(fm, body),
    "xiaohongshu": lambda fm, body: render_xiaohongshu(fm, body),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("master", help="master markdown file with frontmatter")
    p.add_argument("--only", help="comma-separated subset of platforms")
    p.add_argument("--out", default="../hermesclaw-launch/drafts",
                   help="output directory (default: outside the repo)")
    args = p.parse_args()

    text = Path(args.master).read_text()
    fm, body = parse_master(text)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = args.only.split(",") if args.only else list(RENDERERS.keys())
    for name in targets:
        name = name.strip()
        if name not in RENDERERS:
            print(f"[skip] unknown platform: {name}", file=sys.stderr)
            continue
        rendered = RENDERERS[name](fm, body)
        out_file = out_dir / f"{name}.md"
        out_file.write_text(rendered)
        print(f"[done] {out_file}")


if __name__ == "__main__":
    main()
