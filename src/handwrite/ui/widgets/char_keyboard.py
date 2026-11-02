"""12 键字符键盘 + 采集进度条（采集向导第三步用）。

设计依据：
- 界面原型.html 屏 3 的 `.keyboard` / `.key` / `.progress-strip`
- 需求文档 §5.3（标注：点字符 → 点框批量标注）
- 契约 C9：v1 只采集 12 个字符（数字 + 逗号 + 句点）

★ 键盘的「缺字」状态（橙色高亮）不是装饰：
  它直接告诉用户「这两个字符我还没采到，导出后用不上」，
  比导出后再弹个错框友好得多。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.charset import CHARSET_V1

#: 键盘布局：数字一行 + 标点跟在后面（与原型一致）
DIGITS = [str(i) for i in range(10)]
PUNCTS = [",", "."]

#: 字符的显示名（逗号/句点在按钮上不好认，补个中文说明）
CHAR_LABEL = {
    ",": "逗号",
    ".": "句点",
}


class CharKeyboard(QWidget):
    """12 键字符键盘。

    信号：
        char_clicked(ch)   点某个键（选中态切换）
        selection_changed(ch | "") 当前标注模式下的字符，空串 = 退出标注

    ★ 「再次点击同一个字符退出标注模式」是原型明确要求的行为 ——
      流程是：点「3」→ 键盘上 3 亮起 → 依次点框都标成 3 →
      再点一次「3」→ 熄灭 → 回到普通选择模式。
    """

    char_clicked = Signal(str)
    selection_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CharKeyboard")

        self._selected = ""
        self._buttons: dict[str, QPushButton] = {}
        self._counts: dict[str, int] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ── 键位行 ──
        row_host = QWidget()
        rl = QHBoxLayout(row_host)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        lbl = QLabel("数字")
        lbl.setObjectName("KbLabel")
        rl.addWidget(lbl)

        for ch in DIGITS:
            b = self._make_key(ch)
            rl.addWidget(b)

        rl.addSpacing(14)

        lbl2 = QLabel("标点")
        lbl2.setObjectName("KbLabel")
        rl.addWidget(lbl2)

        for ch in PUNCTS:
            b = self._make_key(ch)
            rl.addWidget(b)

        rl.addStretch(1)
        root.addWidget(row_host)

        # ── 操作行 ──
        ops = QHBoxLayout()
        ops.setSpacing(8)
        ops.addWidget(self._hint(
            "点下面的字符进入标注模式，再依次点框就能批量标注；"
            "再点一次同一个字符退出标注。"
        ), 1)
        root.addLayout(ops)

    def _make_key(self, ch: str) -> QPushButton:
        b = QPushButton(ch)
        b.setObjectName("KbKey")
        b.setFixedSize(40, 40)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(CHAR_LABEL.get(ch, ch))
        b.clicked.connect(lambda _c=False, k=ch: self._on_key(k))
        self._buttons[ch] = b
        self._counts[ch] = 0
        return b

    @staticmethod
    def _hint(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("Hint")
        lb.setWordWrap(True)
        return lb

    # ── 交互 ──────────────────────────────────────────────────

    def _on_key(self, ch: str) -> None:
        if self._selected == ch:
            self._selected = ""
        else:
            self._selected = ch
        self._refresh_states()
        self.char_clicked.emit(ch if self._selected else "")
        self.selection_changed.emit(self._selected)

    def _refresh_states(self) -> None:
        for ch, b in self._buttons.items():
            b.setProperty("on", ch == self._selected)
            n = self._counts.get(ch, 0)
            b.setProperty("miss", n <= 0)
            b.style().unpolish(b)
            b.style().polish(b)

    # ── 对外 ──────────────────────────────────────────────────

    def selected(self) -> str:
        return self._selected

    def clear_selection(self) -> None:
        self._selected = ""
        self._refresh_states()
        self.selection_changed.emit("")

    def set_counts(self, counts: dict[str, int], *,
                   charset: list[str] | None = None) -> None:
        """更新每个字符的样本数。为 0 的会亮橙底（缺字提示）。"""
        cs = charset or CHARSET_V1
        for ch in cs:
            if ch not in self._buttons:
                b = self._make_key(ch)
                # 新字符插到末尾（正常不会走到，v1 键盘是固定的 12 键）
                layout = self.layout()
                if layout is not None:
                    host = layout.itemAt(0).widget()
                    if host is not None and host.layout() is not None:
                        host.layout().addWidget(b)      # type: ignore[union-attr]
        for ch, b in self._buttons.items():
            self._counts[ch] = int(counts.get(ch, 0))
        self._refresh_states()

    def missing(self, charset: list[str] | None = None) -> list[str]:
        cs = charset or CHARSET_V1
        return [ch for ch in cs if self._counts.get(ch, 0) <= 0]


class CaptureProgress(QWidget):
    """采集进度条：已采集 x / y + 进度 + 缺字提示。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CaptureProgress")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.text_lb = QLabel("已采集 0 / 12")
        self.text_lb.setObjectName("ProgressText")
        lay.addWidget(self.text_lb)

        self.bar = QProgressBar()
        self.bar.setObjectName("Progress")
        self.bar.setRange(0, 12)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setMinimumWidth(140)
        lay.addWidget(self.bar, 1)

        self.miss_lb = QLabel("")
        self.miss_lb.setObjectName("HintWarn")
        self.miss_lb.setWordWrap(True)
        lay.addWidget(self.miss_lb, 0)

        self.set_counts({})

    def set_counts(self, counts: dict[str, int],
                   charset: list[str] | None = None) -> None:
        cs = charset or CHARSET_V1
        total = len(cs)
        done = sum(1 for ch in cs if counts.get(ch, 0) > 0)
        self.text_lb.setText(f"已采集 {done} / {total}")
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)

        miss = [ch for ch in cs if counts.get(ch, 0) <= 0]
        # ★ 缺字与齐全要换 objectName（HintWarn ↔ HintOk），
        #   换完必须 unpolish/polish 一次，否则 QSS 不会重新上色。
        if miss:
            show = " ".join(CHAR_LABEL.get(c, c) for c in miss)
            self.miss_lb.setText(f"⚠ 还缺少：{show}")
            self.miss_lb.setObjectName("HintWarn")
        else:
            self.miss_lb.setText("✓ 12 个字符都齐了")
            self.miss_lb.setObjectName("HintOk")
        self.miss_lb.style().unpolish(self.miss_lb)
        self.miss_lb.style().polish(self.miss_lb)
        self.miss_lb.setVisible(True)
