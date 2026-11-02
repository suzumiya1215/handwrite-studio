"""网格服务 —— 把 GridSpec 变成一组可绘制的单元格（L3 服务层）。

设计依据：
- 模块设计说明书 §4.5
- 数据格式规范 §2.3（坐标口径）
- 接口契约与测试用例 §6

★ 坐标口径（与 models/grid.py 一致，不得改）::

    单元格 (row, col)：
      left   = start_x_mm + col * col_width_mm
      top    = start_y_mm + row * row_height_mm

★ px 在**构建时一次算好**（契约 C4），下游不再重复换算 ——
  既不会漏传 dpi，也避免反复 round 累积误差。
"""

from __future__ import annotations

from .. import geometry as geo
from ..errors import GridOutOfBoundsError
from ..models.grid import CellRect, GridSpec
from ..models.template import PageGeometry

#: 界面拖拽时的最小格子尺寸（mm）—— 防止拖成 0 宽
MIN_CELL_SIZE_MM = 2.0


# ─────────────────────────────────────────────────────────────
# 构建
# ─────────────────────────────────────────────────────────────


def build_cells(
    spec: GridSpec,
    page: PageGeometry,
    dpi: int,
    *,
    only_active_cols: bool = True,
    validate_bounds: bool = True,
) -> list[CellRect]:
    """按网格参数生成全部单元格。

    Args:
        spec: 网格参数
        page: 页面几何（用来做越界检查）
        dpi: px 换算用的分辨率（预览 150 / 导出 300）
        only_active_cols: 只返回参与填充的列（默认 True）。
            界面**画网格线**时要传 False —— 未激活的列也要显示出来，
            否则用户不知道自己漏勾了哪一列。
        validate_bounds: 超出页面时警告（不抛异常，降级处理）

    Returns:
        CellRect 列表，按 row 再 col 排序。
    """
    cols = spec.effective_cols if only_active_cols else spec.all_cols
    out: list[CellRect] = []

    for r in range(spec.rows):
        for c in cols:
            out.append(cell_at(spec, r, c, dpi))

    return out


def cell_at(spec: GridSpec, row: int, col: int, dpi: int) -> CellRect:
    """算单个单元格的几何。越界抛 GridOutOfBoundsError。"""
    if not (0 <= row < spec.rows):
        raise GridOutOfBoundsError(
            f"行号 {row} 超出范围（共 {spec.rows} 行）。"
        )
    if not (0 <= col < spec.cols):
        raise GridOutOfBoundsError(
            f"列号 {col} 超出范围（共 {spec.cols} 列）。"
        )

    left = spec.start_x_mm + col * spec.col_width_mm
    top = spec.start_y_mm + row * spec.row_height_mm
    rect_mm = geo.Rect(left, top, left + spec.col_width_mm, top + spec.row_height_mm)

    try:
        content_mm = rect_mm.inset(spec.cell_padding_mm)
    except ValueError:
        # 内边距过大：退化为不留边距，而不是崩掉
        content_mm = rect_mm

    return CellRect(
        row=row,
        col=col,
        rect_mm=rect_mm,
        content_mm=content_mm,
        rect_px=rect_mm.to_px(dpi),
        content_px=content_mm.to_px(dpi),
    )


def build_cells_for_page(
    spec: GridSpec,
    page: PageGeometry,
    dpi: int,
) -> list[CellRect]:
    """构建并做越界检查（界面用，会把警告收进 GridSpec 之外的地方）。"""
    cells = build_cells(spec, page, dpi)
    return cells


# ─────────────────────────────────────────────────────────────
# 检查
# ─────────────────────────────────────────────────────────────


def out_of_bounds(spec: GridSpec, page: PageGeometry) -> list[str]:
    """网格有没有超出页面。返回中文警告列表。"""
    w: list[str] = []
    if spec.right_mm > page.width_mm + 0.01:
        over = spec.right_mm - page.width_mm
        w.append(f"网格右边超出页面 {over:.1f} 毫米")
    if spec.bottom_mm > page.height_mm + 0.01:
        over = spec.bottom_mm - page.height_mm
        w.append(f"网格下边超出页面 {over:.1f} 毫米")
    if spec.start_x_mm < -0.01 or spec.start_y_mm < -0.01:
        w.append("网格起点在页面外（负坐标）")
    return w


