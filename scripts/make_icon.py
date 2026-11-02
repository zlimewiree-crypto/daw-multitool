"""Draws the app icon (assets/AppIcon.png, 1024 px), the macOS icon file (assets/AppIcon.icns) and a square,
edge-to-edge copy for platforms that round the corners themselves (assets/AppIcon-square.png, for TikTok):
white waveform bars on a deep purple rounded square with a soft glow, the brand's purple and black.
Run it again only when the icon design changes: python scripts/make_icon.py"""
from pathlib import Path
import struct
import subprocess
import tempfile
import zlib

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 1024
BARS = (0.30, 0.52, 0.78, 1.0, 0.66, 0.44, 0.26)  # bar heights, left to right


def rounded_square(x, y, half, radius):
    """Signed depth inside a rounded square (pixels; positive = inside), for smooth anti-aliased edges."""
    centre = SIZE / 2
    qx, qy = np.abs(x - centre) - (half - radius), np.abs(y - centre) - (half - radius)
    outside = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)
    return radius - outside


def mix(a, b, t):
    return a + (np.array(b, float) - a) * t[..., None]


def draw(square=False):
    y, x = np.mgrid[0:SIZE, 0:SIZE].astype(float) + 0.5
    half, radius = (SIZE / 2, 1) if square else (412, 186)  # Apple's grid: an 824 px tile, ~22% corner radius
    tile = np.clip(rounded_square(x, y, half, radius) + 0.5, 0, 1)
    # black at the bottom, deep purple at the top, a violet glow behind the bars
    t = np.clip((y - (SIZE / 2 - half)) / (2 * half), 0, 1)
    colour = mix(np.full((SIZE, SIZE, 3), (64, 22, 156), float), (10, 7, 18), t ** 0.9)
    glow = np.exp(-(((x - SIZE / 2) / 330) ** 2 + ((y - SIZE / 2) / 250) ** 2))
    colour = mix(colour, (139, 92, 246), glow * 0.55)
    edge = np.clip(1.6 - np.abs(rounded_square(x, y, half, radius) - 2), 0, 1) * 0.35
    colour = mix(colour, (190, 160, 255), edge * (1 - t))  # a thin light rim along the top
    bars = np.zeros((SIZE, SIZE))
    width, gap = 64, 34
    start = SIZE / 2 - (len(BARS) * width + (len(BARS) - 1) * gap) / 2 + width / 2
    for index, level in enumerate(BARS):
        cx, length = start + index * (width + gap), 470 * level
        dy = np.maximum(np.abs(y - SIZE / 2) - (length - width) / 2, 0)  # a capsule: round ends
        bars = np.maximum(bars, np.clip(width / 2 - np.hypot(x - cx, dy) + 0.5, 0, 1))
    shade = mix(np.full((SIZE, SIZE, 3), (255, 255, 255), float), (214, 198, 255), np.clip((y - 300) / 420, 0, 1))
    colour = mix(colour, shade, bars)
    alpha = tile * 255
    return np.dstack([colour, alpha]).round().clip(0, 255).astype(np.uint8)


def write_png(path, pixels):
    height, width, _ = pixels.shape
    raw = b"".join(b"\0" + pixels[row].tobytes() for row in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    ASSETS.mkdir(exist_ok=True)
    png = ASSETS / "AppIcon.png"
    write_png(png, draw())
    write_png(ASSETS / "AppIcon-square.png", draw(square=True))
    with tempfile.TemporaryDirectory() as folder:
        iconset = Path(folder) / "AppIcon.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["/usr/bin/sips", "-z", str(points * scale), str(points * scale), str(png),
                                "--out", str(iconset / name)], check=True, capture_output=True)
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "AppIcon.icns")], check=True)
    print("wrote", png, "and", ASSETS / "AppIcon.icns")


if __name__ == "__main__":
    main()
