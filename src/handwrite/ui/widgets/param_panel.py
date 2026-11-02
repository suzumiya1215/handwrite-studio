"""左侧参数面板的可复用零件。

设计依据：界面原型.html 屏 2 的 `.side` / `.grp` / `.fld` / `.slider-row`
        + 模块设计说明书 §4.11

这个文件提供 6 个小零件，上层的 studio.py 用它们拼出 5 组参数：

    CollapsibleGroup   可折叠分组（▶ 标题 + 内容区）
    FieldRow           「标签 + 输入框 + 单位」一行
    SliderRow          「标签 + 滑块 + 数值」一行
    PresetButtons      一排预设按钮（轻微 / 自然 / 明显），单选
    ColumnChecks       参与填充的列勾选（1..N 方块）
    SegmentedRadios    竖排单选（填写内容的三种模式）

★ 为什么拆成独立控件而不是在 studio.py 里堆 QFormLayout：
  参数面板是这个程序里最密集的界面，14 个滑块 + 9 个输入框 + 10 个列勾选。
  揉在一起会有 400 行布局代码，改一个间距要在几十行里找。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


# ─────────────────────────────────────────────────────────────
# 可折叠分组
# ─────────────────────────────────────────────────────────────


class CollapsibleGroup(QFrame):
    """一组参数：点标题栏折叠/展开。

    ★ 折叠状态要能读出来 —— 保存到项目里，下次打开还是老样子。
    """

    toggled = Signal(bool)      # True = 展开

    def __init__(self, title: str, parent: QWidget | None = None,
                 expanded: bool = True):
        super().__init__(parent)
        self.setObjectName("Grp")
        self._expanded = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        self.head = QFrame()
        self.head.setObjectName("GrpHead")
        self.head.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(12, 9, 12, 9)
        hl.setSpacing(6)

        self.arrow = QLabel("▶")
        self.arrow.setObjectName("GrpArrow")
        self.arrow.setFixedWidth(12)
        hl.addWidget(self.arrow)

        self.title_lb = QLabel(title)
        self.title_lb.setObjectName("GrpTitle")
        hl.addWidget(self.title_lb, 1)

        self.head.mousePressEvent = self._on_head_click        # type: ignore[method-assign]
        root.addWidget(self.head)

        # ── 内容区 ──
        self.body = QWidget()
        self.body.setObjectName("GrpBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(12, 4, 12, 12)
        self.body_lay.setSpacing(8)
        root.addWidget(self.body)

        self.set_expanded(expanded)

    def _on_head_click(self, _event) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, on: bool) -> None:
        self._expanded = bool(on)
        self.body.setVisible(self._expanded)
        self.arrow.setText("▼" if self._expanded else "▶")
        self.setProperty("expanded", self._expanded)
        self.head.setProperty("expanded", self._expanded)
        self.style().unpolish(self.head)
        self.style().polish(self.head)
        self.toggled.emit(self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def add(self, w) -> None:
        if isinstance(w, QWidget):
            self.body_lay.addWidget(w)
        else:
            self.body_lay.addLayout(w)

    def add_row(self, label_text: str) -> QHBoxLayout:
        """加一行「小标题」并返回该行的布局（用于 sub-h 那种分组标题）。"""
        lb = QLabel(label_text)
        lb.setObjectName("SubHead")
        self.body_lay.addWidget(lb)
        row = QHBoxLayout()
        row.setSpacing(8)
        return row


# ─────────────────────────────────────────────────────────────
# 字段行
# ─────────────────────────────────────────────────────────────


class FieldRow(QWidget):
    """「标签 + 输入框 + 单位」一行。"""

    changed = Signal(float)

    def __init__(self, label: str, value: float = 0.0, *, unit: str = "",
                 minimum: float = -100000.0, maximum: float = 100000.0,
                 decimals: int = 1, step: float = 0.5,
                 integer: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._integer = integer
        self._guard = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lb = QLabel(label)
        lb.setObjectName("FldLabel")
        lb.setFixedWidth(58)
        lay.addWidget(lb)

        if integer:
            self.spin: QSpinBox | QDoubleSpinBox = QSpinBox()
            self.spin.setRange(int(minimum), int(maximum))
            self.spin.setSingleStep(int(step) or 1)
        else:
            self.spin = QDoubleSpinBox()
            self.spin.setRange(minimum, maximum)
            self.spin.setDecimals(decimals)
            self.spin.setSingleStep(step)
        self.spin.setObjectName("Input")
        self.spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setValue(int(value) if integer else float(value))
        self.spin.valueChanged.connect(self._on_change)
        lay.addWidget(self.spin, 1)

        if unit:
            u = QLabel(unit)
            u.setObjectName("FldUnit")
            u.setFixedWidth(22)
            u.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            lay.addWidget(u)
        else:
            lay.addSpacing(22)

    def _on_change(self, _v) -> None:
        if not self._guard:
            self.changed.emit(self.value())

    def value(self) -> float:
        return float(self.spin.value())

    def set_value(self, v: float) -> None:
        """外部设值（不触发 changed，避免回环）。"""
        self._guard = True
        self.spin.setValue(int(v) if self._integer else float(v))
        self._guard = False

    def set_enabled(self, on: bool) -> None:
        self.spin.setEnabled(on)


# ─────────────────────────────────────────────────────────────
# 滑块行
# ─────────────────────────────────────────────────────────────


class SliderRow(QWidget):
    """「标签 + 滑块 + 数值文字」一行。

    ★ 滑块内部一律用 int（Qt 要求），对外用 float 的 `value()`。
      `scale` 是「1 个 float 单位 = 多少个 int 刻度」。
      例如 0.00~0.50 的偏移量，scale=100 就能有 1% 的精度。

    ★ `display` 控制标签怎么显示内部值：
      - "percent"：把值当比例显示成整数百分比（0.08 → 「8%」），
        这是偏移量/间距这类参数想要的。
      - "raw"：原样按 fmt 格式化（如 3.0 → 「±3°」）。
    """

    changed = Signal(float)

    def __init__(self, label: str, *, minimum: float = 0.0,
                 maximum: float = 0.5, value: float = 0.1,
                 scale: int = 100, fmt: str = "{:.0f}%",
                 display: str = "percent", suffix: str = "",
                 prefix: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._scale = scale
        self._fmt = fmt
        self._display = display
        self._suffix = suffix
        self._prefix = prefix
        self._guard = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lb = QLabel(label)
        lb.setObjectName("FldLabel")
        lb.setFixedWidth(58)
        lay.addWidget(lb)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("Slider")
        self.slider.setRange(int(round(minimum * scale)), int(round(maximum * scale)))
        self.slider.setValue(int(round(value * scale)))
        self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider.valueChanged.connect(self._on_change)
        lay.addWidget(self.slider, 1)

        self.val_lb = QLabel()
        self.val_lb.setObjectName("FldValue")
        self.val_lb.setFixedWidth(62)
        self.val_lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.val_lb)

        self._refresh_label()

    def _on_change(self, _v: int) -> None:
        self._refresh_label()
        if not self._guard:
            self.changed.emit(self.value())

    def _refresh_label(self) -> None:
        v = self.value()
        if self._display == "percent":
            # 比例 → 整数百分比：0.08 → "8%"
            txt = f"{v * 100:.0f}%"
        else:
            txt = self._fmt.format(v)
        self.val_lb.setText(f"{self._prefix}{txt}{self._suffix}")

    def value(self) -> float:
        return self.slider.value() / float(self._scale)

    def set_value(self, v: float) -> None:
        self._guard = True
        self.slider.setValue(int(round(v * self._scale)))
        self._refresh_label()
        self._guard = False

    def set_range(self, minimum: float, maximum: float) -> None:
        self._guard = True
        self.slider.setRange(int(round(minimum * self._scale)),
                             int(round(maximum * self._scale)))
        self._guard = False
        self._refresh_label()


# ─────────────────────────────────────────────────────────────
# 预设按钮组
# ─────────────────────────────────────────────────────────────


class PresetButtons(QWidget):
    """一排互斥的预设按钮。选中项加 on 属性由 QSS 上色。"""

    picked = Signal(str)

    def __init__(self, items: list[tuple[str, str]], *, current: str = "",
                 parent: QWidget | None = None):
        """items: [(key, 显示文字), ...]"""
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        self._current = ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        for key, text in items:
            b = QPushButton(text)
            b.setObjectName("Preset")
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(False)
            b.clicked.connect(lambda _c=False, k=key: self.set_current(k, emit=True))
            self._buttons[key] = b
            lay.addWidget(b, 1)

        if current:
            self.set_current(current)

    def set_current(self, key: str, *, emit: bool = False) -> None:
        if key not in self._buttons:
            return
        self._current = key
        for k, b in self._buttons.items():
            b.setProperty("on", k == key)
            b.style().unpolish(b)
            b.style().polish(b)
        if emit:
            self.picked.emit(key)

    def current(self) -> str:
        return self._current


# ─────────────────────────────────────────────────────────────
# 列勾选
# ─────────────────────────────────────────────────────────────


class ColumnChecks(QWidget):
    """「参与填充的列」—— 1..N 的方块，点一下切换。

    ★ 语义与 grid_service.toggle_col 对齐：
      空列表 = 全选（这是服务的既有约定，别在这里另立一套）。
      但对用户来说「全选」要显示成全部亮着，所以内部展开成 [0..n-1]。
    """

    changed = Signal(list)          # 传当前参与列（0 基，升序）

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cols = 10
        self._active: list[int] = list(range(10))

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._boxes: list[QCheckBox] = []

        self._rebuild()

    def _rebuild(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._boxes.clear()

        # 每行 5 个，超过换行（10 列正好两行）
        per_row = 5
        for i in range(self._cols):
            cb = QCheckBox(str(i + 1))
            cb.setObjectName("ColChk")
            cb.setChecked(i in self._active)
            cb.stateChanged.connect(self._on_toggle)
            self._grid.addWidget(cb, i // per_row, i % per_row)
            self._boxes.append(cb)
        # 不足一行时补一个弹簧，避免方块被拉宽
        if self._cols % per_row:
            self._grid.setColumnStretch(self._cols % per_row, 1)

    def _on_toggle(self, _state: int) -> None:
        self._active = [i for i, cb in enumerate(self._boxes) if cb.isChecked()]
        self.changed.emit(list(self._active))

    def set_cols(self, cols: int, active: list[int] | None = None) -> None:
        cols = max(1, min(60, int(cols)))
        if cols == self._cols and active is None:
            return
        self._cols = cols
        self._active = list(active) if active else list(range(cols))
        self._rebuild()

    def set_active(self, active: list[int] | None) -> None:
        """外部设值。空 = 全选。"""
        a = list(active) if active else list(range(self._cols))
        self._active = a
        for i, cb in enumerate(self._boxes):
            cb.blockSignals(True)
            cb.setChecked(i in a)
            cb.blockSignals(False)

    def active(self) -> list[int]:
        return list(self._active)

    def set_all(self, on: bool) -> None:
        self._active = list(range(self._cols)) if on else []
        for cb in self._boxes:
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
        self.changed.emit(list(self._active))


# ─────────────────────────────────────────────────────────────
# 竖排单选
# ─────────────────────────────────────────────────────────────


class SegmentedRadios(QWidget):
    """竖排单选项（填写内容的三种模式）。

    ★ 用 checked 变化驱动，且 set_current 要阻塞信号 ——
      否则「程序设值 → 触发 changed → 上层再设值」会绕成环。
    """

    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], *, current: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._radios: dict[str, QRadioButton] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        for key, text in items:
            rb = QRadioButton(text)
            rb.setObjectName("ModeRadio")
            rb.setCursor(Qt.PointingHandCursor)
            rb.toggled.connect(
                lambda checked, k=key: self._on_toggle(k) if checked else None
            )
            self._radios[key] = rb
            lay.addWidget(rb)

        if current:
            self.set_current(current)

    def _on_toggle(self, key: str) -> None:
        self.changed.emit(key)

    def set_current(self, key: str) -> None:
        rb = self._radios.get(key)
        if rb is None:
            return
        for k, r in self._radios.items():
            r.blockSignals(True)
            r.setChecked(k == key)
            r.blockSignals(False)

    def current(self) -> str:
        for k, r in self._radios.items():
            if r.isChecked():
                return k
        return ""

    def set_enabled(self, on: bool) -> None:
        for r in self._radios.values():
            r.setEnabled(on)


# ─────────────────────────────────────────────────────────────
# 一行小工具
# ─────────────────────────────────────────────────────────────


class TextRow(QWidget):
    """「标签 + 单行文本」一行（随机种子用）。"""

    changed = Signal(str)

    def __init__(self, label: str, *, placeholder: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lb = QLabel(label)
        lb.setObjectName("FldLabel")
        lb.setFixedWidth(58)
        lay.addWidget(lb)

        self.edit = QLineEdit()
        self.edit.setObjectName("Input")
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        lay.addWidget(self.edit, 1)

    def text(self) -> str:
        return self.edit.text()

    def set_text(self, s: str) -> None:
        self.edit.blockSignals(True)
        self.edit.setText(s)
        self.edit.blockSignals(False)


def make_hint(text: str, role: str = "Hint") -> QLabel:
    """提示文字。role 可选 Hint / HintWarn / HintInfo / HintOk / HintDanger。"""
    lb = QLabel(text)
    lb.setObjectName(role)
    lb.setWordWrap(True)
    return lb


def make_button(text: str, variant: str = "",
                on_click: Callable | None = None) -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setObjectName(variant)
    b.setCursor(Qt.PointingHandCursor)
    if on_click is not None:
        b.clicked.connect(on_click)
    return b