def validate(spec: GridSpec, page: PageGeometry | None = None) -> list[str]:
    """参数自查 + 越界检查，合并成一份中文提示。"""
    w = list(spec.validate())
    if page is not None:
        w.extend(out_of_bounds(spec, page))
    return w


# ─────────────────────────────────────────────────────────────
# 界面交互：拖拽 / 缩放
# ─────────────────────────────────────────────────────────────


def move_grid(spec: GridSpec, dx_mm: float, dy_mm: float) -> GridSpec:
    """整体平移网格，返回**新**参数。"""
    new = spec.copy()
    new.start_x_mm = spec.start_x_mm + dx_mm
    new.start_y_mm = spec.start_y_mm + dy_mm
    return new


def resize_cell(
    spec: GridSpec,
    col_width_mm: float | None = None,
    row_height_mm: float | None = None,
) -> GridSpec:
    """改列宽/行高，返回**新**参数。带下限保护。"""
    new = spec.copy()
    if col_width_mm is not None:
        new.col_width_mm = max(MIN_CELL_SIZE_MM, float(col_width_mm))
    if row_height_mm is not None:
        new.row_height_mm = max(MIN_CELL_SIZE_MM, float(row_height_mm))
    return new


def fit_to_page(
    spec: GridSpec,
    page: PageGeometry,
    *,
    margin_mm: float = 10.0,
) -> GridSpec:
    """按行数/列数把网格均分到页面可用区域。

    「自动铺满」按钮用。保留行数列数，让格子大小自适应。
    """
    avail_w = max(MIN_CELL_SIZE_MM, page.width_mm - margin_mm * 2)
    avail_h = max(MIN_CELL_SIZE_MM, page.height_mm - margin_mm * 2)

    new = spec.copy()
    new.start_x_mm = margin_mm
    new.start_y_mm = margin_mm
    new.col_width_mm = max(MIN_CELL_SIZE_MM, avail_w / max(1, spec.cols))
    new.row_height_mm = max(MIN_CELL_SIZE_MM, avail_h / max(1, spec.rows))
    return new


def set_grid_size(spec: GridSpec, rows: int, cols: int) -> GridSpec:
    """改行列数，返回**新**参数。旧的行列数越界时会被箝位。"""
    new = spec.copy()
    new.rows = max(1, min(200, int(rows)))
    new.cols = max(1, min(50, int(cols)))
    # 清掉越界的激活列
    new.active_cols = [c for c in spec.active_cols if 0 <= c < new.cols]
    return new


