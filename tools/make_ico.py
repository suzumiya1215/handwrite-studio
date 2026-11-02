"""生成应用图标 app.ico（多尺寸）。"""
from __future__ import annotations

from PIL import Image, ImageDraw


def _draw_icon(size: int) -> Image.Image:
    """画一个「手写表单」风格的图标。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # 背景圆角方块（深蓝）
    pad = int(s * 0.06)
    d.rounded_rectangle(
        [pad, pad, s - pad, s - pad],
        radius=int(s * 0.18),
        fill=(44, 82, 130, 255),
        outline=(71, 130, 206, 255),
        width=max(1, int(s * 0.015)),
    )

    # 白纸页（略倾斜）
    margin = int(s * 0.22)
    page = [
        (margin, margin + int(s * 0.04)),
        (s - margin + int(s * 0.02), margin - int(s * 0.02)),
        (s - margin - int(s * 0.02), s - margin + int(s * 0.04)),
        (margin - int(s * 0.04), s - margin - int(s * 0.02)),
    ]
    d.polygon(page, fill=(250, 250, 250, 255))

    # 手写横线（模拟数字笔迹）
    ink = (30, 30, 30, 255)
    y0 = margin + int(s * 0.18)
    line_w = max(1, int(s * 0.022))
    for i in range(4):
        y = y0 + i * int(s * 0.13)
        # 用多段小折线模拟手写抖动
        pts = []
        x = margin + int(s * 0.08)
        end_x = s - margin - int(s * 0.10)
        while x < end_x:
            pts.append((x, y + int(s * 0.01 * (1 if i % 2 else -1))))
            x += int(s * 0.08)
        if pts:
            d.line(pts, fill=ink, width=line_w, joint="curve")

    return img


def main() -> None:
    # PIL 对多尺寸 ICO 支持不稳定，先放一张 256×256，
    # Windows 会自动缩放；后续可用专用工具再压多尺寸。
    out = "app.ico"
    _draw_icon(256).save(out, format="ICO")
    print(f"已生成 {out}，尺寸：256×256")


if __name__ == "__main__":
    main()
