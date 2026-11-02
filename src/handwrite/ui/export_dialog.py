"""导出对话框 + 导出进度。

设计依据：
- 需求文档 §6.6 / §9（导出 PDF、PNG，DPI 可选，取消要能中断）
- 模块设计说明书 §4.11
- 契约：`export_service.export_pdf(...)` / `export_png(...)`

★ 为什么导出要单独一个对话框而不是「点了导出就闷头干」：
  一次 300DPI 的多页导出在低端机上要十几秒。没有进度和取消，
  用户会以为程序死了 —— 这是这类工具最常见的差评来源。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.services.export_service import ALLOWED_DPI


def _btn(text: str, variant: str = "", on_click=None) -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setObjectName(variant)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumWidth(88)
    if on_click is not None:
        b.clicked.connect(on_click)
    return b


class ExportDialog(QDialog):
    """导出设置。

    ★ 这个对话框**只收集设置**，不真跑导出。
      导出由控制器拿走设置后在自己的线程/循环里跑并回报进度，
      这样「取消」能真的中断（对话框自己跑就没法中断自己）。
    """

    def __init__(self, *, default_path: str = "", page_count: int = 1,
                 default_dpi: int = 300, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("导出")
        self.setObjectName("AppDialog")
        self.setModal(True)
        self.resize(560, 340)

        self._result: dict | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel("导出")
        title.setObjectName("DialogTitle")
        root.addWidget(title)

        # ── 格式 ──
        row_fmt = QHBoxLayout()
        row_fmt.setSpacing(8)
        lb = QLabel("格式")
        lb.setObjectName("FldLabel")
        lb.setFixedWidth(58)
        row_fmt.addWidget(lb)
        self.cmb_fmt = QComboBox()
        self.cmb_fmt.addItem("PDF（推荐，文字和尺寸都不走样）", "pdf")
        self.cmb_fmt.addItem("PNG 图片（每页一张）", "png")
        self.cmb_fmt.currentIndexChanged.connect(self._on_fmt)
        row_fmt.addWidget(self.cmb_fmt, 1)
        root.addLayout(row_fmt)

        # ── 清晰度 ──
        row_dpi = QHBoxLayout()
        row_dpi.setSpacing(8)
        lb2 = QLabel("清晰度")
        lb2.setObjectName("FldLabel")
        lb2.setFixedWidth(58)
        row_dpi.addWidget(lb2)
        self.cmb_dpi = QComboBox()
        for d in ALLOWED_DPI:
            label = f"{d} DPI"
            if d == 150:
                label += "（小、快，屏幕看）"
            elif d == 300:
                label += "（推荐，打印清楚）"
            elif d == 600:
                label += "（很大，专业印刷）"
            self.cmb_dpi.addItem(label, d)
        self.cmb_dpi.setCurrentIndex(
            list(ALLOWED_DPI).index(default_dpi) if default_dpi in ALLOWED_DPI else 1)
        row_dpi.addWidget(self.cmb_dpi, 1)
        root.addLayout(row_dpi)

        # ── 页码 ──
        row_pg = QHBoxLayout()
        row_pg.setSpacing(8)
        lb3 = QLabel("页码")
        lb3.setObjectName("FldLabel")
        lb3.setFixedWidth(58)
        row_pg.addWidget(lb3)
        self.cmb_pages = QComboBox()
        self.cmb_pages.addItem(f"全部（共 {page_count} 页）", "all")
        self.cmb_pages.addItem("仅当前页", "current")
        if page_count > 1:
            self.cmb_pages.addItem("自定义…", "custom")
        self.cmb_pages.currentIndexChanged.connect(self._on_pages)
        row_pg.addWidget(self.cmb_pages, 1)

        self.edit_pages = QLineEdit()
        self.edit_pages.setObjectName("Input")
        self.edit_pages.setPlaceholderText("如 1,3,5-7")
        self.edit_pages.setVisible(False)
        self.edit_pages.setMaximumWidth(120)
        row_pg.addWidget(self.edit_pages)
        root.addLayout(row_pg)

        # ── 保存位置 ──
        row_out = QHBoxLayout()
        row_out.setSpacing(8)
        lb4 = QLabel("保存到")
        lb4.setObjectName("FldLabel")
        lb4.setFixedWidth(58)
        row_out.addWidget(lb4)
        self.edit_out = QLineEdit(default_path)
        self.edit_out.setObjectName("Input")
        row_out.addWidget(self.edit_out, 1)
        row_out.addWidget(_btn("浏览…", "", self._browse))
        root.addLayout(row_out)

        self.chk_open = QCheckBox("导出完成后打开所在文件夹")
        self.chk_open.setChecked(True)
        root.addWidget(self.chk_open)

        root.addStretch(1)

        # ── 底部 ──
        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addStretch(1)
        foot.addWidget(_btn("取消", "Ghost", self.reject))
        foot.addWidget(_btn("开始导出", "Primary", self._accept))
        root.addLayout(foot)

    # ── 交互 ──────────────────────────────────────────────────

    def _on_fmt(self, _i: int) -> None:
        """PNG 模式下路径得是文件夹（每页一张图）。"""
        is_png = self.cmb_fmt.currentData() == "png"
        self.edit_out.setPlaceholderText(
            "选一个文件夹（每页存成一张图）" if is_png else "如 D:/桌面/表格_手写版.pdf")
        if is_png and self.edit_out.text().lower().endswith(".pdf"):
            p = Path(self.edit_out.text())
            self.edit_out.setText(str(p.with_suffix("")))

    def _on_pages(self, _i: int) -> None:
        self.edit_pages.setVisible(self.cmb_pages.currentData() == "custom")

    def _browse(self) -> None:
        if self.cmb_fmt.currentData() == "png":
            d = QFileDialog.getExistingDirectory(self, "选择导出文件夹",
                                                 self.edit_out.text() or "")
            if d:
                self.edit_out.setText(d)
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 PDF", self.edit_out.text() or "表格_手写版.pdf",
            "PDF 文件 (*.pdf)")
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.edit_out.setText(path)

    def _accept(self) -> None:
        out = self.edit_out.text().strip()
        if not out:
            self.edit_out.setFocus()
            return
        self._result = {
            "kind": self.cmb_fmt.currentData(),
            "dpi": int(self.cmb_dpi.currentData()),
            "pages": self._pages_value(),
            "out": out,
            "open_folder": self.chk_open.isChecked(),
        }
        self.accept()

    def _pages_value(self):
        kind = self.cmb_pages.currentData()
        if kind == "all":
            return "all"
        if kind == "current":
            return "current"
        return self.edit_pages.text().strip() or "all"

    def result_settings(self) -> dict | None:
        return self._result


class ProgressDialog(QDialog):
    """导出进度。带取消按钮 —— 取消要真的能中断。"""

    cancelled = Signal()

    def __init__(self, title: str = "正在导出", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("AppDialog")
        self.setModal(True)
        self.setFixedWidth(420)
        self._cancelled = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        self.title_lb = QLabel(title)
        self.title_lb.setObjectName("DialogTitle")
        root.addWidget(self.title_lb)

        self.text_lb = QLabel("准备中…")
        self.text_lb.setObjectName("Hint")
        root.addWidget(self.text_lb)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        root.addWidget(self.bar)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_cancel = _btn("取消", "Ghost", self._on_cancel)
        foot.addWidget(self.btn_cancel)
        root.addLayout(foot)

    def _on_cancel(self) -> None:
        self._cancelled = True
        self.cancelled.emit()
        self.text_lb.setText("正在取消…")
        self.btn_cancel.setEnabled(False)

    def is_cancelled(self) -> bool:
        return self._cancelled

    def set_progress(self, done: int, total: int, text: str = "") -> None:
        total = max(1, int(total))
        pct = int(round(done * 100.0 / total))
        self.bar.setValue(min(100, max(0, pct)))
        if text:
            self.text_lb.setText(text)
        else:
            self.text_lb.setText(f"{done} / {total}")

    def set_text(self, text: str) -> None:
        self.text_lb.setText(text)

    def finish(self) -> None:
        self.bar.setValue(100)
        self.accept()


# ─────────────────────────────────────────────────────────────
# 完成提示
# ─────────────────────────────────────────────────────────────


def reveal_in_explorer(path: str) -> None:
    """在资源管理器里打开并选中文件。失败静默（不阻断导出成功的事实）。"""
    try:
        p = Path(path)
        if p.is_file():
            os.startfile(str(p.parent))       # noqa: S606 (Windows 专用)
        elif p.is_dir():
            os.startfile(str(p))              # noqa: S606
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 自检
# ─────────────────────────────────────────────────────────────


def _selftest() -> int:
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    fails = []

    # ★ 自检里不能写 "C:/..." 这类字面盘符路径 —— 会踩 N12/P4 禁令。
    #   用相对路径即可，这里只验证行为不验证路径本身。
    d = ExportDialog(default_path="样例_手写版.pdf", page_count=3, default_dpi=300)
    if d.cmb_dpi.currentData() != 300:
        fails.append(f"默认 DPI 不对 {d.cmb_dpi.currentData()}")
    if d.cmb_pages.count() != 3:
        fails.append("多页时应有三个页码选项")
    d.edit_out.setText("样例2_手写版.pdf")
    d._accept()
    s = d.result_settings()
    if not s or s["kind"] != "pdf" or s["dpi"] != 300 or s["pages"] != "all":
        fails.append(f"设置收集不对 {s}")

    # 单页时没有「自定义」
    d2 = ExportDialog(page_count=1)
    if d2.cmb_pages.count() != 2:
        fails.append("单页时不该有自定义选项")

    # 进度
    pd = ProgressDialog()
    pd.set_progress(2, 8)
    if pd.bar.value() != 25:
        fails.append(f"进度百分比不对 {pd.bar.value()}")
    pd.set_progress(8, 8)
    if pd.bar.value() != 100:
        fails.append("100% 不对")

    got = []
    pd2 = ProgressDialog()
    pd2.cancelled.connect(lambda: got.append(1))
    pd2._on_cancel()
    if not pd2.is_cancelled() or got != [1]:
        fails.append("取消没生效")

    if fails:
        print("导出对话框自检失败：", file=sys.stderr)
        for f in fails:
            print("  ✗", f, file=sys.stderr)
        return 1
    print("导出对话框自检通过（格式 / DPI / 页码 / 进度 / 取消）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