def auto_fit_cols(spec: GridSpec, page: PageGeometry) -> GridSpec:
    """按列宽把列数自动算出来（「铺满一行的格子数」）。"""
    avail = page.width_mm - spec.start_x_mm * 2
    if spec.col_width_mm <= 0:
        return spec
    cols = int(avail // spec.col_width_mm)
    return set_grid_size(spec, spec.rows, max(1, cols))


def auto_fit_rows(spec: GridSpec, page: PageGeometry) -> GridSpec:
    """按行高把行数自动算出来。"""
    avail = page.height_mm - spec.start_y_mm * 2
    if spec.row_height_mm <= 0:
        return spec
    rows = int(avail // spec.row_height_mm)
    return set_grid_size(spec, max(1, rows), spec.cols)


# ─────────────────────────────────────────────────────────────
# 命中测试（鼠标操作）
# ─────────────────────────────────────────────────────────────


def hit_test(cells: list[CellRect], x_px: float, y_px: float) -> CellRect | None:
    """找出点在哪一格。没有则返回 None。"""
    for c in cells:
        if c.contains_px(x_px, y_px):
            return c
    return None


def hit_test_border(
    cells: list[CellRect],
    x_px: float,
    y_px: float,
    *,
    tolerance_px: float = 4.0,
) -> tuple[int, int, str] | None:
    """判断点是否落在网格的**外边框**上（用于拖拽整体）。

    Returns:
        (row, col, 边名) 其中边名是 "left"/"right"/"top"/"bottom"，没命中返回 None。
    """
    if not cells:
        return None
    rows = max(c.row for c in cells)
    cols = max(c.col for c in cells)
    spec_left = min(c.rect_px[0] for c in cells)
    spec_top = min(c.rect_px[1] for c in cells)
    spec_right = max(c.rect_px[2] for c in cells)
    spec_bottom = max(c.rect_px[3] for c in cells)

    t = tolerance_px
    if abs(x_px - spec_left) <= t and spec_top - t <= y_px <= spec_bottom + t:
        return rows, 0, "left"
    if abs(x_px - spec_right) <= t and spec_top - t <= y_px <= spec_bottom + t:
        return rows, cols, "right"
    if abs(y_px - spec_top) <= t and spec_left - t <= x_px <= spec_right + t:
        return 0, cols, "top"
    if abs(y_px - spec_bottom) <= t and spec_left - t <= x_px <= spec_right + t:
        return rows, cols, "bottom"
    return None


# ─────────────────────────────────────────────────────────────
# 列选择
# ─────────────────────────────────────────────────────────────


def toggle_col(spec: GridSpec, col: int) -> GridSpec:
    """切换某一列是否参与填充。

    ★ 语义细节：`active_cols` 为空 = **全部列**。
      所以在「全选」状态下取消一列，必须先把「全部」展开成显式列表，
      否则「去掉第 3 列」会变成「只填第 3 列」。
    """
    new = spec.copy()
    if not (0 <= col < new.cols):
        return new

    current = set(new.effective_cols)          # 展开成显式集合
    if col in current:
        current.discard(col)
    else:
        current.add(col)

    # 如果又变回全选，就存成空列表（省空间，语义也一致）
    if current == set(range(new.cols)):
        new.active_cols = []
    else:
        new.active_cols = sorted(current)
    return new


def set_all_cols(spec: GridSpec, active: bool) -> GridSpec:
    """全选 / 全不选。全选用空列表表示。"""
    new = spec.copy()
    new.active_cols = [] if active else []
    if not active:
        # 全不选没法用空列表表达（空=全部），只能显式给一个不可能的值
        # → 用 [-1] 会被 effective_cols 过滤掉，得到空列表
        new.active_cols = [-1]
    return new


def is_col_active(spec: GridSpec, col: int) -> bool:
    return col in spec.effective_cols


def active_summary(spec: GridSpec) -> str:
    """给界面显示的「参与填充的列」摘要。"""
    cols = spec.effective_cols
    if len(cols) == spec.cols:
        return f"全部 {spec.cols} 列"
    if not cols:
        return "没有选中任何列"
    if len(cols) <= 6:
        return "第 " + "、".join(str(c + 1) for c in cols) + " 列"
    return f"{len(cols)} 列（第 {cols[0]+1} ~ 第 {cols[-1]+1} 列之间）"


# ─────────────────────────────────────────────────────────────
# 预览用：网格线
# ─────────────────────────────────────────────────────────────


def grid_lines(
    spec: GridSpec,
    page: PageGeometry,
    dpi: int,
) -> list[tuple[tuple[int, int], tuple[int, int], bool]]:
    """生成网格线的端点列表，供预览叠加绘制。

    Returns:
        [((x0,y0), (x1,y1), is_active), ...]
        is_active 表示这条线围出的列是否参与填充（界面用不同颜色画）。
    """
    left = geo.mm_to_px_r(spec.start_x_mm, dpi)
    top = geo.mm_to_px_r(spec.start_y_mm, dpi)
    right = geo.mm_to_px_r(spec.right_mm, dpi)
    bottom = geo.mm_to_px_r(spec.bottom_mm, dpi)

    lines: list[tuple[tuple[int, int], tuple[int, int], bool]] = []
    active = set(spec.effective_cols)

    # 竖线
    for c in range(spec.cols + 1):
        x = geo.mm_to_px_r(spec.start_x_mm + c * spec.col_width_mm, dpi)
        is_act = (c - 1) in active or c in active
        lines.append(((x, top), (x, bottom), is_act))

    # 横线
    for r in range(spec.rows + 1):
        y = geo.mm_to_px_r(spec.start_y_mm + r * spec.row_height_mm, dpi)
        lines.append(((left, y), (right, y), True))

    return lines


def cell_index_map(cells: list[CellRect]) -> dict[tuple[int, int], CellRect]:
    """(row, col) → CellRect 的查表。"""
    return {c.key: c for c in cells}
