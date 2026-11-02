"""字形采集向导（三步：抠图 → 框字 → 标注）。

设计依据：
- 界面原型.html 屏 3（two-pane 对比 / 容差滑块 / SVG 框选 / 12 键键盘）
- 需求文档 §5（采集流程）
- 模块设计说明书 §4.11（font_wizard）

三步各自要什么：

  ① 抠图  —— 原图 vs 处理后双窗格 + 容差滑块 + 反色 + 旋转校正
             用 `imageops` 做二值化，实时预览（滑块拖动就重算）
  ② 框字  —— 画布上拖框 / 右键删框 / Ctrl+Z/Y/Delete
             用 `imageops.detect_glyphs` 自动框选打底
  ③ 标注  —— 12 键键盘 + 进度条 + 缺字警告
             用 `font_service.add_samples` 攒字形

★ 三步都「只攒中间结果，不落盘」。最后一步的「导出字形库」才写文件 ——
  这样用户中途退出不会留下半套字形库（那是最难解释的脏数据）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import imageops
from ..core.charset import CHARSET_V1
from .widgets import CaptureProgress, CharKeyboard, pil_to_pixmap
from .widgets.char_keyboard import CHAR_LABEL

#: 三步的定义（沿用 M1 的 WIZARD_STEPS 语义）
WIZARD_STEPS = [
    (1, "抠图", "把手写照片里的背景去掉，只留下字"),
    (2, "框字", "把每个字单独框出来"),
    (3, "标注", "告诉程序每一格是哪个字符"),
]


def _label(text: str, role: str = "") -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setObjectName(role)
    lb.setWordWrap(True)
    return lb


def _btn(text: str, variant: str = "", on_click=None) -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setObjectName(variant)
    b.setCursor(Qt.PointingHandCursor)
    if on_click is not None:
        b.clicked.connect(on_click)
    return b


# ─────────────────────────────────────────────────────────────
# 步骤条
# ─────────────────────────────────────────────────────────────


class StepBar(QWidget):
    """三步进度条：① 抠图 ── ② 框字 ── ③ 标注。"""

    step_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StepBar")
        self._current = 1
        self._items: list[QFrame] = []

        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 12, 24, 12)
        lay.setSpacing(0)

        for i, (num, name, _tip) in enumerate(WIZARD_STEPS):
            if i > 0:
                line = QFrame()
                line.setObjectName("StepLine")
                line.setFixedHeight(2)
                lay.addWidget(line, 1)

            item = QFrame()
            item.setObjectName("StepItem")
            item.setCursor(Qt.PointingHandCursor)
            il = QHBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(6)

            badge = QLabel(str(num))
            badge.setObjectName("StepItemNum")
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignCenter)
            il.addWidget(badge)
            il.addWidget(_label(name, "StepItemText"))

            item.mousePressEvent = (                       # type: ignore[method-assign]
                lambda _e, n=num: self.step_clicked.emit(n)
            )
            self._items.append(item)
            lay.addWidget(item)

        self.set_current(1)

    def set_current(self, step: int) -> None:
        self._current = step
        for i, item in enumerate(self._items, start=1):
            item.setProperty("state",
                             "done" if i < step else ("current" if i == step else ""))
            item.style().unpolish(item)
            item.style().polish(item)


# ─────────────────────────────────────────────────────────────
# 第一步：抠图
# ─────────────────────────────────────────────────────────────


class _ImagePane(QFrame):
    """一个带标题的图片窗格（原图 / 处理后预览共用）。"""

    def __init__(self, title: str, sub: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Pane")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QFrame()
        head.setObjectName("PaneHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 7, 10, 7)
        hl.addWidget(_label(title, "PaneTitle"))
        hl.addStretch(1)
        self.sub_lb = _label(sub, "PaneSub")
        hl.addWidget(self.sub_lb)
        lay.addWidget(head)

        body = QScrollArea()
        body.setObjectName("PaneBody")
        body.setWidgetResizable(True)
        body.setFrameShape(QFrame.NoFrame)

        self.canvas = QLabel()
        self.canvas.setObjectName("PaneCanvas")
        self.canvas.setAlignment(Qt.AlignCenter)
        self.canvas.setMinimumSize(200, 200)
        body.setWidget(self.canvas)
        lay.addWidget(body, 1)

        self._pm = QPixmap()

    def set_image(self, pil_image) -> None:
        if pil_image is None:
            self.canvas.setText("（没有图片）")
            self.canvas.setPixmap(QPixmap())
            return
        pm = pil_to_pixmap(pil_image)
        self._pm = pm
        self.canvas.setPixmap(pm)
        self.canvas.resize(pm.size())
        self.sub_lb.setText(f"{pm.width()} × {pm.height()}")

    def set_sub(self, text: str) -> None:
        self.sub_lb.setText(text)


class CutoutPage(QWidget):
    """① 抠图：调容差看效果，满意了去下一步。"""

    changed = Signal()               # 参数变了（要重算预览）
    request_pick = Signal()          # 请求重新选择图片

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WizBody")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 0)
        root.setSpacing(14)

        # ── 双窗格 ──
        panes = QHBoxLayout()
        panes.setSpacing(14)
        self.pane_src = _ImagePane("原图", "")
        self.pane_out = _ImagePane("处理后预览（黑白）", "已去背景")
        panes.addWidget(self.pane_src, 1)
        panes.addWidget(self.pane_out, 1)
        root.addLayout(panes, 1)

        # ── 容差 ──
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(_label("容差", "CtlLabel"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("Slider")
        self.slider.setRange(0, 100)
        self.slider.setValue(29)
        self.slider.setFixedWidth(280)
        self.slider.valueChanged.connect(self._on_slider)
        row.addWidget(self.slider)
        self.tol_lb = _label("29", "Mono")
        self.tol_lb.setFixedWidth(30)
        row.addWidget(self.tol_lb)
        row.addWidget(_label("容差越高，保留的近似黑色像素越多（字更粗）", "CtlTip"))
        row.addStretch(1)
        root.addLayout(row)

        # ── 反色 / 旋转 ──
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.chk_invert = QCheckBox("反色（白笔写在黑纸上）")
        self.chk_invert.stateChanged.connect(lambda _s: self.changed.emit())
        row2.addWidget(self.chk_invert)

        row2.addSpacing(16)
        row2.addWidget(_label("旋转校正", "CtlLabel"))
        self.rot = QSlider(Qt.Horizontal)
        self.rot.setObjectName("Slider")
        self.rot.setRange(-15, 15)
        self.rot.setValue(0)
        self.rot.setFixedWidth(150)
        self.rot.valueChanged.connect(self._on_rot)
        row2.addWidget(self.rot)
        self.rot_lb = _label("0°", "Mono")
        self.rot_lb.setFixedWidth(34)
        row2.addWidget(self.rot_lb)

        row2.addStretch(1)
        row2.addWidget(_btn("重新选择图片", "Ghost", self.request_pick.emit))
        root.addLayout(row2)

    def _on_slider(self, v: int) -> None:
        self.tol_lb.setText(str(v))
        self.changed.emit()

    def _on_rot(self, v: int) -> None:
        self.rot_lb.setText(f"{v}°")
        self.changed.emit()

    # ── 取值 ──
    def tolerance(self) -> int:
        return int(self.slider.value())

    def invert(self) -> bool:
        return self.chk_invert.isChecked()

    def rotate(self) -> float:
        return float(self.rot.value())

    def set_values(self, *, tolerance: int | None = None,
                   invert: bool | None = None, rotate: float | None = None) -> None:
        if tolerance is not None:
            self.slider.blockSignals(True)
            self.slider.setValue(int(tolerance))
            self.tol_lb.setText(str(int(tolerance)))
            self.slider.blockSignals(False)
        if invert is not None:
            self.chk_invert.blockSignals(True)
            self.chk_invert.setChecked(bool(invert))
            self.chk_invert.blockSignals(False)
        if rotate is not None:
            self.rot.blockSignals(True)
            self.rot.setValue(int(rotate))
            self.rot_lb.setText(f"{int(rotate)}°")
            self.rot.blockSignals(False)


# ─────────────────────────────────────────────────────────────
# 第二步：框字
# ─────────────────────────────────────────────────────────────


class BoxCanvas(QWidget):
    """可交互的框选画布。

    交互（与原型一致）：
    - 空白处左键拖拽 → 补一个新框
    - 左键点框 → 选中；再点别处取消
    - 右键点框 → 删除
    - Delete → 删选中；Ctrl+Z / Ctrl+Y → 撤销/恢复
    """

    boxes_changed = Signal()

    #: 撤销栈上限（防止长时间编辑把内存吃光）
    MAX_UNDO = 100

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("BoxCanvas")
        self.setMinimumSize(400, 260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._pm = QPixmap()
        self._boxes: list[QRect] = []
        self._selected = -1
        self._labels: list[str] = []          # 每个框的标注（"" 表示未标注）
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []

        self._drawing = False
        self._drag_from = QPoint()
        self._drag_to = QPoint()

        self._scale = 1.0
        self._offset = QPoint(0, 0)
        self._pick_mode = ""                  # 标注模式下点框就打标

    # ── 数据 ──

    def set_image(self, pil_image) -> None:
        self._pm = pil_to_pixmap(pil_image) if pil_image is not None else QPixmap()
        self._rescale()
        self.update()

    def set_boxes(self, boxes: list, labels: list[str] | None = None,
                  *, reset_undo: bool = True) -> None:
        """boxes 可以是 QRect 列表，也可以是 (x, y, w, h) 元组列表。"""
        self._boxes = [self._as_rect(b) for b in (boxes or [])]
        self._labels = list(labels or [""] * len(self._boxes))
        if len(self._labels) < len(self._boxes):
            self._labels += [""] * (len(self._boxes) - len(self._labels))
        self._selected = -1
        if reset_undo:
            self._undo.clear()
            self._redo.clear()
        self.boxes_changed.emit()
        self.update()

    @staticmethod
    def _as_rect(b) -> QRect:
        if isinstance(b, QRect):
            return QRect(b)
        if hasattr(b, "left") and hasattr(b, "top"):
            return QRect(int(b.left), int(b.top),
                         int(getattr(b, "right") - b.left),
                         int(getattr(b, "bottom") - b.top))
        x, y, w, h = b
        return QRect(int(x), int(y), int(w), int(h))

    def boxes(self) -> list[QRect]:
        return [QRect(b) for b in self._boxes]

    def labels(self) -> list[str]:
        return list(self._labels)

    def count(self) -> int:
        return len(self._boxes)

    def labeled_count(self) -> int:
        return sum(1 for s in self._labels if s)

    # ── 撤销 / 恢复 ──

    def _snapshot(self) -> tuple:
        return ([QRect(b) for b in self._boxes], list(self._labels))

    def _push_undo(self) -> None:
        self._undo.append(self._snapshot())
        if len(self._undo) > self.MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        boxes, labels = self._undo.pop()
        self._boxes, self._labels = boxes, labels
        self._selected = -1
        self.boxes_changed.emit()
        self.update()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        boxes, labels = self._redo.pop()
        self._boxes, self._labels = boxes, labels
        self._selected = -1
        self.boxes_changed.emit()
        self.update()
        return True

    def delete_selected(self) -> bool:
        if 0 <= self._selected < len(self._boxes):
            self._push_undo()
            del self._boxes[self._selected]
            del self._labels[self._selected]
            self._selected = -1
            self.boxes_changed.emit()
            self.update()
            return True
        return False

    def clear_all(self) -> None:
        if not self._boxes:
            return
        self._push_undo()
        self._boxes.clear()
        self._labels.clear()
        self._selected = -1
        self.boxes_changed.emit()
        self.update()

    def add_box(self, rect: QRect, label: str = "") -> int:
        self._push_undo()
        self._boxes.append(QRect(rect))
        self._labels.append(label)
        self.boxes_changed.emit()
        self.update()
        return len(self._boxes) - 1

    # ── 标注 ──

    def set_pick_mode(self, ch: str) -> None:
        """设置「点框就标成 ch」模式；空串 = 普通选择模式。"""
        self._pick_mode = ch
        self.setCursor(Qt.CrossCursor if ch else Qt.ArrowCursor)
        self.update()

    def pick_mode(self) -> str:
        return self._pick_mode

    def label_at(self, index: int, ch: str) -> None:
        if 0 <= index < len(self._labels):
            self._push_undo()
            self._labels[index] = ch
            self.boxes_changed.emit()
            self.update()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self._labels:
            if s:
                out[s] = out.get(s, 0) + 1
        return out

    # ── 显示 ──

    def _rescale(self) -> None:
        if self._pm.isNull():
            self._scale, self._offset = 1.0, QPoint(0, 0)
            return
        aw = max(1, self.width())
        ah = max(1, self.height())
        s = min(aw / self._pm.width(), ah / self._pm.height())
        self._scale = s
        self._offset = QPoint(
            int((aw - self._pm.width() * s) / 2),
            int((ah - self._pm.height() * s) / 2),
        )

    def to_image(self, pt: QPoint) -> QPoint:
        """控件坐标 → 图像坐标。"""
        return QPoint(int((pt.x() - self._offset.x()) / self._scale),
                      int((pt.y() - self._offset.y()) / self._scale))

    def to_widget(self, pt: QPoint) -> QPoint:
        return QPoint(int(pt.x() * self._scale + self._offset.x()),
                      int(pt.y() * self._scale + self._offset.y()))

    def paintEvent(self, event) -> None:            # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor(247, 248, 250))

        if self._pm.isNull():
            p.setPen(QColor(157, 163, 174))
            p.drawText(self.rect(), Qt.AlignCenter, "还没有图片")
            p.end()
            return

        self._rescale()
        target = QRect(self._offset,
                       self._pm.size() * self._scale)
        p.drawPixmap(target, self._pm)

        # 已标框：绿；未标框：橙（未标注一眼可见）；选中：加粗高亮
        for i, b in enumerate(self._boxes):
            wb = QRect(self.to_widget(b.topLeft()), self.to_widget(b.bottomRight()))
            labeled = bool(self._labels[i]) if i < len(self._labels) else False
            if i == self._selected:
                pen = QPen(QColor(47, 111, 237), 2.4)
                brush = QBrush(QColor(47, 111, 237, 42))
            elif labeled:
                pen = QPen(QColor(34, 160, 107), 1.6)
                brush = QBrush(QColor(34, 160, 107, 26))
            else:
                pen = QPen(QColor(200, 128, 40), 1.6)
                brush = QBrush(QColor(200, 128, 40, 26))
            p.setPen(pen)
            p.setBrush(brush)
            p.drawRect(wb)

            # 已标注的框，右上角写上是哪个字符
            if labeled and i < len(self._labels):
                ch = self._labels[i]
                p.setPen(QColor(34, 160, 107))
                f = p.font()
                f.setPointSizeF(max(9.0, f.pointSizeF() - 1))
                p.setFont(f)
                p.drawText(wb.topRight() + QPoint(-14, -3),
                           Qt.AlignRight | Qt.AlignBottom,
                           CHAR_LABEL.get(ch, ch))

        # 正在拖的新框
        if self._drawing:
            r = QRect(self._drag_from, self._drag_to).normalized()
            p.setPen(QPen(QColor(47, 111, 237), 1.6, Qt.DashLine))
            p.setBrush(QBrush(QColor(47, 111, 237, 34)))
            p.drawRect(r)

        p.end()

    def resizeEvent(self, event) -> None:            # noqa: N802
        super().resizeEvent(event)
        self._rescale()

    # ── 鼠标 ──

    def _hit(self, pos: QPoint) -> int:
        """返回命中的框序号（后画的优先，跟图层直觉一致）。"""
        for i in range(len(self._boxes) - 1, -1, -1):
            wb = QRect(self.to_widget(self._boxes[i].topLeft()),
                       self.to_widget(self._boxes[i].bottomRight()))
            if wb.contains(pos):
                return i
        return -1

    def mousePressEvent(self, event) -> None:        # noqa: N802
        pos = event.position().toPoint()

        if event.button() == Qt.RightButton:
            i = self._hit(pos)
            if i >= 0:
                self._push_undo()
                del self._boxes[i]
                del self._labels[i]
                self._selected = -1
                self.boxes_changed.emit()
                self.update()
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            i = self._hit(pos)
            if i >= 0:
                if self._pick_mode:
                    # 标注模式：点框就打标
                    self.label_at(i, self._pick_mode)
                else:
                    self._selected = i
                    self.update()
                event.accept()
                return
            # 空白处：开始拖新框
            self._drawing = True
            self._drag_from = self._drag_to = pos
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:         # noqa: N802
        if self._drawing:
            self._drag_to = event.position().toPoint()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:      # noqa: N802
        if self._drawing and event.button() == Qt.LeftButton:
            self._drawing = False
            r = QRect(self._drag_from, event.position().toPoint()).normalized()
            # 太小的框当误触丢掉（手一抖就会画出一个 3×3 的点）
            if r.width() >= 6 and r.height() >= 6:
                a = self.to_image(r.topLeft())
                b = self.to_image(r.bottomRight())
                self._push_undo()
                self._boxes.append(QRect(a, b).normalized())
                self._labels.append(self._pick_mode or "")
                self._selected = len(self._boxes) - 1
                self.boxes_changed.emit()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:          # noqa: N802
        k = event.key()
        mods = event.modifiers()
        if k == Qt.Key_Delete or k == Qt.Key_Backspace:
            if self.delete_selected():
                event.accept()
                return
        if k == Qt.Key_Z and mods & Qt.ControlModifier:
            self.undo()
            event.accept()
            return
        if k == Qt.Key_Y and mods & Qt.ControlModifier:
            self.redo()
            event.accept()
            return
        if k == Qt.Key_A and mods & Qt.ControlModifier:
            self._selected = len(self._boxes) - 1 if self._boxes else -1
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)


class BoxPage(QWidget):
    """② 框字：自动框选打底 + 手动增删。"""

    auto_requested = Signal()
    boxes_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WizBody")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 0)
        root.setSpacing(12)

        host = QFrame()
        host.setObjectName("BoxHost")
        hl = QVBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)

        self.canvas = BoxCanvas()
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.boxes_changed.connect(self._on_changed)
        hl.addWidget(self.canvas)
        root.addWidget(host, 1)

        self.hint_lb = _label("", "Hint")
        root.addWidget(self.hint_lb)

        ops = QHBoxLayout()
        ops.setSpacing(8)
        ops.addWidget(_btn("自动框选", "", self.auto_requested.emit))
        ops.addWidget(_btn("全部清空", "Danger", self._clear))
        ops.addStretch(1)
        ops.addWidget(_btn("撤销", "", self.canvas.undo))
        ops.addWidget(_btn("恢复", "", self.canvas.redo))
        root.addLayout(ops)

        self._refresh_hint()

    def _clear(self) -> None:
        self.canvas.clear_all()
        self._refresh_hint()

    def _on_changed(self) -> None:
        self._refresh_hint()
        self.boxes_changed.emit()

    def _refresh_hint(self) -> None:
        n = self.canvas.count()
        labeled = self.canvas.labeled_count()
        extra = f"（其中 {labeled} 个已标注）" if labeled else ""
        self.hint_lb.setText(
            f"已框选 {n} 个字符{extra}。框错了可以右键删除，漏了就在空白处拖一个框出来。"
            " 快捷键：Ctrl+Z 撤销、Ctrl+Y 恢复、Delete 删除选中框。"
        )


# ─────────────────────────────────────────────────────────────
# 第三步：标注
# ─────────────────────────────────────────────────────────────


class LabelPage(QWidget):
    """③ 标注：点字符 → 点框批量标注。"""

    export_requested = Signal()
    skip_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WizBody")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 0)
        root.setSpacing(12)

        # ── 进度条 ──
        self.progress = CaptureProgress()
        root.addWidget(self.progress)

        # ── 框选画布（复用 BoxCanvas，切到标注模式）──
        host = QFrame()
        host.setObjectName("BoxHost")
        hl = QVBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        self.canvas = BoxCanvas()
        self.canvas.boxes_changed.connect(self._on_boxes_changed)
        hl.addWidget(self.canvas)
        root.addWidget(host, 1)

        # ── 键盘 ──
        self.keyboard = CharKeyboard()
        self.keyboard.selection_changed.connect(self._on_mode)
        root.addWidget(self.keyboard)

        self.tip_lb = _label(
            "橙色高亮 = 这个字符还没有采集到样本。可以「稍后补充」，不耽误导出。"
            f"　v1 只采集这 {len(CHARSET_V1)} 个字符（数字 + 逗号 + 句点），不含字母和汉字。",
            "Hint",
        )
        root.addWidget(self.tip_lb)

    def _on_mode(self, ch: str) -> None:
        self.canvas.set_pick_mode(ch)

    def _on_boxes_changed(self) -> None:
        counts = self.canvas.counts()
        self.progress.set_counts(counts)
        self.keyboard.set_counts(counts)

    def refresh(self) -> None:
        self._on_boxes_changed()


# ─────────────────────────────────────────────────────────────
# 向导窗口
# ─────────────────────────────────────────────────────────────


class WizardWindow(QWidget):
    """采集向导（三步）。

    信号：
        back_home           返回首页
        source_picked(str)  选好了图片路径
        request_preview     抠图参数变了，请求重算处理后预览
        request_auto_boxes  请求自动框选（控制器算完灌回来）
        finish_requested(name) 点「导出字形库」或「稍后补充」，带库名
    """

    back_home = Signal()
    source_picked = Signal(str)
    request_preview = Signal()
    request_auto_boxes = Signal()
    finish_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WizardWindow")
        self.setWindowTitle("新建字形库 · 手写字迹填充器")
        self.resize(1160, 780)

        self._step = 1

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 工具条 ──
        bar = QWidget()
        bar.setObjectName("Toolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(10)

        bl.addWidget(_btn("← 返回首页", "Ghost", self.back_home.emit))
        bl.addStretch(1)
        self.title_lb = _label("新建字形库", "ToolbarTitle")
        bl.addWidget(self.title_lb)
        bl.addStretch(1)
        root.addWidget(bar)

        # ── 步骤条 ──
        self.stepbar = StepBar()
        self.stepbar.step_clicked.connect(self.set_step)
        root.addWidget(self.stepbar)

        # ── 三步堆栈 ──
        self.stack = QStackedWidget()
        self.stack.setObjectName("WizardStack")

        self.page_cut = CutoutPage()
        self.page_box = BoxPage()
        self.page_label = LabelPage()

        self.page_cut.changed.connect(self.request_preview.emit)
        self.page_cut.request_pick.connect(self.pick_source)
        self.page_box.auto_requested.connect(self.request_auto_boxes.emit)

        for p in (self.page_cut, self.page_box, self.page_label):
            self.stack.addWidget(p)
        root.addWidget(self.stack, 1)

        # ── 底部导航 ──
        root.addWidget(self._build_foot())

        self.set_step(1)

    # ── 底部导航 ─────────────────────────────────────────────

    def _build_foot(self) -> QWidget:
        foot = QFrame()
        foot.setObjectName("WizFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 10, 24, 14)
        fl.setSpacing(8)

        self.btn_prev = _btn("上一步", "", self.prev_step)
        fl.addWidget(self.btn_prev)

        fl.addSpacing(8)
        fl.addWidget(_label("字形库名称", "CtlLabel"))
        self._name_edit = QLineEdit()
        self._name_edit.setObjectName("Input")
        self._name_edit.setPlaceholderText("我的字迹")
        self._name_edit.setMaximumWidth(180)
        fl.addWidget(self._name_edit)

        fl.addStretch(1)

        # 第三步的两个收尾按钮
        self.btn_skip = _btn("稍后补充", "", self._on_skip)
        self.btn_export = _btn("导出字形库", "Primary", self._on_export)
        fl.addWidget(self.btn_skip)
        fl.addWidget(self.btn_export)

        # 前两步的「下一步」
        self.btn_next = _btn("下一步 ▶", "Primary", self.next_step)
        fl.addWidget(self.btn_next)

        return foot

    def _on_export(self) -> None:
        """点「导出字形库」→ 带库名结束向导。"""
        self.finish_requested.emit(self.font_name())

    # ── 切步 ────────────────────────────────────────────────

    def set_step(self, step: int) -> None:
        step = max(1, min(3, int(step)))
        self._step = step
        self.stepbar.set_current(step)
        self.stack.setCurrentIndex(step - 1)

        is_last = step == 3
        self.btn_prev.setVisible(step > 1)
        self._name_edit.setVisible(is_last)
        self.btn_skip.setVisible(is_last)
        self.btn_export.setVisible(is_last)
        self.btn_next.setVisible(not is_last)
        if is_last:
            self.page_label.refresh()
        self._refresh_title()

    def next_step(self) -> None:
        self.set_step(self._step + 1)

    def prev_step(self) -> None:
        self.set_step(self._step - 1)

    def step(self) -> int:
        return self._step

    def _refresh_title(self) -> None:
        for num, name, _tip in WIZARD_STEPS:
            if num == self._step:
                self.title_lb.setText(f"新建字形库 · 第 {num} 步：{name}")
                break

    def _on_skip(self) -> None:
        """稍后补充 = 直接去导出（缺的字符不影响已采到的部分）。"""
        self.finish_requested.emit(self.font_name())

    # ── 选图 ────────────────────────────────────────────────

    def pick_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选一张手写照片或扫描件", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;所有文件 (*)")
        if path:
            self.source_picked.emit(path)

    # ── 灌数据 ──────────────────────────────────────────────

    def set_source_image(self, pil_image, path: str = "") -> None:
        self.page_cut.pane_src.set_image(pil_image)
        if path:
            self.page_cut.pane_src.set_sub(Path(path).name)

    def set_preview_image(self, pil_image) -> None:
        self.page_cut.pane_out.set_image(pil_image)

    def set_boxes_image(self, pil_image) -> None:
        """框字与标注两步共用同一张处理后的图。"""
        self.page_box.canvas.set_image(pil_image)
        self.page_label.canvas.set_image(pil_image)

    def set_boxes(self, boxes: list, *, reset_undo: bool = True) -> None:
        self.page_box.canvas.set_boxes(boxes, reset_undo=reset_undo)
        self.page_label.canvas.set_boxes(boxes, reset_undo=reset_undo)

    def set_source_info(self, text: str) -> None:
        self.page_cut.pane_src.set_sub(text)

    # ── 取值 ────────────────────────────────────────────────

    def cutout_options(self) -> dict:
        return {
            "tolerance": self.page_cut.tolerance(),
            "invert": self.page_cut.invert(),
            "rotate": self.page_cut.rotate(),
        }

    def boxes(self) -> list:
        return self.page_box.canvas.boxes()

    def labeled_boxes(self) -> list[tuple]:
        """[(rect, char), ...] —— 只取已标注的。"""
        c = self.page_label.canvas
        out = []
        for b, ch in zip(c.boxes(), c.labels()):
            if ch:
                out.append((b, ch))
        return out

    def label_counts(self) -> dict:
        return self.page_label.canvas.counts()

    def font_name(self) -> str:
        name = self._name_edit.text().strip() if hasattr(self, "_name_edit") else ""
        return name or "我的字迹"

    def set_font_name_default(self, name: str) -> None:
        self.title_lb.setText(f"新建字形库 · {name}")


# ─────────────────────────────────────────────────────────────
# 自检
# ─────────────────────────────────────────────────────────────


def _selftest() -> int:
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PIL import Image

    app = QApplication.instance() or QApplication([])
    fails = []

    w = WizardWindow()
    if w.step() != 1:
        fails.append("初始步不是 1")

    # 切步
    w.next_step(); w.next_step()
    if w.step() != 3:
        fails.append(f"前进到 3 失败 {w.step()}")
    # ★ 一律用 isVisibleTo：窗口没 show() 时 isVisible() 恒为 False
    if not w.btn_skip.isVisibleTo(w):
        fails.append("第三步应显示「稍后补充」")
    if w.btn_next.isVisibleTo(w):
        fails.append("第三步不该再显示「下一步」")
    w.prev_step()
    if w.step() != 2:
        fails.append("后退失败")

    # 框选画布
    img = Image.new("RGB", (600, 300), "white")
    w.set_boxes_image(img)
    w.set_boxes([])
    c = w.page_box.canvas
    c.add_box(QRect(10, 10, 40, 40))
    c.add_box(QRect(60, 10, 40, 40))
    if c.count() != 2:
        fails.append(f"加框失败 {c.count()}")
    if not c.undo() or c.count() != 1:
        fails.append(f"撤销失败 {c.count()}")
    if not c.redo() or c.count() != 2:
        fails.append(f"恢复失败 {c.count()}")

    # 标注
    w.page_label.canvas.set_boxes(c.boxes(), reset_undo=False)
    w.page_label.canvas.label_at(0, "3")
    w.page_label.canvas.label_at(1, ".")
    counts = w.label_counts()
    if counts.get("3") != 1 or counts.get(".") != 1:
        fails.append(f"标注计数不对 {counts}")
    labeled = w.labeled_boxes()
    if len(labeled) != 2:
        fails.append(f"已标注框数不对 {len(labeled)}")

    # 键盘联动进度
    w.page_label.refresh()
    if "2" not in w.page_label.progress.text_lb.text():
        fails.append(f"进度没更新 {w.page_label.progress.text_lb.text()}")

    # 键盘模式
    w.page_label.keyboard._on_key("5")
    if w.page_label.canvas.pick_mode() != "5":
        fails.append(f"标注模式没传进画布 {w.page_label.canvas.pick_mode()}")

    # 删除
    w.page_label.canvas._selected = 0
    if not w.page_label.canvas.delete_selected():
        fails.append("删除失败")

    if fails:
        print("采集向导自检失败：", file=sys.stderr)
        for f in fails:
            print("  ✗", f, file=sys.stderr)
        return 1
    print("采集向导自检通过（三步切换 / 框选增删撤 / 标注联动 / 键盘模式）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
