"""Draws the kikitori app icon source (1024x1024 PNG) with the standard library.

Role: produces `app/src-tauri/icons/source/icon.png`, the input for
`npx tauri icon`, so the icon is original artwork and reproducible. The mark is
two subtitle lines on an indigo tile: a short white original line above a long
subtitle-yellow translation line, the same colours as the overlay.

Usage (from the repo root): uv run --no-project python app/dev/make_icon.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZE = 1024
INDIGO = (0x22, 0x30, 0x5C)
WHITE = (0xF4, 0xF6, 0xFB)
YELLOW = (0xF2, 0xC2, 0x30)

Rect = tuple[float, float, float, float, float]  # cx, cy, half_w, half_h, radius


def rounded_rect_distance(x: float, y: float, rect: Rect) -> float:
    """Signed distance to a rounded rectangle (negative inside)."""
    cx, cy, hw, hh, r = rect
    qx = abs(x - cx) - (hw - r)
    qy = abs(y - cy) - (hh - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    return outside + inside - r


def coverage(distance: float) -> float:
    """Analytic anti-aliasing: one-pixel ramp across the edge."""
    return min(1.0, max(0.0, 0.5 - distance))


def render() -> bytes:
    tile: Rect = (512, 512, 448, 448, 200)
    bars: list[tuple[Rect, tuple[int, int, int]]] = [
        ((392, 616, 150, 44, 44), WHITE),
        ((664, 616, 92, 44, 44), WHITE),
        ((512, 760, 330, 62, 62), YELLOW),
    ]
    rows = bytearray()
    for py in range(SIZE):
        rows.append(0)  # PNG filter type 0 for this scanline
        y = py + 0.5
        for px in range(SIZE):
            x = px + 0.5
            alpha = coverage(rounded_rect_distance(x, y, tile))
            if alpha <= 0.0:
                rows += b"\x00\x00\x00\x00"
                continue
            r, g, b = (float(c) for c in INDIGO)
            for rect, color in bars:
                a = coverage(rounded_rect_distance(x, y, rect))
                if a > 0.0:
                    r = r * (1 - a) + color[0] * a
                    g = g * (1 - a) + color[1] * a
                    b = b * (1 - a) + color[2] * a
            rows += bytes((round(r), round(g), round(b), round(alpha * 255)))
    return bytes(rows)


def png(width: int, height: int, raw_rgba_rows: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw_rgba_rows, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    out = (
        Path(__file__).resolve().parent.parent
        / "src-tauri"
        / "icons"
        / "source"
        / "icon.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png(SIZE, SIZE, render()))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
