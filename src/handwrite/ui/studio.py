"""主工作台 —— 左侧调参数、右侧看预览。

设计依据：
- 界面原型.html 屏 2（工具条 / 侧栏 5 组 / 预览 + 分页器）
- 需求文档 §6（界面结构）、§7（交互流程）
- 模块设计说明书 §4.11（studio_window）

结构：
    ┌── 工具条：新建 / 打开 / 保存 / 导出 PDF … 字形库选择 / ?
    ├── 主体 ──┬── 侧栏（滚动）
    │          │   ① 网格标定   ② 行列数   ③ 参与填充的列
    │          │   ④ 填写内容   ⑤ 抖动参数
    │          └── 预览：画布 + 底部条（分页 / 应用到所有页 / 显示网格 / 缩放）
    └── 状态栏

★ 本窗口只做「呈现 + 发信号」，不碰文件系统、不算抖动、不做 mm↔px 换算。
  所有数据都从外面灌进来（`set_project` / `set_page` / `set_fill_image`），
  所有用户动作都发信号出去（`request_*`），由 main.py 的 MainController 接。
  —— 这样界面可以脱开内核单独构造与测试（靠 offscreen 平台跑）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.charset import CHARSET_V1
from .widgets import (
    CanvasLayers,
    CollapsibleGroup,
    ColumnChecks,
    FieldRow,
    PresetButtons,
    PreviewCanvas,
    SegmentedRadios,
    SliderRow,
    TextRow,
    make_button,
    make_hint,
)

# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────

#: 抖动预设 → 三档强度。键要与 models/jitter.py 的 preset 字段对得上。
JITTER_PRESETS = [
    ("light", "轻微"),
    ("natural", "自然"),
    ("bold", "明显"),
]

#: 填写内容的三种模式
CONTENT_MODES = [
    ("random", "随机数字 / 小数"),
    ("range", "指定区间（如 36.0 ~ 37.5）"),
    ("manual", "手动录入"),
]


def _label(text: str, role: str = "") -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setObjectName(role)
    lb.setWordWrap(True)
    return lb


def _sep() -> QFrame:
    s = QFrame()
    s.setObjectName("ToolbarSep")
    s.setFixedWidth(1)
    return s


# ─────────────────────────────────────────────────────────────
# 侧栏
# ─────────────────────────────────────────────────────────────


class ParamSidebar(QScrollArea):
    """左侧 5 组参数。改任何一项都发对应信号出去。"""

    # 网格标定
    grid_changed = Signal(str, float)          # (字段名, 新值)
    # 行列数
    size_changed = Signal(int, int)            # (rows, cols)
    # 参与列
    cols_changed = Signal(list)                # 参与列（0 基）
    # 填写内容
    content_mode_changed = Signal(str)         # random / range / manual
    content_changed = Signal(str, object)      # (字段名, 值)
    # 抖动
    jitter_preset_changed = Signal(str)
    jitter_changed = Signal(str, float)        # (参数路径, 值)
    seed_changed = Signal(str)                 # 随机种子文本
    request_open_manual = Signal()             # 点「手动录入」的编辑按钮

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Side")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(258)
        self.setMaximumWidth(360)

        host = QWidget()
        host.setObjectName("SideHost")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(12, 12, 12, 16)
        lay.setSpacing(8)

        self._build_grid(lay)
        self._build_size(lay)
        self._build_cols(lay)
        self._build_content(lay)
        self._build_jitter(lay)
        lay.addStretch(1)

        self.setWidget(host)

    # ── ① 网格标定 ───────────────────────────────────────────

    def _build_grid(self, parent: QVBoxLayout) -> None:
        g = CollapsibleGroup("网格标定")
        self.grp_grid = g

        self.f_start_x = FieldRow("起点 X", 12.5, unit="mm", minimum=0.0, maximum=2000.0)
        self.f_start_y = FieldRow("起点 Y", 45.0, unit="mm", minimum=0.0, maximum=2000.0)
        self.f_col_w = FieldRow("列宽", 15.0, unit="mm", minimum=1.0, maximum=500.0)
        self.f_row_h = FieldRow("行高", 12.0, unit="mm", minimum=1.0, maximum=500.0)
        self.f_pad = FieldRow("内边距", 1.0, unit="mm", minimum=0.0, maximum=100.0)

        for name, row in (
            ("start_x_mm", self.f_start_x), ("start_y_mm", self.f_start_y),
            ("col_width_mm", self.f_col_w), ("row_height_mm", self.f_row_h),
            ("cell_padding_mm", self.f_pad),
        ):
            row.changed.connect(lambda v, n=name: self.grid_changed.emit(n, v))
            g.add(row)

        g.add(make_hint("在右边预览图上按住 Shift 拖动网格，这里数字会跟着变。"))
        parent.addWidget(g)

    # ── ② 行列数 ─────────────────────────────────────────────

    def _build_size(self, parent: QVBoxLayout) -> None:
        g = CollapsibleGroup("行列数")
        self.grp_size = g

        self.f_rows = FieldRow("行数", 24, integer=True, minimum=1, maximum=200)
        self.f_cols = FieldRow("列数", 10, integer=True, minimum=1, maximum=60)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.f_rows, 1)
        row.addWidget(self.f_cols, 1)

        self.f_rows.changed.connect(lambda _v: self._emit_size())
        self.f_cols.changed.connect(lambda _v: self._emit_size())
        g.add(row)
        parent.addWidget(g)

    def _emit_size(self) -> None:
        self.size_changed.emit(int(self.f_rows.value()), int(self.f_cols.value()))

    # ── ③ 参与填充的列 ───────────────────────────────────────

    def _build_cols(self, parent: QVBoxLayout) -> None:
        g = CollapsibleGroup("参与填充的列")
        self.grp_cols = g

        self.col_checks = ColumnChecks()
        self.col_checks.changed.connect(self.cols_changed.emit)
        g.add(self.col_checks)

        self.cols_summary = make_hint("")
        g.add(self.cols_summary)

        ops = QHBoxLayout()
        ops.setSpacing(6)
        ops.addWidget(make_button("全选", "", lambda: self.col_checks.set_all(True)))
        ops.addWidget(make_button("全不选", "", lambda: self.col_checks.set_all(False)))
        ops.addStretch(1)
        g.add(ops)

        g.add(make_hint("取消勾选的列会留空，比如表格里不需要填的栏目。"))
        self.col_checks.changed.connect(self._refresh_cols_summary)
        self._refresh_cols_summary(list(range(10)))
        parent.addWidget(g)

    def _refresh_cols_summary(self, active: list) -> None:
        n = self.col_checks._cols
        if len(active) == n:
            self.cols_summary.setText(f"{n} 列全部参与填充")
        elif not active:
            self.cols_summary.setText("没有任何列参与填充 —— 导出的还是空白表")
        else:
            # 连续段压缩成「1-3、5、8-10」这种更好读
            self.cols_summary.setText("参与：" + _ranges_text([c + 1 for c in active]))

    # ── ④ 填写内容 ───────────────────────────────────────────

    def _build_content(self, parent: QVBoxLayout) -> None:
        g = CollapsibleGroup("填写内容")
        self.grp_content = g

        self.mode = SegmentedRadios(CONTENT_MODES, current="range")
        self.mode.changed.connect(self._on_mode)
        g.add(self.mode)

        # ── 区间/小数参数（随机与区间共用）──
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 6, 0, 0)
        bl.setSpacing(8)

        self.f_min = FieldRow("最小值", 36.0, decimals=1, step=0.1)
        self.f_max = FieldRow("最大值", 37.5, decimals=1, step=0.1)
        self.f_decimals = FieldRow("小数位数", 1, integer=True, minimum=0, maximum=6)
        self.f_len_min = FieldRow("最少字数", 2, integer=True, minimum=1, maximum=12)
        self.f_len_max = FieldRow("最多字数", 4, integer=True, minimum=1, maximum=12)

        for name, row in (
            ("min", self.f_min), ("max", self.f_max),
            ("decimals", self.f_decimals),
            ("length_min", self.f_len_min), ("length_max", self.f_len_max),
        ):
            row.changed.connect(lambda v, n=name: self.content_changed.emit(n, v))
            bl.addWidget(row)

        self.manual_btn = make_button(
            "打开录入表格…", "Primary", self.request_open_manual.emit)
        bl.addWidget(self.manual_btn)

        self.manual_hint = make_hint("手动录入支持从 Excel / WPS 整块粘贴。")
        bl.addWidget(self.manual_hint)

        self._range_box = box
        g.add(box)
        self._on_mode("range")
        parent.addWidget(g)

    def _on_mode(self, mode: str) -> None:
        is_manual = mode == "manual"
        self._range_box.setVisible(not is_manual)
        self.content_mode_changed.emit(mode)

    # ── ⑤ 抖动参数 ───────────────────────────────────────────

    def _build_jitter(self, parent: QVBoxLayout) -> None:
        g = CollapsibleGroup("抖动参数")
        self.grp_jitter = g

        self.presets = PresetButtons(JITTER_PRESETS, current="natural")
        self.presets.picked.connect(self.jitter_preset_changed.emit)
        g.add(self.presets)

        # ── 一、单元整体随机 ──
        g.add(_label("一、单元整体随机", "SubHead"))
        self.s_cell_ox = self._slider(g, "cell.offset_x", "左右偏移", 0.0, 0.5, 0.08)
        self.s_cell_oy = self._slider(g, "cell.offset_y", "上下偏移", 0.0, 0.5, 0.08)
        self.s_cell_rot = self._slider(g, "cell.rotate_deg", "整体旋转", 0.0, 15.0, 3.0,
                                      scale=10, display="raw", fmt="{:.0f}",
                                      prefix="±", suffix="°")

        # ── 二、单字位置随机 ──
        g.add(_label("二、单字位置随机", "SubHead"))
        self.s_pos_ox = self._slider(g, "char_pos.offset_x", "左右", 0.0, 1.0, 0.10)
        self.s_pos_oy = self._slider(g, "char_pos.offset_y", "上下", 0.0, 1.0, 0.10)
        self.s_pos_sp = self._slider(g, "char_pos.spacing", "字间距", 0.0, 0.5, 0.08)

        # ── 三、单字形变随机 ──
        g.add(_label("三、单字形变随机", "SubHead"))
        self.s_shape_scale = self._slider(
            g, "char_shape.scale", "字体大小", 0.0, 0.4, 0.15,
            display="raw", fmt="{:.0f}", prefix="±")
        self.s_shape_bold = self._slider(
            g, "char_shape.bold_px_max", "字体加粗", 0.0, 3.0, 1.0,
            scale=10, display="raw", fmt="{:.0f}", suffix="px")
        self.s_shape_slant = self._slider(
            g, "char_shape.slant_deg", "字体倾度", 0.0, 20.0, 6.0,
            scale=10, display="raw", fmt="{:.0f}", prefix="±", suffix="°")
        self.s_shape_sx = self._slider(
            g, "char_shape.stretch_x", "水平拉伸", 0.0, 0.3, 0.08,
            display="raw", fmt="{:.0f}", prefix="±")
        self.s_shape_sy = self._slider(
            g, "char_shape.stretch_y", "垂直拉伸", 0.0, 0.3, 0.08,
            display="raw", fmt="{:.0f}", prefix="±")

        # ── 四、小数点随机 ──
        g.add(_label("四、小数点随机", "SubHead"))
        self.s_dot_off = self._slider(g, "dot.offset", "位置", 0.0, 0.3, 0.10)
        self.s_dot_scale = self._slider(
            g, "dot.scale", "大小", 0.0, 0.5, 0.15,
            display="raw", fmt="{:.0f}", prefix="±")

        # ── 种子 ──
        g.add(_label("随机种子", "SubHead"))
        self.seed = TextRow("种子", placeholder="留空 = 每次不同")
        self.seed.changed.connect(self.seed_changed.emit)
        g.add(self.seed)
        g.add(make_hint("填同一个种子，每次出来的字迹一模一样；留空则每次都不同。"))

        g.add(make_button("重置为默认", "Ghost", self.reset_jitter))
        parent.addWidget(g)

    def _slider(self, group: CollapsibleGroup, path: str, label: str,
                lo: float, hi: float, val: float, **kw) -> SliderRow:
        s = SliderRow(label, minimum=lo, maximum=hi, value=val, **kw)
        s.changed.connect(lambda v, p=path: self.jitter_changed.emit(p, v))
        group.add(s)
        return s

    # ── 对外：灌数据 ─────────────────────────────────────────

    def set_grid_values(self, **kw) -> None:
        """按字段名设网格值（不触发信号）。"""
        m = {
            "start_x_mm": self.f_start_x, "start_y_mm": self.f_start_y,
            "col_width_mm": self.f_col_w, "row_height_mm": self.f_row_h,
            "cell_padding_mm": self.f_pad,
        }
        for k, v in kw.items():
            if k in m and v is not None:
                m[k].set_value(float(v))

    def set_size_values(self, rows: int, cols: int) -> None:
        self.f_rows.set_value(rows)
        self.f_cols.set_value(cols)
        self.col_checks.set_cols(cols)

    def set_cols_active(self, active: list[int]) -> None:
        self.col_checks.set_active(active)
        self._refresh_cols_summary(active if active else list(range(self.col_checks._cols)))

    def set_content_values(self, mode: str = "", **kw) -> None:
        if mode:
            self.mode.set_current(mode)
            self._on_mode(mode)
        m = {
            "min": self.f_min, "max": self.f_max, "decimals": self.f_decimals,
            "length_min": self.f_len_min, "length_max": self.f_len_max,
        }
        for k, v in kw.items():
            if k in m and v is not None:
                m[k].set_value(float(v))

    def set_jitter_preset(self, preset: str) -> None:
        self.presets.set_current(preset)

    def set_jitter_values(self, d: dict) -> None:
        """d 是扁平 dict：{"cell.offset_x": 0.08, ...}。"""
        m = {
            "cell.offset_x": self.s_cell_ox, "cell.offset_y": self.s_cell_oy,
            "cell.rotate_deg": self.s_cell_rot,
            "char_pos.offset_x": self.s_pos_ox, "char_pos.offset_y": self.s_pos_oy,
            "char_pos.spacing": self.s_pos_sp,
            "char_shape.scale": self.s_shape_scale,
            "char_shape.bold_px_max": self.s_shape_bold,
            "char_shape.slant_deg": self.s_shape_slant,
            "char_shape.stretch_x": self.s_shape_sx,
            "char_shape.stretch_y": self.s_shape_sy,
            "dot.offset": self.s_dot_off, "dot.scale": self.s_dot_scale,
        }
        for k, v in (d or {}).items():
            if k in m and v is not None:
                m[k].set_value(float(v))

    def set_seed(self, seed) -> None:
        self.seed.set_text("" if seed is None else str(seed))

    def reset_jitter(self) -> None:
        """恢复成 JitterSpec 的默认值（与 models/jitter.py 的出厂值一致）。"""
        self.set_jitter_preset("natural")
        self.set_jitter_values({
            "cell.offset_x": 0.08, "cell.offset_y": 0.08, "cell.rotate_deg": 3.0,
            "char_pos.offset_x": 0.10, "char_pos.offset_y": 0.10,
            "char_pos.spacing": 0.08,
            "char_shape.scale": 0.15, "char_shape.bold_px_max": 1.0,
            "char_shape.slant_deg": 6.0,
            "char_shape.stretch_x": 0.08, "char_shape.stretch_y": 0.08,
            "dot.offset": 0.10, "dot.scale": 0.15,
        })
        self.jitter_preset_changed.emit("natural")


def _ranges_text(nums: list[int]) -> str:
    """把 [1,2,3,5,8,9,10] 压成 "1-3、5、8-10"。"""
    if not nums:
        return "（无）"
    nums = sorted(set(nums))
    out: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return "、".join(out)


# ─────────────────────────────────────────────────────────────
# 预览区
# ─────────────────────────────────────────────────────────────


class PreviewPane(QWidget):
    """右侧：画布 + 底部条。"""

    page_changed = Signal(int)              # 目标页码（0 基）
    show_grid_changed = Signal(bool)
    apply_all_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Canvas")
        self._page = 0
        self._count = 1

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── 画布本体 ──
        host = QFrame()
        host.setObjectName("CanvasBody")
        hl = QVBoxLayout(host)
        hl.setContentsMargins(16, 16, 16, 16)

        self.canvas = PreviewCanvas()
        hl.addWidget(self.canvas)
        lay.addWidget(host, 1)

        # ── 底部条 ──
        foot = QFrame()
        foot.setObjectName("CanvasFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(14, 8, 14, 8)
        fl.setSpacing(10)

        self.btn_prev = make_button("◀ 上一页", "", self.prev_page)
        self.btn_prev.setObjectName("Pager")
        self.btn_next = make_button("下一页 ▶", "", self.next_page)
        self.btn_next.setObjectName("Pager")
        self.page_lb = _label("第 1 / 1 页", "PagerText")

        fl.addWidget(self.btn_prev)
        fl.addWidget(self.page_lb)
        fl.addWidget(self.btn_next)

        fl.addSpacing(8)
        fl.addWidget(_sep())

        from PySide6.QtWidgets import QCheckBox
        self.chk_all = QCheckBox("应用到所有页")
        self.chk_all.setChecked(True)
        self.chk_all.stateChanged.connect(
            lambda _s: self.apply_all_changed.emit(self.chk_all.isChecked()))
        fl.addWidget(self.chk_all)

        self.chk_grid = QCheckBox("显示网格")
        self.chk_grid.setChecked(True)
        self.chk_grid.stateChanged.connect(self._on_grid_toggle)
        fl.addWidget(self.chk_grid)

        self.btn_orig = make_button("按住查看原图", "Ghost")
        self.btn_orig.setObjectName("Hold")
        self.btn_orig.pressed.connect(lambda: self.canvas.set_show_original(True))
        self.btn_orig.released.connect(lambda: self.canvas.set_show_original(False))
        fl.addWidget(self.btn_orig)

        fl.addStretch(1)

        self.zoom_lb = _label("缩放 100%", "ZoomText")
        self.zoom_lb.setObjectName("ZoomText")
        fl.addWidget(self.zoom_lb)

        self.btn_fit = make_button("适应", "Ghost", self.canvas.fit_to_window)
        self.btn_fit.setObjectName("Pager")
        fl.addWidget(self.btn_fit)

        lay.addWidget(foot)

        self.canvas.zoom_changed.connect(
            lambda z: self.zoom_lb.setText(f"缩放 {z}%"))
        self._refresh_pager()

    def _on_grid_toggle(self, _s: int) -> None:
        on = self.chk_grid.isChecked()
        self.canvas.set_show_grid(on)
        self.show_grid_changed.emit(on)

    def prev_page(self) -> None:
        self.set_page(self._page - 1, emit=True)

    def next_page(self) -> None:
        self.set_page(self._page + 1, emit=True)

    def set_page(self, index: int, *, emit: bool = False) -> None:
        index = max(0, min(self._count - 1, int(index)))
        if index == self._page and not emit:
            self._refresh_pager()
            return
        self._page = index
        self._refresh_pager()
        if emit:
            self.page_changed.emit(index)

    def set_page_count(self, n: int) -> None:
        self._count = max(1, int(n))
        if self._page >= self._count:
            self._page = self._count - 1
        self._refresh_pager()

    def page(self) -> int:
        return self._page

    def page_count(self) -> int:
        return self._count

    def _refresh_pager(self) -> None:
        self.page_lb.setText(f"第 {self._page + 1} / {self._count} 页")
        self.btn_prev.setEnabled(self._page > 0)
        self.btn_next.setEnabled(self._page < self._count - 1)

    # ── 转发给画布 ──

    def set_layers(self, layers: CanvasLayers, *, keep_view: bool = False) -> None:
        self.canvas.set_layers(layers, keep_view=keep_view)

    def set_fill(self, image) -> None:
        self.canvas.set_fill(image)

    def set_base(self, image) -> None:
        self.canvas.set_base(image)

    def apply_to_all(self) -> bool:
        return self.chk_all.isChecked()

    def set_apply_to_all(self, on: bool) -> None:
        self.chk_all.setChecked(bool(on))


# ─────────────────────────────────────────────────────────────
# 主工作台
# ─────────────────────────────────────────────────────────────


class StudioWindow(QWidget):
    """主工作台。

    信号（全部是「请求」，由 MainController 决定怎么做）：
        back_home           返回首页
        open_font_manage    打开字形库管理
        request_new         新建项目
        request_open        打开项目
        request_save        保存
        request_export      导出 PDF
        request_help        帮助
        request_fill        请求重新填充预览（参数变了）

    数据入口（全部是「灌」，界面自己不查数据）：
        set_font_name / set_status / set_project / set_page / set_fill_image
    """

    back_home = Signal()
    open_font_manage = Signal()
    request_new = Signal()
    request_open = Signal()
    request_save = Signal()
    request_export = Signal()
    request_help = Signal()
    request_fill = Signal()
    request_open_manual = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StudioWindow")
        self.setWindowTitle("手写字迹填充器")
        self.resize(1280, 840)

        self._suppress_fill = False      # 灌数据时别触发重填

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        # ── 主体：侧栏 | 预览 ──
        split = QSplitter(Qt.Horizontal)
        split.setObjectName("StudioSplit")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)

        self.sidebar = ParamSidebar()
        self.preview = PreviewPane()

        split.addWidget(self.sidebar)
        split.addWidget(self.preview)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 1000])
        root.addWidget(split, 1)

        root.addWidget(self._build_status())

        self._wire()

    # ── 工具条 ───────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Toolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(8)

        self.btn_back = make_button("← 返回首页", "Ghost", self.back_home.emit)
        bl.addWidget(self.btn_back)
        bl.addSpacing(6)

        self.btn_new = make_button("＋ 新建", "Primary", self.request_new.emit)
        self.btn_open = make_button("打开", "", self.request_open.emit)
        self.btn_save = make_button("保存", "", self.request_save.emit)
        for b in (self.btn_new, self.btn_open, self.btn_save):
            bl.addWidget(b)

        bl.addSpacing(4)
        bl.addWidget(_sep())
        bl.addSpacing(4)

        self.btn_export = make_button("导出 PDF", "Primary", self.request_export.emit)
        bl.addWidget(self.btn_export)

        bl.addStretch(1)

        self.font_pick = QPushButton("字形库：未选择")
        self.font_pick.setObjectName("FontPick")
        self.font_pick.setCursor(Qt.PointingHandCursor)
        self.font_pick.clicked.connect(self.open_font_manage.emit)
        bl.addWidget(self.font_pick)

        self.btn_help = make_button("?", "Ghost", self.request_help.emit)
        self.btn_help.setFixedWidth(32)
        bl.addWidget(self.btn_help)

        return bar

    # ── 状态栏 ───────────────────────────────────────────────

    def _build_status(self) -> QWidget:
        status = QFrame()
        status.setObjectName("StatusBar")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(14, 6, 14, 6)
        sl.setSpacing(10)

        self.status_lb = _label("就绪", "StatusText")
        sl.addWidget(self.status_lb, 1)

        self.hover_lb = _label("", "StatusHint")
        self.hover_lb.setObjectName("StatusHint")
        sl.addWidget(self.hover_lb, 0, Qt.AlignRight)

        return status

    # ── 接线 ─────────────────────────────────────────────────

    def _wire(self) -> None:
        # 任何参数变动都要重填预览（灌数据时用 _suppress_fill 屏蔽）
        self.sidebar.grid_changed.connect(self._on_param_changed)
        self.sidebar.size_changed.connect(self._on_param_changed)
        self.sidebar.cols_changed.connect(self._on_param_changed)
        self.sidebar.content_changed.connect(self._on_param_changed)
        self.sidebar.jitter_changed.connect(self._on_param_changed)
        self.sidebar.jitter_preset_changed.connect(self._on_param_changed)
        self.sidebar.seed_changed.connect(self._on_param_changed)
        self.sidebar.content_mode_changed.connect(lambda _m: self._emit_fill())
        self.sidebar.request_open_manual.connect(self.request_open_manual.emit)

        # 分页 / 显示开关
        self.preview.page_changed.connect(lambda _i: self.request_fill.emit())

        # 画布交互
        self.preview.canvas.cell_hovered.connect(self._on_hover)
        self.preview.canvas.grid_dragged.connect(self._on_grid_dragged)

    def _on_param_changed(self, *_a) -> None:
        self._emit_fill()

    def _emit_fill(self) -> None:
        if not self._suppress_fill:
            self.request_fill.emit()

    def _on_hover(self, row: int, col: int) -> None:
        if row < 0 or col < 0:
            self.hover_lb.setText("")
        else:
            self.hover_lb.setText(f"第 {row + 1} 行 · 第 {col + 1} 列")

    def _on_grid_dragged(self, dx_px: float, dy_px: float) -> None:
        """画布拖动网格 → 转成 mm 更新「起点」两个输入框。

        ★ 这里做 px→mm 的换算**只是为了让输入框跟上手指**，
          真正落盘的还是 mm 数值（契约 C4：px 只在贴图时算一次）。
        """
        dpi = self._drag_dpi
        if dpi <= 0:
            return
        k = 25.4 / dpi
        self.sidebar.f_start_x.set_value(self.sidebar.f_start_x.value() + dx_px * k)
        self.sidebar.f_start_y.set_value(self.sidebar.f_start_y.value() + dy_px * k)
        self.request_fill.emit()

    #: 拖动网格时用来换算的 DPI（由 set_page_meta 灌入）
    _drag_dpi: int = 150

    # ── 对外接口：灌数据 ─────────────────────────────────────

    def set_font_name(self, name: str) -> None:
        self.font_pick.setText("字形库：" + (name or "未选择"))
        self.font_pick.setProperty("ready", bool(name))
        self.font_pick.style().unpolish(self.font_pick)
        self.font_pick.style().polish(self.font_pick)

    def set_status(self, text: str) -> None:
        self.status_lb.setText(text)

    def set_page_meta(self, index: int, count: int, *, dpi: int = 150) -> None:
        """告诉界面现在是第几页、共几页、预览用的 DPI。"""
        self._drag_dpi = int(dpi) if dpi else 150
        self.preview.set_page_count(count)
        self.preview.set_page(index)

    def set_grid_spec(self, spec, *, suppress: bool = True) -> None:
        """把 GridSpec 灌进侧栏（suppress=True 时不触发重填）。"""
        old = self._suppress_fill
        self._suppress_fill = suppress
        try:
            self.sidebar.set_grid_values(
                start_x_mm=spec.start_x_mm, start_y_mm=spec.start_y_mm,
                col_width_mm=spec.col_width_mm, row_height_mm=spec.row_height_mm,
                cell_padding_mm=spec.cell_padding_mm,
            )
            self.sidebar.set_size_values(spec.rows, spec.cols)
            active = list(spec.active_cols) if spec.active_cols else list(range(spec.cols))
            self.sidebar.set_cols_active(active)
        finally:
            self._suppress_fill = old

    def set_content_spec(self, spec, *, suppress: bool = True) -> None:
        old = self._suppress_fill
        self._suppress_fill = suppress
        try:
            mode = "range" if spec.mode in ("random", "range") else spec.mode
            r = spec.random
            self.sidebar.set_content_values(
                mode=mode, min=r.min, max=r.max, decimals=r.decimals,
                length_min=r.length_min, length_max=r.length_max,
            )
        finally:
            self._suppress_fill = old

    def set_jitter_spec(self, spec, *, suppress: bool = True) -> None:
        old = self._suppress_fill
        self._suppress_fill = suppress
        try:
            self.sidebar.set_jitter_preset(spec.preset or "natural")
            cs = spec.char_shape
            # 面板用的是「半幅」表示法（±15% = scale_min 0.85 / max 1.15），
            # 存储用的是 [min, max]，这里换算成半幅给滑块。
            scale_half = max(abs(1.0 - cs.scale_min), abs(cs.scale_max - 1.0))
            sx_half = max(abs(1.0 - cs.stretch_x_min), abs(cs.stretch_x_max - 1.0))
            sy_half = max(abs(1.0 - cs.stretch_y_min), abs(cs.stretch_y_max - 1.0))
            self.sidebar.set_jitter_values({
                "cell.offset_x": spec.cell.offset_x,
                "cell.offset_y": spec.cell.offset_y,
                "cell.rotate_deg": spec.cell.rotate_deg,
                "char_pos.offset_x": spec.char_pos.offset_x,
                "char_pos.offset_y": spec.char_pos.offset_y,
                "char_pos.spacing": spec.char_pos.spacing,
                "char_shape.scale": scale_half,
                "char_shape.bold_px_max": cs.bold_px_max,
                "char_shape.slant_deg": cs.slant_deg,
                "char_shape.stretch_x": sx_half,
                "char_shape.stretch_y": sy_half,
                "dot.offset": spec.dot.offset,
                "dot.scale": max(abs(1.0 - spec.dot.scale_min),
                                 abs(spec.dot.scale_max - 1.0)),
            })
        finally:
            self._suppress_fill = old

    def set_seed(self, seed, *, suppress: bool = True) -> None:
        old = self._suppress_fill
        self._suppress_fill = suppress
        try:
            self.sidebar.set_seed(seed)
        finally:
            self._suppress_fill = old

    def set_layers(self, layers: CanvasLayers, *, keep_view: bool = False) -> None:
        self.preview.set_layers(layers, keep_view=keep_view)

    def set_fill_image(self, image) -> None:
        self.preview.set_fill(image)

    def set_apply_to_all(self, on: bool) -> None:
        self.preview.set_apply_to_all(on)

    def apply_to_all(self) -> bool:
        return self.preview.apply_to_all()

    def set_enabled_actions(self, *, save: bool = True, export: bool = True) -> None:
        """没打开模板时把「保存/导出」置灰 —— 比点了没反应好。"""
        self.btn_save.setEnabled(save)
        self.btn_export.setEnabled(export)

    # ── 组装 GridSpec / JitterSpec（供控制器取用）───────────

    def grid_values(self) -> dict:
        """侧栏当前的网格数值（控制器拿它去建 GridSpec）。"""
        b = self.sidebar
        return {
            "start_x_mm": b.f_start_x.value(),
            "start_y_mm": b.f_start_y.value(),
            "col_width_mm": b.f_col_w.value(),
            "row_height_mm": b.f_row_h.value(),
            "cell_padding_mm": b.f_pad.value(),
            "rows": int(b.f_rows.value()),
            "cols": int(b.f_cols.value()),
            "active_cols": b.col_checks.active(),
        }

    def content_values(self) -> dict:
        b = self.sidebar
        return {
            "mode": b.mode.current(),
            "min": b.f_min.value(),
            "max": b.f_max.value(),
            "decimals": int(b.f_decimals.value()),
            "length_min": int(b.f_len_min.value()),
            "length_max": int(b.f_len_max.value()),
        }

    def jitter_values(self) -> dict:
        """侧栏当前抖动值 → 可喂给 JitterSpec 的半幅 dict。"""
        b = self.sidebar
        return {
            "preset": b.presets.current(),
            "cell.offset_x": b.s_cell_ox.value(),
            "cell.offset_y": b.s_cell_oy.value(),
            "cell.rotate_deg": b.s_cell_rot.value(),
            "char_pos.offset_x": b.s_pos_ox.value(),
            "char_pos.offset_y": b.s_pos_oy.value(),
            "char_pos.spacing": b.s_pos_sp.value(),
            "char_shape.scale_half": b.s_shape_scale.value(),
            "char_shape.bold_px_max": b.s_shape_bold.value(),
            "char_shape.slant_deg": b.s_shape_slant.value(),
            "char_shape.stretch_x_half": b.s_shape_sx.value(),
            "char_shape.stretch_y_half": b.s_shape_sy.value(),
            "dot.offset": b.s_dot_off.value(),
            "dot.scale_half": b.s_dot_scale.value(),
        }

    def seed_value(self):
        t = self.sidebar.seed.text().strip()
        if not t:
            return None
        try:
            return int(t)
        except ValueError:
            # 允许任意文本 → 稳定映射成整数（同文本恒得同种子）
            return abs(hash(t)) % (2 ** 31)


# ─────────────────────────────────────────────────────────────
# 自检
# ─────────────────────────────────────────────────────────────


def _selftest() -> int:
    """开发期自检：构造窗口 + 检查关键控件都在。

    用法： .venv/Scripts/python.exe -m handwrite.ui.studio
    """
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    w = StudioWindow()

    fails = []
    for attr in ("btn_new", "btn_open", "btn_save", "btn_export", "font_pick",
                 "sidebar", "preview", "status_lb", "hover_lb"):
        if not hasattr(w, attr):
            fails.append(attr)

    # 五组参数都在
    for attr in ("grp_grid", "grp_size", "grp_cols", "grp_content", "grp_jitter"):
        if not hasattr(w.sidebar, attr):
            fails.append("sidebar." + attr)

    # 抖动滑块数量（3+3+5+2 = 13 个滑块需要能读到值）
    jv = w.jitter_values()
    if len(jv) != 14:                 # 13 个值 + preset
        fails.append(f"jitter_values 项数={len(jv)}")

    # GridSpec 往返
    from ..core.services.grid_service import GridSpec
    spec = GridSpec(start_x_mm=10.0, start_y_mm=20.0, col_width_mm=14.0,
                    row_height_mm=11.0, cell_padding_mm=1.2,
                    rows=18, cols=8, active_cols=[0, 1, 2])
    w.set_grid_spec(spec)
    gv = w.grid_values()
    if abs(gv["start_x_mm"] - 10.0) > 1e-6 or gv["rows"] != 18 or gv["cols"] != 8:
        fails.append(f"grid 往返失败 {gv}")
    if gv["active_cols"] != [0, 1, 2]:
        fails.append(f"参与列往返失败 {gv['active_cols']}")

    # 分页
    w.set_page_meta(0, 3)
    if w.preview.page_count() != 3:
        fails.append("分页数不对")
    w.preview.next_page()
    if w.preview.page() != 1:
        fails.append(f"翻页失败 {w.preview.page()}")

    # 字符集自检（12 个）
    if len(CHARSET_V1) != 12:
        fails.append(f"字符集不是 12 个：{len(CHARSET_V1)}")

    if fails:
        print("自检失败：", file=sys.stderr)
        for f in fails:
            print("  ✗", f, file=sys.stderr)
        return 1
    print(f"主工作台自检通过（{len(CHARSET_V1)} 个字符 / 13 个抖动参数 / 5 组面板）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
