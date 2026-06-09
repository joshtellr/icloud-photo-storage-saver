#!/usr/bin/env python3
"""Generate the DMG window background (1x PNG, 2x PNG, and a HiDPI .tiff).

Run with the osxphotos venv python (it has Pillow):
    ~/.local/share/uv/tools/osxphotos/bin/python make_dmg_background.py

Outputs into docs/: dmg-background.png, dmg-background@2x.png, dmg-background.tiff
The .tiff (built via `tiffutil -cathidpicheck`) is what the DMG references so the
art stays crisp on Retina displays. Window/icon geometry here must match build_dmg.sh.
"""
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Logical window content size (points). Mirrored in build_dmg.sh.
W, H = 620, 430
APP_X, ICON_Y = 165, 205        # center of the app icon
APPS_X = 455                    # center of the Applications shortcut
DOCS = Path(__file__).resolve().parent.parent / "docs"

BG_TOP = (247, 248, 250)        # soft light gradient (fixed; looks fine in dark mode too)
BG_BOT = (233, 236, 241)
INK = (29, 29, 31)              # title
SUBTLE = (110, 112, 119)        # subtitle / hint
ARROW = (10, 132, 255)          # Apple accent blue


def load_font(size, bold=False):
    # SF is a variable font; pick the Bold named instance when asked.
    try:
        f = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", size)
        if bold:
            f.set_variation_by_name("Bold")
        else:
            f.set_variation_by_name("Regular")
        return f
    except Exception:
        path = "/System/Library/Fonts/HelveticaNeue.ttc"
        return ImageFont.truetype(path, size, index=1 if bold else 0)


def centered(d, cx, y, text, font, fill):
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.text((cx - (r - l) / 2, y), text, font=font, fill=fill)


def render(scale):
    s = scale
    img = Image.new("RGB", (W * s, H * s), BG_TOP)
    d = ImageDraw.Draw(img)

    # vertical gradient
    for y in range(H * s):
        t = y / (H * s)
        d.line(
            [(0, y), (W * s, y)],
            fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)),
        )

    title = load_font(27 * s, bold=True)
    sub = load_font(15 * s, bold=False)
    hint = load_font(12 * s, bold=False)

    centered(d, (W * s) // 2, 40 * s, "iCloud Photo Storage Saver", title, INK)
    centered(d, (W * s) // 2, 80 * s,
             "Drag the app into your Applications folder to install", sub, SUBTLE)

    # Arrow from the app icon to the Applications folder, at icon center height.
    y = ICON_Y * s
    x0, x1 = (APP_X + 78) * s, (APPS_X - 78) * s
    shaft = 7 * s
    head = 22 * s
    d.line([(x0, y), (x1 - head + 2 * s, y)], fill=ARROW, width=shaft)
    d.ellipse([x0 - shaft // 2, y - shaft // 2, x0 + shaft // 2, y + shaft // 2], fill=ARROW)
    d.polygon([(x1, y), (x1 - head, y - head * 0.7), (x1 - head, y + head * 0.7)], fill=ARROW)

    centered(d, (W * s) // 2, (H - 52) * s,
             "First launch only: right-click the app → Open → Open", hint, SUBTLE)
    return img


def main():
    DOCS.mkdir(exist_ok=True)
    p1 = DOCS / "dmg-background.png"
    p2 = DOCS / "dmg-background@2x.png"
    tiff = DOCS / "dmg-background.tiff"
    render(1).save(p1)
    render(2).save(p2)
    subprocess.run(["tiffutil", "-cathidpicheck", str(p1), str(p2), "-out", str(tiff)],
                   check=True)
    print(f"✓ wrote {p1.name}, {p2.name}, {tiff.name}")


if __name__ == "__main__":
    main()
