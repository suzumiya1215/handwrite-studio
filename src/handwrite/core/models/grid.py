"""网格数据模型。

设计依据：
- 数据格式规范 §2.3（grid 对象与坐标口径）
- 模块设计说明书 §4.5、§5.3

★ 坐标口径（不得改动，全项目统一）::

    单元格 (row, col) 的矩形（mm，原点在页面左上角）：
      left   = start_x_mm + col * col_width_mm
      top    = start_y_mm + row * row_height_mm
      right  = left + col_width_mm
      bottom = top  + row_height_mm

    内容区（去掉内边距后）：
      c_left = left + cell_padding_mm   ...（依此类推）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import geometry as geo

# ─────────────────────────────────────────────────────────────
# 网格参数
# ─────────────────────────────────────────────────────────────


@dataclass
class GridSpec:
    """网格标定参数（对应 .hwpj 的 grid 对象）。"""

    start_x_mm: float = 0.0
    start_y_mm: float = 0.0
    col_width_mm: float = 15.0
    row_height_mm: float = 12.0
    cell_padding_mm: float = 1.0
    rows: int = 24
    cols: int = 10
    #: ★ 空列表 = 全部列参与填充（数据格式规范定的语义）
    active_cols: list[int] = field(default_factory=list)
    #: 界面显示用的行号起点，**不影响 CellRect.row**（恒为 0-based）
    row_index_start: int = 1
    #: 预留：行序号步长基准，v1 恒 0
    row_index_base: int = 0

    # ── 派生 ─────────────────────────────────────────────
    @property
    def effective_cols(self) -> list[int]:
        """实际参与填充的列下标。

        ★ 注意判空方式：空列表 = 全部。不能写 `if self.active_cols`，
          那样空数组会走进错误分支。
        """
        if not self.active_cols:
            return list(range(self.cols))
        return sorted(c for c in self.active_cols if 0 <= c < self.cols)

    @property
    def all_cols(self) -> list[int]:
        return list(range(self.cols))

    @property
    def total_width_mm(self) -> float:
        return self.cols * self.col_width_mm

    @property
    def total_height_mm(self) -> float:
        return self.rows * self.row_height_mm

    @property
    def right_mm(self) -> float:
        return self.start_x_mm + self.total_width_mm

    @property
    def bottom_mm(self) -> float:
        return self.start_y_mm + self.total_height_mm

    @property
    def filled_cell_count(self) -> int:
        return self.rows * len(self.effective_cols)

    def display_row_number(self, row: int) -> int:
        """0-based row → 界面显示的行号。"""
        return row + self.row_index_start

    # ── 校验 ─────────────────────────────────────────────
    def validate(self) -> list[str]:
        """自查参数合理性，返回中文警告列表（空 = 没问题）。"""
        w: list[str] = []
        if self.col_width_mm <= 0:
            w.append("列宽必须大于 0")
        if self.row_height_mm <= 0:
            w.append("行高必须大于 0")
        if self.rows < 1:
            w.append("行数至少为 1")
        if self.cols < 1:
            w.append("列数至少为 1")
        if self.cell_padding_mm < 0:
            w.append("内边距不能为负")
        if self.cell_padding_mm * 2 >= min(self.col_width_mm, self.row_height_mm):
            w.append("内边距过大，超过单元格短边的一半")
        if self.rows > 200:
            w.append("行数超过上限 200")
        if self.cols > 50:
            w.append("列数超过上限 50")
        # 激活列越界
        bad_cols = [c for c in self.active_cols if not (0 <= c < self.cols)]
        if bad_cols:
            w.append(f"有 {len(bad_cols)} 个列号超出范围，已忽略")
        return w

    def copy(self) -> "GridSpec":
        import copy as _copy
        return _copy.deepcopy(self)

    def to_dict(self) -> dict:
        return {
            "start_x_mm": self.start_x_mm,
            "start_y_mm": self.start_y_mm,
            "col_width_mm": self.col_width_mm,
            "row_height_mm": self.row_height_mm,
            "cell_padding_mm": self.cell_padding_mm,
            "rows": self.rows,
            "cols": self.cols,
            "active_cols": list(self.active_cols),
            "row_index_start": self.row_index_start,
            "row_index_base": self.row_index_base,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GridSpec":
        g = cls()
        for k, v in (d or {}).items():
            if hasattr(g, k):
                setattr(g, k, v)
        return g


# ─────────────────────────────────────────────────────────────
# 单元格
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CellRect:
    """一个单元格的几何信息（mm 与 px 都有）。

    ★ px 值由 GridService 在构建时按目标 DPI 一次算好（契约 C4），
      下游直接用，不再重复换算 —— 既不漏传 dpi，也快。
    """

    row: int
    col: int
    rect_mm: geo.Rect
    content_mm: geo.Rect
    rect_px: tuple[int, int, int, int]
    content_px: tuple[int, int, int, int]

    @property
    def key(self) -> tuple[int, int]:
        return self.row, self.col

    # ── mm 侧快捷访问 ────────────────────────────────────
    @property
    def left_mm(self) -> float:
        return self.rect_mm.left

    @property
    def top_mm(self) -> float:
        return self.rect_mm.top

    @property
    def width_mm(self) -> float:
        return self.rect_mm.w

    @property
    def height_mm(self) -> float:
        return self.rect_mm.h

    @property
    def center_mm(self) -> tuple[float, float]:
        return self.rect_mm.cx, self.rect_mm.cy

    # ── px 侧快捷访问 ────────────────────────────────────
    @property
    def rect_px_rect(self) -> geo.Rect:
        l, t, r, b = self.rect_px
        return geo.Rect(float(l), float(t), float(r), float(b))

    @property
    def content_px_rect(self) -> geo.Rect:
        l, t, r, b = self.content_px
        return geo.Rect(float(l), float(t), float(r), float(b))

    @property
    def content_size_px(self) -> tuple[int, int]:
        l, t, r, b = self.content_px
        return max(0, r - l), max(0, b - t)

    @property
    def center_px(self) -> tuple[float, float]:
        l, t, r, b = self.rect_px
        return (l + r) / 2.0, (t + b) / 2.0

    @property
    def content_center_px(self) -> tuple[float, float]:
        l, t, r, b = self.content_px
        return (l + r) / 2.0, (t + b) / 2.0

    def contains_px(self, x: float, y: float) -> bool:
        """点（像素）是否落在本格。界面 hover 判定用。"""
        l, t, r, b = self.rect_px
        return l <= x <= r and t <= y <= b
