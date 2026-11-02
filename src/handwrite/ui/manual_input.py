"""手动录入弹窗 —— 一张可粘贴的表格编辑器。

设计依据：
- 界面原型.html 屏 4（`grid-editor` / 黄底「将跳过」/ 从表格粘贴 / 清空）
- 需求文档 §6.4（手动录入）
- 契约 C9~C11：超范围字符跳过并提示，不报错

三个要点：
1. **支持 Excel / WPS 整块粘贴**：从剪贴板拿到的是 Tab + 换行的文本，
   按 `content_service.parse_table` 切分后填进表格。
2. **黄底提示**：单元格里有当前字形库不支持的字符时，把该格标黄并
   在表头写明「黄底表示该字符没有字形，将被跳过」。
   —— 比导出后弹「有 3 个字符被跳过」更早、更直观。
3. **行号 / 列号固定**：用 QTableWidget 的 verticalHeader 当行号，
   避免像原型那样把行号塞成第一列（排序/粘贴都会错位）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.charset import CHARSET_V1
from ..core.services import content_service as cs

#: 黄底（与原型 `.empty` 的 #FEFAF2 一致，但用饱和一点好认）
WARN_BG = QColor(255, 246, 224)
WARN_FG = QColor(150, 100, 20)
NORMAL_BG = QColor(255, 255, 255)


class ManualInputDialog(QDialog):
    """手动录入内容。

    ★ 用 `run()` 静态方法调用而不是 `exec()`：
      非模态语义上更干净，返回 (接受?, matrix)。但同时保留 exec() 兼容。

    数据契约：
        run(rows, cols, matrix, supported, parent) -> (bool, ContentMatrix)
    """

    def __init__(self, rows: int, cols: int, matrix=None, *,
                 supported: list[str] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("手动录入内容")
        self.setObjectName("AppDialog")
        self.setModal(True)

        self._rows = max(1, int(rows))
        self._cols = max(1, int(cols))
        self._supported = set(supported or CHARSET_V1)
        self._result_matrix = None

        self.resize(960, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        # ── 表头行 ──
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("手动录入内容")
        title.setObjectName("DialogTitle")
        head.addWidget(title)

        self.meta_lb = QLabel(
            f"共 {self._rows} 行 × {self._cols} 列　·　"
            "黄底表示该字符没有字形，将被跳过"
        )
        self.meta_lb.setObjectName("Hint")
        head.addWidget(self.meta_lb)
        head.addStretch(1)
        root.addLayout(head)

        # ── 表格 ──
        self.table = QTableWidget(self._rows, self._cols)
        self.table.setObjectName("GridEditor")
        self.table.setHorizontalHeaderLabels([f"第{i + 1}列" for i in range(self._cols)])
        self.table.setVerticalHeaderLabels([f"第{i + 1}行" for i in range(self._rows)])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(84)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.itemChanged.connect(self._on_item_changed)

        # ★ 粘贴：QTableWidget 原生支持多行文本粘贴（内部用 Tab 切分），
        #   但原生的粘贴会把内容按「当前选区左上角」铺开，且不识别
        #   「行数超过表格」的情况。这里接管 keyPressEvent 用我们自己的
        #   parse_table，保证与 content_service 的口径完全一致。
        self.table.keyPressEvent = self._on_table_key      # type: ignore[method-assign]

        root.addWidget(self.table, 1)

        # ── 统计提示 ──
        self.warn_lb = QLabel("")
        self.warn_lb.setObjectName("HintWarn")
        self.warn_lb.setWordWrap(True)
        self.warn_lb.setVisible(False)
        root.addWidget(self.warn_lb)

        # ── 底部按钮 ──
        foot = QHBoxLayout()
        foot.setSpacing(8)
        b_paste = self._btn("从剪贴板粘贴", "", self.paste_from_clipboard)
        b_clear = self._btn("清空", "Danger", self.clear_all)
        b_fill = self._btn("向下填充", "", self.fill_down)
        foot.addWidget(b_paste)
        foot.addWidget(b_clear)
        foot.addWidget(b_fill)
        foot.addStretch(1)
        b_cancel = self._btn("取消", "Ghost", self.reject)
        b_ok = self._btn("保存更改", "Primary", self._accept)
        foot.addWidget(b_cancel)
        foot.addWidget(b_ok)
        root.addLayout(foot)

        self.set_matrix(matrix)
        self._refresh_warning()

    # ── 小工具 ────────────────────────────────────────────────

    @staticmethod
    def _btn(text: str, variant: str = "", on_click=None) -> QPushButton:
        b = QPushButton(text)
        if variant:
            b.setObjectName(variant)
        b.setCursor(Qt.PointingHandCursor)
        b.setMinimumWidth(88)
        if on_click is not None:
            b.clicked.connect(on_click)
        return b

    # ── 数据进出 ──────────────────────────────────────────────

    def set_matrix(self, matrix) -> None:
        """把 ContentMatrix 灌进表格。matrix 为 None 时全空。"""
        self.table.blockSignals(True)
        try:
            for r in range(self._rows):
                for c in range(self._cols):
                    val = ""
                    if matrix is not None:
                        try:
                            val = matrix.data[r][c]
                        except (IndexError, TypeError):
                            val = ""
                    item = QTableWidgetItem("" if val is None else str(val))
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    self.table.setItem(r, c, item)
        finally:
            self.table.blockSignals(False)
        self._recolor_all()

    def get_matrix(self):
        """读出表格 → ContentMatrix（尺寸与开弹窗时一致）。"""
        data: list[list[str]] = []
        for r in range(self._rows):
            row: list[str] = []
            for c in range(self._cols):
                item = self.table.item(r, c)
                row.append(item.text().strip() if item is not None else "")
            data.append(row)
        return cs.ContentMatrix(rows=self._rows, cols=self._cols, data=data)

    # ── 交互 ──────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        self._recolor(item)
        self._refresh_warning()

    def _on_table_key(self, event) -> None:
        """Ctrl+V 走我们自己的粘贴；其余交给基类。"""
        if (event.key() == Qt.Key_V and event.modifiers() & Qt.ControlModifier):
            self.paste_from_clipboard()
            event.accept()
            return
        if event.key() == Qt.Key_Delete:
            self._clear_selection()
            event.accept()
            return
        QTableWidget.keyPressEvent(self.table, event)

    def paste_from_clipboard(self) -> None:
        """从剪贴板整块粘贴。

        ★ 从「当前选中格的左上角」开始铺 —— 与 Excel 的行为一致。
          如果粘贴区域超出表格范围，多出来的部分丢弃并提示（不静默丢）。
        """
        from PySide6.QtWidgets import QApplication

        text = QApplication.clipboard().text()
        if not text.strip():
            self._show_warn("剪贴板里没有文字。先在 Excel 里选中区域按 Ctrl+C。")
            return

        rows = cs.parse_table(text)
        if not rows:
            self._show_warn("剪贴板的内容没法识别成表格，请确认是从 Excel 复制的。")
            return

        r0, c0 = self._selection_origin()
        max_r = len(rows)
        max_c = max((len(r) for r in rows), default=0)

        clipped_rows = max(0, r0 + max_r - self._rows)
        clipped_cols = max(0, c0 + max_c - self._cols)

        self.table.blockSignals(True)
        try:
            for i, row in enumerate(rows):
                r = r0 + i
                if r >= self._rows:
                    break
                for j, val in enumerate(row):
                    c = c0 + j
                    if c >= self._cols:
                        break
                    item = self.table.item(r, c)
                    if item is None:
                        item = QTableWidgetItem("")
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                        self.table.setItem(r, c, item)
                    item.setText(str(val))
        finally:
            self.table.blockSignals(False)

        self._recolor_all()
        self._refresh_warning()

        if clipped_rows or clipped_cols:
            self._show_warn(
                f"粘贴的内容比表格大，超出部分已忽略"
                f"（超出 {clipped_rows} 行 / {clipped_cols} 列）。"
            )
        else:
            self._hide_warn()

    def _clear_selection(self) -> None:
        for item in self.table.selectedItems():
            item.setText("")
        self._refresh_warning()

    def _selection_origin(self) -> tuple[int, int]:
        sel = self.table.selectedRanges()
        if sel:
            return max(0, sel[0].topRow()), max(0, sel[0].leftColumn())
        return 0, 0

    def fill_down(self) -> None:
        """把选中区域的第一个值往下填充到整个选区（录同一列的重复值好用）。"""
        sel = self.table.selectedRanges()
        if not sel:
            self._show_warn("先选中一块区域，再点「向下填充」。")
            return
        r = sel[0]
        top = r.topRow()
        src = self.table.item(top, r.leftColumn())
        if src is None or not src.text():
            self._show_warn("选中区域的第一行是空的，没有东西可以往下填。")
            return
        val = src.text()
        self.table.blockSignals(True)
        try:
            for row in range(top + 1, r.bottomRow() + 1):
                for col in range(r.leftColumn(), r.rightColumn() + 1):
                    item = self.table.item(row, col)
                    if item is None:
                        item = QTableWidgetItem("")
                        self.table.setItem(row, col, item)
                    item.setText(val)
        finally:
            self.table.blockSignals(False)
        self._recolor_all()
        self._refresh_warning()

    def clear_all(self) -> None:
        self.table.blockSignals(True)
        try:
            for r in range(self._rows):
                for c in range(self._cols):
                    item = self.table.item(r, c)
                    if item is not None:
                        item.setText("")
        finally:
            self.table.blockSignals(False)
        self._recolor_all()
        self._refresh_warning()

    # ── 黄底标注 ──────────────────────────────────────────────

    def _bad_chars(self, text: str) -> list[str]:
        return [ch for ch in text if ch and ch not in self._supported]

    def _recolor(self, item: QTableWidgetItem) -> None:
        bad = self._bad_chars(item.text())
        if bad:
            item.setBackground(QBrush(WARN_BG))
            item.setForeground(QBrush(WARN_FG))
            item.setToolTip("这些字符没有字形，标记时会跳过：" + " ".join(bad))
        else:
            item.setBackground(QBrush(NORMAL_BG))
            item.setForeground(QBrush(QColor(34, 38, 46)))
            item.setToolTip("")

    def _recolor_all(self) -> None:
        for r in range(self._rows):
            for c in range(self._cols):
                item = self.table.item(r, c)
                if item is not None:
                    self._recolor(item)
        self.table.viewport().update()

    def _refresh_warning(self) -> None:
        """统计有多少格会被跳过，更新底部提示与表头说明。"""
        report = cs.check_charset(self.get_matrix())
        n_cells = report.affected_cells
        if n_cells <= 0:
            self.meta_lb.setText(
                f"共 {self._rows} 行 × {self._cols} 列　·　"
                "黄底表示该字符没有字形，将被跳过"
            )
            self.warn_lb.setVisible(False)
            return

        chars = "".join(sorted(report.unsupported.keys()))
        self.warn_lb.setText(
            f"有 {n_cells} 个格子里含没有字形的字符，填充时会跳过它们："
            f"{chars}"
            f"（共 {report.total_unsupported} 次）。"
            " 想把名字之类的内容也写上去，得先回「新建字形库」补采。"
        )
        self.warn_lb.setVisible(True)

    def _show_warn(self, text: str) -> None:
        self.warn_lb.setText(text)
        self.warn_lb.setVisible(True)

    def _hide_warn(self) -> None:
        self.warn_lb.setVisible(False)

    # ── 收尾 ──────────────────────────────────────────────────

    def _accept(self) -> None:
        self._result_matrix = self.get_matrix()
        self.accept()

    def result_matrix(self):
        return self._result_matrix


# ─────────────────────────────────────────────────────────────
# 便捷入口
# ─────────────────────────────────────────────────────────────


def ask_manual_input(parent, rows: int, cols: int, matrix=None,
                     supported: list[str] | None = None):
    """弹窗并返回 (是否确定, ContentMatrix)。

    ★ 提供一个「非 exec 不可」的静态入口，但调用方在测试里可以绕过：
      测试直接构造 ManualInputDialog 调 set_matrix / get_matrix。
    """
    dlg = ManualInputDialog(rows, cols, matrix, supported=supported, parent=parent)
    ok = dlg.exec() == QDialog.Accepted
    return ok, (dlg.result_matrix() if ok else None)


# ─────────────────────────────────────────────────────────────
# 自检（不起真弹窗）
# ─────────────────────────────────────────────────────────────


def _selftest() -> int:
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    fails = []
    d = ManualInputDialog(6, 4)
    if d.table.rowCount() != 6 or d.table.columnCount() != 4:
        fails.append("表格尺寸不对")

    # 灌内容 → 读出
    m = cs.ContentMatrix(rows=6, cols=4, data=[
        ["1", "张三", "36.5", "36.4"],
        ["2", "李四", "36.6", "36.5"],
        ["", "", "", ""], ["", "", "", ""], ["", "", "", ""], ["", "", "", ""],
    ])
    d.set_matrix(m)
    got = d.get_matrix()
    if got.data[0][0] != "1" or got.data[0][1] != "张三" or got.data[1][2] != "36.6":
        fails.append(f"往返失败 {got.data[0]}")

    # 黄底标注：张三 / 李四 都是不支持的字符
    item = d.table.item(0, 1)
    if item.background().color() != WARN_BG:
        fails.append(f"中文格没标黄 {item.background().color().name()}")
    item_ok = d.table.item(0, 2)
    if item_ok.background().color() != NORMAL_BG:
        fails.append(f"合法格被标黄 {item_ok.background().color().name()}")

    # 提示条
    # ★ 用 isVisibleTo 而不是 isVisible：弹窗还没 show() 时后者恒为 False
    if not d.warn_lb.isVisibleTo(d):
        fails.append("有跳字符时没显示提示条")
    if "张三" not in (item.toolTip() or "") and "张" not in (item.toolTip() or ""):
        fails.append(f"格子的 tooltip 没写清楚：{item.toolTip()!r}")

    # 粘贴
    QApplication.clipboard().setText("A\tB\tC\nD\tE\tF")
    d.table.setCurrentCell(2, 0)
    d.paste_from_clipboard()
    if d.table.item(2, 0).text() != "A" or d.table.item(3, 2).text() != "F":
        fails.append(f"粘贴落点不对 {d.table.item(2, 0).text()}, {d.table.item(3, 2).text()}")

    # 全区清空
    d.clear_all()
    if any(d.table.item(r, c).text() for r in range(6) for c in range(4)):
        fails.append("清空没清干净")

    if fails:
        print("手动录入弹窗自检失败：", file=sys.stderr)
        for f in fails:
            print("  ✗", f, file=sys.stderr)
        return 1
    print("手动录入弹窗自检通过（往返 / 黄底 / 粘贴 / 清空）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
