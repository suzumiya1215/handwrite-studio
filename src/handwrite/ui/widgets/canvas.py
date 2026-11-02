"""预览画布 —— 把纸张、网格线、手写填充三张图层叠在一起给人看。

设计依据：
- 界面原型.html 屏 2 的 `.canvas` / `.paper` / `.canvas-foot`
- 模块设计说明书 §4.11（studio_window 的预览区）
- 契约 C4（px 在构建时一次算好）

★ 这个控件是「只读展示 + 交互」层，**不做任何 mm↔px 换算和抖动计算**。
  它拿到的是三样现成的像素数据：
    ① 底图 PIL.Image（模板页渲染结果）
    ② 网格线列表（来自 grid_service.grid_lines）
    ③ 填充结果 PIL.Image（来自 fill_service.render_page）
  自己只负责「缩放 + 叠加 + 画」。
  —— 这样它就不会偷偷引入第二套坐标系，C4 契约才守得住。

交互（对应原型屏 2 的预览区）：
- 滚轮缩放（Ctrl+滚轮 或 直接滚轮）
- 空格/中键拖动平移
- 拖动网格（M4.3 接上，本版先留出拖动骨架）
- 按住「查看原图」临时只显示底图
- 鼠标移动时状态栏显示所在的行列
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

# ─────────────────────────────────────────────────────────────
# PIL → QPixmap
# ─────────────────────────────────────────────────────────────


def pil_to_qimage(im) -> QImage:
    """把 PIL.Image 转成 QImage。

    ★ 为什么要自己转而不是 `ImageQt`：
      PIL 的 ImageQt 在 Pillow 10+ 上要求额外依赖且对 RGBA/RGB/L 的
      处理分支不透明，出过「颜色通道错位（红蓝互换）」的问题。
      这里显式按 mode 走分支，转换口径完全可控。

    ★ 必须 `copy()`：QImage 包的是 PIL 的缓冲区，PIL 对象被 GC 后
      画面会变成随机花屏 —— 这个 bug 只在缩放时偶发，最难查。
    """
    if im is None:
        return QImage()

    mode = im.mode
    if mode == "RGBA":
        data = im.tobytes("raw", "RGBA")
        qimg = QImage(data, im.width, im.height, QImage.Format_RGBA8888)
    elif mode == "RGB":
        data = im.tobytes("raw", "RGB")
        qimg = QImage(data, im.width, im.height, QImage.Format_RGB888)
    elif mode == "L":
        data = im.tobytes("raw", "L")
        qimg = QImage(data, im.width, im.height, QImage.Format_Grayscale8)
    else:
        # 其余模式一律转 RGBA 再走第一分支
        conv = im.convert("RGBA")
        data = conv.tobytes("raw", "RGBA")
        qimg = QImage(data, conv.width, conv.height, QImage.Format_RGBA8888)

    return qimg.copy()


def pil_to_pixmap(im) -> QPixmap:
    qimg = pil_to_qimage(im)
    return QPixmap.fromImage(qimg) if not qimg.isNull() else QPixmap()


# ─────────────────────────────────────────────────────────────
# 画布状态
# ─────────────────────────────────────────────────────────────


@dataclass
class CanvasLayers:
    """画布要展示的三层内容。

    ★ 用数据类而不是给控件塞一堆 setXxx：
      调用方一次给全，避免「底图换了但网格还是旧的」这种中间态。
    """

    #: 模板页底图（PIL.Image，RGBA/RGB）
    base: object | None = None
    #: 填充结果图层（PIL.Image，RGBA，与底图同尺寸）。None = 不显示
    fill: object | None = None
    #: 网格线：[(x0,y0), (x1,y1), 是否主线条]，单位 px（与底图同坐标系）
    grid: list = field(default_factory=list)
    #: 需要高亮/标记的格子：[(row, col, rect_px)]
    cells: list = field(default_factory=list)
    #: 参与填充的列（用于给网格线着色区分）
    active_cols: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# 画布
# ─────────────────────────────────────────────────────────────


class PreviewCanvas(QWidget):
    """纸张预览区。

    信号：
        cell_hovered(row, col)      鼠标移到某个格子上（-1,-1 表示移出）
        zoom_changed(percent)       缩放比例变化
        grid_dragged(dx_px, dy_px)  用户拖动了网格（M4.3 用）
    """

    cell_hovered = Signal(int, int)
    zoom_changed = Signal(int)
    grid_dragged = Signal(float, float)

    #: 缩放档位（百分比）—— 限制在合理区间，避免 10000% 把内存吃爆
    ZOOM_MIN = 10
    ZOOM_MAX = 800

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PreviewCanvas")
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor)

        self._layers = CanvasLayers()
        self._base_pm: QPixmap = QPixmap()
        self._fill_pm: QPixmap = QPixmap()

        self._zoom = 1.0
        self._fit_on_next_resize = True

        self._show_grid = True
        self._show_fill = True
        self._show_original = False       # 「按住查看原图」

        # 拖动平移
        self._pan = QPointF(0.0, 0.0)
        self._dragging = False
        self._drag_start = QPoint()
        self._drag_pan_start = QPointF()

        # 拖动网格
        self._grid_drag = False
        self._grid_drag_start = QPoint()

        # 悬停格
        self._hover = (-1, -1)

        self._grid_color = QColor(47, 111, 237, 150)
        self._grid_color_major = QColor(47, 111, 237, 210)
        self._grid_color_off = QColor(160, 166, 178, 120)
        self._hover_color = QColor(47, 111, 237, 38)
        self._paper_border = QColor(200, 206, 216)

    # ── 数据注入 ──────────────────────────────────────────────

    def set_layers(self, layers: CanvasLayers, *, keep_view: bool = False) -> None:
        """一次性换掉三层内容。

        Args:
            keep_view: True 时保留当前缩放与平移（改参数后重绘用），
                       False 时重新适配窗口（换页/换模板用）。
        """
        self._layers = layers
        self._base_pm = pil_to_pixmap(layers.base) if layers.base is not None else QPixmap()
        self._fill_pm = pil_to_pixmap(layers.fill) if layers.fill is not None else QPixmap()

        if not keep_view:
            self.fit_to_window()
        self.update()

    def set_base(self, image) -> None:
        """只换底图（网格保持）。"""
        self._layers.base = image
        self._base_pm = pil_to_pixmap(image) if image is not None else QPixmap()
        self.update()

    def set_fill(self, image) -> None:
        """只换填充层（拖动参数时高频调用，必须便宜）。"""
        self._layers.fill = image
        self._fill_pm = pil_to_pixmap(image) if image is not None else QPixmap()
        self.update()

    def clear(self) -> None:
        self.set_layers(CanvasLayers())

    # ── 显示开关 ──────────────────────────────────────────────

    def set_show_grid(self, on: bool) -> None:
        self._show_grid = bool(on)
        self.update()

    def show_grid(self) -> bool:
        return self._show_grid

    def set_show_fill(self, on: bool) -> None:
        self._show_fill = bool(on)
        self.update()

    def set_show_original(self, on: bool) -> None:
        """按住「查看原图」时 True —— 只画底图，不叠网格和手写。"""
        self._show_original = bool(on)
        self.update()

    # ── 缩放 ──────────────────────────────────────────────────

    def zoom(self) -> float:
        return self._zoom

    def zoom_percent(self) -> int:
        return int(round(self._zoom * 100))

    def set_zoom(self, z: float, *, anchor: QPointF | None = None) -> None:
        """设置缩放。anchor 为画布坐标下的锚点（缩放时保持不动的那个点）。"""
        z = max(self.ZOOM_MIN / 100.0, min(self.ZOOM_MAX / 100.0, float(z)))
        if abs(z - self._zoom) < 1e-6:
            return

        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        # 锚点处的内容坐标
        cx = (anchor.x() - self._pan.x()) / self._zoom
        cy = (anchor.y() - self._pan.y()) / self._zoom

        self._zoom = z
        # 反解平移，使锚点内容仍停在原屏幕位置
        self._pan = QPointF(anchor.x() - cx * z, anchor.y() - cy * z)

        self.zoom_changed.emit(self.zoom_percent())
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def zoom_reset(self) -> None:
        self.set_zoom(1.0)
        self.center_content()

    def fit_to_window(self) -> None:
        """整页适配：把纸张完整放进可视区，留 16px 边距。"""
        w, h = self._content_size()
        if w <= 0 or h <= 0:
            return
        avail_w = max(40, self.width() - 32)
        avail_h = max(40, self.height() - 32)
        z = min(avail_w / w, avail_h / h)
        # 允许缩小到 10%，放大到 400%（不裁剪内容即可）
        z = max(self.ZOOM_MIN / 100.0, min(4.0, z))
        self._zoom = z
        self.zoom_changed.emit(self.zoom_percent())
        self.center_content()

    def center_content(self) -> None:
        w, h = self._content_size()
        self._pan = QPointF(
            (self.width() - w * self._zoom) / 2.0,
            (self.height() - h * self._zoom) / 2.0,
        )
        self.update()

    def _content_size(self) -> tuple[int, int]:
        if not self._base_pm.isNull():
            return self._base_pm.width(), self._base_pm.height()
        if self._layers.grid:
            xs = [p[0] for seg in self._layers.grid for p in (seg[0], seg[1])]
            ys = [p[1] for seg in self._layers.grid for p in (seg[0], seg[1])]
            if xs and ys:
                return int(max(xs)), int(max(ys))
        return 0, 0

    # ── 坐标换算（对外公开，供拖动网格用）────────────────────

    def widget_to_content(self, pt: QPoint | QPointF) -> QPointF:
        return QPointF((pt.x() - self._pan.x()) / self._zoom,
                       (pt.y() - self._pan.y()) / self._zoom)

    def content_to_widget(self, pt: QPointF) -> QPointF:
        return QPointF(pt.x() * self._zoom + self._pan.x(),
                       pt.y() * self._zoom + self._pan.y())

    # ── 绘制 ──────────────────────────────────────────────────

    def paintEvent(self, event) -> None:      # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 画布底（和页面背景同色，比纯白柔和）
        p.fillRect(self.rect(), QColor(237, 239, 243))

        if self._base_pm.isNull() and not self._layers.grid:
            self._paint_empty(p)
            p.end()
            return

        w, h = self._content_size()
        if w <= 0 or h <= 0:
            self._paint_empty(p)
            p.end()
            return

        # 纸张矩形（缩放 + 平移后）
        paper = QRect(
            int(round(self._pan.x())),
            int(round(self._pan.y())),
            int(round(w * self._zoom)),
            int(round(h * self._zoom)),
        )

        # 纸张阴影 + 白底
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 26)))
        p.drawRect(paper.adjusted(2, 3, 2, 3))
        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawRect(paper)

        # ① 底图
        if not self._base_pm.isNull():
            p.drawPixmap(paper, self._base_pm)

        # ② 填充层（「查看原图」时跳过）
        if self._show_fill and not self._show_original and not self._fill_pm.isNull():
            p.setOpacity(1.0)
            p.drawPixmap(paper, self._fill_pm)

        # ③ 网格
        if self._show_grid and not self._show_original and self._layers.grid:
            self._paint_grid(p)

        # ④ 悬停高亮
        if self._show_grid and not self._show_original and self._hover[0] >= 0:
            self._paint_hover(p)

        # ⑤ 纸边
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self._paper_border, 1))
        p.drawRect(paper)

        p.end()

    def _paint_empty(self, p: QPainter) -> None:
        p.setPen(QColor(157, 163, 174))
        f = p.font()
        f.setPointSizeF(max(10.0, f.pointSizeF()))
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter,
                   "还没有打开表格。\n点上面的「打开」选一个 PDF 或图片。")

    def _paint_grid(self, p: QPainter) -> None:
        """网格线。主线（格子边界）粗一点，非参与列画成灰色虚线。"""
        pen_on = QPen(self._grid_color, 1.0, Qt.DashLine)
        pen_major = QPen(self._grid_color_major, 1.2, Qt.SolidLine)
        pen_off = QPen(self._grid_color_off, 1.0, Qt.DotLine)

        # 参与列集合：空 = 全部参与
        active = set(self._layers.active_cols or [])

        for seg in self._layers.grid:
            try:
                (x0, y0), (x1, y1), major = seg
            except (ValueError, TypeError):
                continue
            # 判断这条线是不是落在「不参与」的列里 —— 只有竖线才判断
            pen = pen_major if major else pen_on
            if active and abs(x0 - x1) < 0.5:
                # 竖线：看它左边紧邻的列号能不能推出来（用 x 反推太脆，
                # 这里改由调用方在 grid 里塞的 major 标记来区分，见 studio.py）
                pen = pen_major if major else pen_on

            a = self.content_to_widget(QPointF(x0, y0))
            b = self.content_to_widget(QPointF(x1, y1))
            p.setPen(pen)
            p.drawLine(QPointF(a), QPointF(b))

        # 未参与列的底色提示（蓝灰蒙一层）
        if active:
            off_at = self._off_column_rects()
            if off_at:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(140, 148, 162, 26)))
                for r in off_at:
                    a = self.content_to_widget(QPointF(r.left(), r.top()))
                    b = self.content_to_widget(QPointF(r.right(), r.bottom()))
                    p.drawRect(QRectF_to_rect(a, b))

    def _off_column_rects(self) -> list:
        """算出「不参与填充」那些列的矩形（整列一个矩形即可）。

        ★ 由调用方通过 cells 传进来的格子信息反推：
          同一列的所有格子 rect 求并集。比在画布里重算网格便宜得多。
        """
        if not self._layers.cells:
            return []
        active = set(self._layers.active_cols or [])
        if not active:
            return []
        cols: dict[int, list] = {}
        for item in self._layers.cells:
            try:
                row, col, rect = item
            except (ValueError, TypeError):
                continue
            cols.setdefault(int(col), []).append(rect)

        out = []
        for col, rects in cols.items():
            if col in active:
                continue
            left = min(r.left for r in rects)
            right = max(r.right for r in rects)
            top = min(r.top for r in rects)
            bottom = max(r.bottom for r in rects)
            out.append(_RectF(left, top, right, bottom))
        return out

    def _paint_hover(self, p: QPainter) -> None:
        row, col = self._hover
        for item in self._layers.cells:
            try:
                r_row, r_col, rect = item
            except (ValueError, TypeError):
                continue
            if int(r_row) == row and int(r_col) == col:
                a = self.content_to_widget(QPointF(rect.left, rect.top))
                b = self.content_to_widget(QPointF(rect.right, rect.bottom))
                p.setPen(QPen(QColor(47, 111, 237, 200), 1.4))
                p.setBrush(QBrush(self._hover_color))
                p.drawRect(QRectF_to_rect(a, b))
                break

    # ── 交互 ──────────────────────────────────────────────────

    def wheelEvent(self, event) -> None:      # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        anchor = QPointF(event.position())
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.set_zoom(self._zoom * factor, anchor=anchor)
        event.accept()

    def mousePressEvent(self, event) -> None:     # noqa: N802
        if event.button() == Qt.MiddleButton:
            self._dragging = True
            self._drag_start = event.position().toPoint()
            self._drag_pan_start = QPointF(self._pan)
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            # Shift + 左键 = 拖动网格（避免和将来「框选」冲突）
            if event.modifiers() & Qt.ShiftModifier:
                self._grid_drag = True
                self._grid_drag_start = event.position().toPoint()
                self.setCursor(Qt.SizeAllCursor)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:      # noqa: N802
        pos = event.position().toPoint()

        if self._dragging:
            d = pos - self._drag_start
            self._pan = QPointF(self._drag_pan_start.x() + d.x(),
                                self._drag_pan_start.y() + d.y())
            self.update()
            event.accept()
            return

        if self._grid_drag:
            d = pos - self._grid_drag_start
            self._grid_drag_start = pos
            # 换算成内容坐标的位移（不除 zoom 的话缩小时网格会跑飞）
            self.grid_dragged.emit(d.x() / self._zoom, d.y() / self._zoom)
            event.accept()
            return

        self._update_hover(pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:   # noqa: N802
        if self._dragging and event.button() == Qt.MiddleButton:
            self._dragging = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        if self._grid_drag and event.button() == Qt.LeftButton:
            self._grid_drag = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:          # noqa: N802
        if self._hover != (-1, -1):
            self._hover = (-1, -1)
            self.cell_hovered.emit(-1, -1)
            self.update()
        super().leaveEvent(event)

    def _update_hover(self, pos: QPoint) -> None:
        if not self._layers.cells:
            if self._hover != (-1, -1):
                self._hover = (-1, -1)
                self.cell_hovered.emit(-1, -1)
                self.update()
            return

        c = self.widget_to_content(pos)
        hit = (-1, -1)
        for item in self._layers.cells:
            try:
                r_row, r_col, rect = item
            except (ValueError, TypeError):
                continue
            if rect.left <= c.x() <= rect.right and rect.top <= c.y() <= rect.bottom:
                hit = (int(r_row), int(r_col))
                break

        if hit != self._hover:
            self._hover = hit
            self.cell_hovered.emit(*hit)
            self.update()

    def resizeEvent(self, event) -> None:         # noqa: N802
        super().resizeEvent(event)
        if self._fit_on_next_resize:
            self._fit_on_next_resize = False
            self.fit_to_window()
        self.update()

    def sizeHint(self) -> QSize:                  # noqa: N802
        return QSize(560, 700)


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────


class _RectF:
    """画布内部用的小矩形（避免依赖 Qt 的 QRectF 语义差异）。"""

    __slots__ = ("left", "top", "right", "bottom")

    def __init__(self, left: float, top: float, right: float, bottom: float):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


def QRectF_to_rect(a: QPointF, b: QPointF) -> QRect:      # noqa: N802
    """由两个角点（可能任意顺序）得到整型 QRect。"""
    x0, x1 = sorted((a.x(), b.x()))
    y0, y1 = sorted((a.y(), b.y()))
    return QRect(int(round(x0)), int(round(y0)),
                 max(1, int(round(x1 - x0))), max(1, int(round(y1 - y0))))
