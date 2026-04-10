#!/usr/bin/env python3
"""social_preview.py — generate the 1280x640 GitHub social preview card.

GitHub uses this image when the repo URL is shared on Twitter/X, Telegram,
Slack, LinkedIn, etc. Without it, link previews render blank and click-through
drops sharply.

Usage:
    python3 scripts/social_preview.py
    python3 scripts/social_preview.py --out docs/social-preview.png

Then upload it via Settings → Social preview on github.com.

Requires: Pillow (`pip install pillow`).
"""

import argparse
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Need Pillow: pip install pillow")

WIDTH, HEIGHT = 1280, 640
BG = (8, 12, 20)              # near-black with a hint of blue
FG = (255, 255, 255)
ACCENT = (7, 193, 96)         # WeChat green
SUBTLE = (140, 150, 165)
PADDING = 60


def find_font(size, bold=False):
    """Try to find a system font that supports both Latin and CJK."""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="docs/social-preview.png")
    args = p.parse_args()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Background gradient (subtle)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(BG[0] + (15 - BG[0]) * ratio)
        g = int(BG[1] + (22 - BG[1]) * ratio)
        b = int(BG[2] + (40 - BG[2]) * ratio)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

    # Accent strip on the left
    draw.rectangle([(0, 0), (12, HEIGHT)], fill=ACCENT)

    # Title
    title_font = find_font(96, bold=True)
    draw.text((PADDING, 100), "HermesClaw", font=title_font, fill=FG)

    # Subtitle line 1
    sub_font = find_font(38, bold=False)
    draw.text(
        (PADDING, 230),
        "One WeChat bot. Two AI brains.",
        font=sub_font,
        fill=FG,
    )

    # Subtitle line 2
    draw.text(
        (PADDING, 285),
        "Run Hermes Agent + OpenClaw on the same Clawbot account.",
        font=sub_font,
        fill=SUBTLE,
    )

    # Command pills
    pill_font = find_font(34, bold=True)
    pill_y = 400
    pill_x = PADDING
    pill_h = 60
    for label, color in [
        ("/hermes", (88, 101, 242)),    # discord blue
        ("/openclaw", (255, 138, 0)),   # lobster orange
        ("/both", ACCENT),
    ]:
        bbox = draw.textbbox((0, 0), label, font=pill_font)
        text_w = bbox[2] - bbox[0]
        pill_w = text_w + 50
        draw.rounded_rectangle(
            [(pill_x, pill_y), (pill_x + pill_w, pill_y + pill_h)],
            radius=30, fill=color,
        )
        draw.text((pill_x + 25, pill_y + 8), label, font=pill_font, fill=FG)
        pill_x += pill_w + 24

    # Footer
    footer_font = find_font(28, bold=False)
    draw.text(
        (PADDING, HEIGHT - PADDING - 30),
        "github.com/AaronWong1999/hermesclaw",
        font=footer_font,
        fill=SUBTLE,
    )

    # Tag in top-right corner
    tag_font = find_font(22, bold=True)
    tag_text = "MIT  •  Python  •  one-line install"
    bbox = draw.textbbox((0, 0), tag_text, font=tag_font)
    tag_w = bbox[2] - bbox[0]
    draw.text(
        (WIDTH - PADDING - tag_w, PADDING),
        tag_text,
        font=tag_font,
        fill=SUBTLE,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    print(f"[done] {out_path}  ({out_path.stat().st_size // 1024} KB)")
    print()
    print("Next step: upload via")
    print("  https://github.com/AaronWong1999/hermesclaw/settings  →  Social preview")


if __name__ == "__main__":
    main()
