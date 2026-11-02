"""
坐标与单位换算 —— ★ 全项目唯一的 mm↔px 换算处。

设计依据：
- 需求文档 §7.1 / 数据格式规范 §5（换算与单位约定）
- 模块设计说明书 §4.2、§6.1（契约 C1 ~ C4）

为什么单独成模块：
    「mm 为存储单位，px 只在最终贴图时 round 一次」是本项目的基本口径。
    如果各模块自己写 `* dpi / 25.4`，会出现 25.4 这个常数散落十几处、
    精度口径不一致、行距累积误差。集中到这一个文件，改口径只需改一处。

铁律：
- C1：所有**存储**的长度用 mm
- C2：所有**计算**中途用 float，不做中间取整
- C3：唯一的 mm→px 转换点就是这里的函数（N3：别处不出现 25.4）
- C4：图像相关的 px 值在 CellRect 里预先算好，下游直接用
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: 英寸与毫米的换算常数 —— ★ 全项目只允许在这个文件里出现
MM_PER_INCH: float = 25.4

#: 常用 DPI 口径
DPI_PREVIEW: int = 150   # 界面预览
DPI_EXPORT: int = 300    # 正式导出（打印标准）


# ─────────────────────────────────────────────────────────────
# 单位换算
# ─────────────────────────────────────────────────────────────


def mm_to_px(mm: float, dpi: int) -> float:
    """毫米 → 像素。

    ★ 返回未取整的 float（契约 C2）。中间步骤不要取整，
      否则 24 行累加会偏出十几个像素，肉眼可见行距不均。
    """
    return mm * dpi / MM_PER_INCH


def px_to_mm(px: float, dpi: int) -> float:
    """像素 → 毫米。"""
    return px * MM_PER_INCH / dpi


def mm_to_px_r(mm: float, dpi: int) -> int:
    """毫米 → 像素（四舍五入）。

    ★ 只在最终贴图 / 画框的前一步调用（契约 C3）。
    """
    return int(round(mm_to_px(mm, dpi)))


def pt_to_mm(pt: float) -> float:
    """PDF 点 → 毫米。1 pt = 1/72 inch。"""
    return pt / 72.0 * MM_PER_INCH


def mm_to_pt(mm: float) -> float:
    """毫米 → PDF 点。"""
    return mm / MM_PER_INCH * 72.0


def page_size_px(width_mm: float, height_mm: float, dpi: int) -> tuple[int, int]:
    """页面尺寸 mm → 位图像素尺寸。"""
    return mm_to_px_r(width_mm, dpi), mm_to_px_r(height_mm, dpi)


# ─────────────────────────────────────────────────────────────
# 矩形
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rect:
    """轴对齐矩形。单位由调用方约定（mm 或 px）。

    原点约定：**页面左上角**（不是 PDF 内部的左下角）。
    """

    left: float
    top: float
    right: float
    bottom: float

    # ── 派生属性 ──────────────────────────────────────────
    @property
    def w(self) -> float:
        return self.right - self.left

    @property
    def h(self) -> float:
        return self.bottom - self.top

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> tuple[float, float]:
        return self.w, self.h

    # ── 运算 ─────────────────────────────────────────────
    def inset(self, d: float) -> "Rect":
        """四边向内收缩 d。d 过大（超过一半）时抛 ValueError。

        抛出而不是返回反向矩形 —— 反向矩形会在下游算出负宽高，
        变成难以定位的诡异 bug。
        """
        if d < 0:
            raise ValueError(f"内边距不能为负：{d}")
        if d * 2 >= min(self.w, self.h):
            raise ValueError(
                f"内边距 {d} 过大，超过矩形短边的一半（{min(self.w, self.h) / 2}）"
            )
        return Rect(self.left + d, self.top + d, self.right - d, self.bottom - d)

    def expand(self, d: float) -> "Rect":
        """四边向外扩张 d（允许负值等于收缩）。"""
        return Rect(self.left - d, self.top - d, self.right + d, self.bottom + d)

    def intersect(self, other: "Rect") -> "Rect | None":
        """求交。完全不相交返回 None。"""
        l = max(self.left, other.left)
        t = max(self.top, other.top)
        r = min(self.right, other.right)
        b = min(self.bottom, other.bottom)
        if r <= l or b <= t:
            return None
        return Rect(l, t, r, b)

    def contains(self, x: float, y: float) -> bool:
        """点是否在矩形内（含边界）。"""
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.left, self.top, self.right, self.bottom)

    def to_px(self, dpi: int) -> tuple[int, int, int, int]:
        """转成整数像素四元组（left, top, right, bottom）。

        ★ 这是唯一的 mm→整数 px 出口，内部统一 round。
        """
        return (
            mm_to_px_r(self.left, dpi),
            mm_to_px_r(self.top, dpi),
            mm_to_px_r(self.right, dpi),
            mm_to_px_r(self.bottom, dpi),
        )


def rect_mm_to_px(rect: Rect, dpi: int) -> tuple[int, int, int, int]:
    """Rect(mm) → 整数像素四元组。Rect.to_px 的函数式写法。"""
    return rect.to_px(dpi)


def rect_from_corners(x0: float, y0: float, x1: float, y1: float) -> Rect:
    """由任意两个对角点构造矩形（自动归一化，不要求左上/右下顺序）。"""
    return Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


# ─────────────────────────────────────────────────────────────
# 几何运算
# ─────────────────────────────────────────────────────────────


def rotated_bounds(w: float, h: float, deg: float) -> tuple[float, float]:
    """把 w×h 旋转 deg 度后的**外接矩形**尺寸。

    用于给旋转后的字形预留空间 —— 旋转 45° 时外接框会变成原来的 √2 倍，
    按原尺寸留位会把字裁掉。
    """
    rad = math.radians(abs(deg) % 180)
    cos_a = abs(math.cos(rad))
    sin_a = abs(math.sin(rad))
    return w * cos_a + h * sin_a, w * sin_a + h * cos_a


def fit_scale(
    src_w: float,
    src_h: float,
    box_w: float,
    box_h: float,
    *,
    allow_upscale: bool = True,
) -> float:
    """等比缩放到刚好放进 box 的倍数（取 min）。

    Args:
        allow_upscale: False 时上限为 1.0（小图不放大，避免糊掉）。
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"源尺寸必须为正：{src_w}×{src_h}")
    if box_w <= 0 or box_h <= 0:
        raise ValueError(f"目标尺寸必须为正：{box_w}×{box_h}")
    k = min(box_w / src_w, box_h / src_h)
    if not allow_upscale:
        k = min(k, 1.0)
    return k


def distribute(total: float, weights: list[float]) -> list[float]:
    """按权重分配一个总长度（用于一行里给每个字分宽度）。

    权重为 0 时平均分配。
    """
    if not weights:
        return []
    s = sum(weights)
    if s <= 0:
        return [total / len(weights)] * len(weights)
    return [total * w / s for w in weights]


def center_offset(outer: float, inner: float) -> float:
    """居中偏移量。"""
    return (outer - inner) / 2.0
