"""生成 BookDB 应用图标（packaging/assets/bookdb.ico）—— **纯标准库**，无需 Pillow。

设计：圆角方形底 + 白色「书」的简笔图形（两条书脊 + 书页）。
输出多尺寸 ICO（16/32/48/64/128/256），供 Inno Setup 与 Windows 使用。
"""

from __future__ import annotations

import struct
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "packaging" / "assets" / "bookdb.ico"

#: 主题色（与 GUI 的 --accent 一致）
ACCENT = (27, 122, 90)
ACCENT_DARK = (17, 92, 66)
PAPER = (255, 255, 255)
PAPER_EDGE = (225, 235, 230)


def _blend(bg, fg, alpha):
    return tuple(round(b * (1 - alpha) + f * alpha) for b, f in zip(bg, fg))


def make_rgba(size: int) -> bytearray:
    """画一张 size×size 的 RGBA 位图。"""
    w = h = size
    px = bytearray(w * h * 4)
    radius = size * 0.22
    # 书本区域（相对尺寸）
    bx0, bx1 = size * 0.24, size * 0.76
    by0, by1 = size * 0.20, size * 0.80
    spine = size * 0.05

    for y in range(h):
        for x in range(w):
            cx, cy = x + 0.5, y + 0.5

            # ── 圆角方形底
            dx = max(radius - cx, cx - (w - radius), 0)
            dy = max(radius - cy, cy - (h - radius), 0)
            corner_dist = (dx * dx + dy * dy) ** 0.5
            if corner_dist > radius:
                rgba = (0, 0, 0, 0)                      # 圆角外：透明
            else:
                # 轻微竖向渐变，显得有质感
                t = cy / h
                rgba = (*_blend(ACCENT, ACCENT_DARK, t * 0.55), 255)

            # ── 书本（白色纸页）
            if rgba[3] and bx0 <= cx <= bx1 and by0 <= cy <= by1:
                inner = bx0 + spine <= cx <= bx1 - size * 0.03
                if inner:
                    rgba = (*PAPER, 255)
                    # 中缝（书脊折痕）
                    mid = (bx0 + bx1) / 2
                    if abs(cx - mid) <= max(1, size * 0.012):
                        rgba = (*PAPER_EDGE, 255)
                else:
                    rgba = (*PAPER_EDGE, 255)

            i = (y * w + x) * 4
            px[i:i + 4] = bytes(rgba)

    # ── 书签（红色小飘带，增加辨识度）
    mark_w = max(2, int(size * 0.055))
    mx0 = int(size * 0.60)
    my0 = int(size * 0.20)
    my1 = int(size * 0.34)
    for y in range(my0, my1):
        for x in range(mx0, mx0 + mark_w):
            if x < w and y < h:
                i = (y * w + x) * 4
                px[i:i + 4] = bytes((198, 60, 48, 255))
    return px


def bmp_bytes(size: int) -> bytes:
    """ICO 内嵌的 BMP（BITMAPINFOHEADER + BGRA 自下而上 + AND 掩码）。"""
    px = make_rgba(size)
    header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, len(px), 0, 0, 0, 0,
    )
    rows = bytearray()
    for y in range(size - 1, -1, -1):                    # BMP 自下而上
        row = px[y * size * 4:(y + 1) * size * 4]
        for x in range(size):                            # BGRA
            r, g, b, a = row[x * 4:x * 4 + 4]
            rows += bytes((b, g, r, a))
    # AND 掩码：每行按 4 字节对齐；32 位图此掩码可全 0
    and_row = ((size + 31) // 32) * 4
    return header + bytes(rows) + bytes(and_row * size)


def main() -> None:
    sizes = [16, 32, 48, 64, 128, 256]
    images = [bmp_bytes(s) for s in sizes]
    out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for size, data in zip(sizes, images):
        out += struct.pack(
            "<BBBBHHII",
            0 if size == 256 else size,       # 256 记作 0
            0 if size == 256 else size,
            0, 0, 1, 32, len(data), offset,
        )
        offset += len(data)
    for data in images:
        out += data

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(out)
    print(f"✓ {OUT}  ({OUT.stat().st_size // 1024} KB，{len(sizes)} 个尺寸)")


if __name__ == "__main__":
    main()
