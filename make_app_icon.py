#!/usr/bin/env python3
"""Generate the app icon: an iCloud-blue squircle with a white photo-cloud and a
green "freed space" down-badge. Renders a 1024px master, the macOS .iconset, and
bundles them into docs/AppIcon.icns (via iconutil).

Run with the osxphotos venv python (it has Pillow):
    ~/.local/share/uv/tools/osxphotos/bin/python make_app_icon.py

Outputs into docs/: app-icon.png (1024 preview) and AppIcon.icns.
"""
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

DOCS = Path(__file__).resolve().parent / "docs"
SS = 4  # supersample factor for crisp edges

# Palette
BLUE_TOP = (77, 173, 255)     # light iCloud blue
BLUE_BOT = (10, 116, 230)     # deeper blue
GREEN = (52, 199, 89)         # Apple system green (the "saved space" badge)
WHITE = (255, 255, 255)


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vertical_gradient(size, top, bot):
    g = Image.new("RGB", size)
    px = g.load()
    h = size[1]
    for y in range(h):
        c = _lerp(top, bot, y / (h - 1))
        for x in range(size[0]):
            px[x, y] = c
    return g


def _cloud_mask(size):
    """White cloud silhouette on a transparent layer, centered in `size`."""
    layer = Image.new("L", size, 0)
    d = ImageDraw.Draw(layer)
    w, h = size
    cx = w / 2
    cy = h * 0.46
    u = w * 0.36  # cloud scale unit

    def circle(px, py, r):
        d.ellipse([px - r, py - r, px + r, py + r], fill=255)

    # overlapping puffs + a flat base = classic cloud
    circle(cx - 0.62 * u, cy + 0.18 * u, 0.42 * u)   # left
    circle(cx + 0.66 * u, cy + 0.16 * u, 0.48 * u)   # right
    circle(cx - 0.04 * u, cy - 0.30 * u, 0.58 * u)   # top center
    circle(cx + 0.34 * u, cy - 0.06 * u, 0.40 * u)   # upper right
    # base bar
    bx0, bx1 = cx - 1.02 * u, cx + 1.12 * u
    by0, by1 = cy + 0.20 * u, cy + 0.62 * u
    r = (by1 - by0) / 2
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=r, fill=255)
    return layer


def _down_badge(size):
    """Green circle with a bold white down-arrow (freed/offloaded space)."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = size
    r = w * 0.165
    cx, cy = w * 0.70, h * 0.70
    # white ring so the badge reads against the cloud
    ring = r * 1.16
    d.ellipse([cx - ring, cy - ring, cx + ring, cy + ring], fill=WHITE + (255,))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GREEN + (255,))
    # down arrow
    sw = r * 0.26
    d.line([(cx, cy - r * 0.50), (cx, cy + r * 0.34)], fill=WHITE, width=int(sw))
    head = r * 0.46
    d.polygon([(cx - head, cy + 0.02 * r), (cx + head, cy + 0.02 * r),
               (cx, cy + r * 0.58)], fill=WHITE)
    return layer


def render(px):
    size = (px * SS, px * SS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))

    margin = px * SS * 100 / 1024
    radius = px * SS * 185 / 1024
    body = [margin, margin, size[0] - margin, size[1] - margin]

    # soft drop shadow
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([body[0], body[1] + margin * 0.18,
                          body[2], body[3] + margin * 0.18],
                         radius=radius, fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(margin * 0.30))
    canvas = Image.alpha_composite(canvas, shadow)

    # gradient body, clipped to the squircle
    grad = _vertical_gradient(size, BLUE_TOP, BLUE_BOT).convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(body, radius=radius, fill=255)
    canvas.paste(grad, (0, 0), mask)

    # cloud (white) + badge
    cloud = Image.new("RGBA", size, (0, 0, 0, 0))
    cloud.paste(Image.new("RGBA", size, WHITE + (255,)), (0, 0), _cloud_mask(size))
    canvas = Image.alpha_composite(canvas, cloud)
    canvas = Image.alpha_composite(canvas, _down_badge(size))

    return canvas.resize((px, px), Image.LANCZOS)


def main():
    DOCS.mkdir(exist_ok=True)
    master = render(1024)
    master.save(DOCS / "app-icon.png")

    sizes = [16, 32, 64, 128, 256, 512, 1024]
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        rendered = {s: render(s) for s in sizes}
        # macOS iconset naming: NAMExN.png and the @2x variant
        pairs = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"),
                 (64, "32x32@2x"), (128, "128x128"), (256, "128x128@2x"),
                 (256, "256x256"), (512, "256x256@2x"), (512, "512x512"),
                 (1024, "512x512@2x")]
        for s, label in pairs:
            rendered[s].save(iconset / f"icon_{label}.png")
        out = DOCS / "AppIcon.icns"
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
                       check=True)
    print(f"✓ wrote {(DOCS / 'app-icon.png').name}, {(DOCS / 'AppIcon.icns').name}")


if __name__ == "__main__":
    main()
