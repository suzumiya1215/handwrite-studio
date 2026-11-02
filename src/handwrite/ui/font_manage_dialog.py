"""字形库管理对话框 —— 列出 / 选择 / 新建 / 删除字形库。

设计依据：
- 需求文档 §5.4（管理界面）、界面原型.html 首页卡片②「管理」
- 模块设计说明书 §4.11

★ 这个对话框只负责「陈列 + 选择」，真正的载入逻辑交给控制器
  （通过 on_pick 回调），删除才在这里直接调 font_service。
  —— 这样「选了之后下一步去哪」完全由控制器决定。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..core.services import font_service as fts
from .dialogs import ConfirmDialog


class FontManageDialog(QDialog):
    """字形库管理。

    信号：
        font_picked(str)   用户点「选择并使用」，传库 id
        new_requested()    用户点「新建字形库」
    """

    font_picked = Signal(str)
    new_requested = Signal()

    def __init__(self, parent: object | None = None, *, title: str = "字形库管理"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("AppDialog")
        self.setModal(True)
        self.resize(560, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title_lb = QLabel("字形库管理")
        title_lb.setObjectName("DialogTitle")
        root.addWidget(title_lb)

        hint = QLabel(
            "选择一套字迹用于填表；也可以新建一套。" "每套字形库都来自你手写的字，不会和别人混。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.list = QListWidget()
        self.list.setObjectName("FontList")
        self.list.itemDoubleClicked.connect(lambda _i: self._choose())
        root.addWidget(self.list, 1)

        self.empty_lb = QLabel("还没有任何字形库。点「新建字形库」开始采集。")
        self.empty_lb.setObjectName("Hint")
        self.empty_lb.setWordWrap(True)
        self.empty_lb.setVisible(False)
        root.addWidget(self.empty_lb)

        # ── 底部按钮 ──
        foot = QHBoxLayout()
        foot.setSpacing(8)

        self.btn_new = QPushButton("新建字形库")
        self.btn_new.setObjectName("Primary")
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.clicked.connect(self._new)
        foot.addWidget(self.btn_new)

        foot.addStretch(1)

        self.btn_del = QPushButton("删除")
        self.btn_del.setObjectName("Danger")
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.clicked.connect(self._delete)
        foot.addWidget(self.btn_del)

        self.btn_use = QPushButton("选择并使用")
        self.btn_use.setObjectName("Primary")
        self.btn_use.setCursor(Qt.PointingHandCursor)
        self.btn_use.clicked.connect(self._choose)
        foot.addWidget(self.btn_use)

        self.btn_cancel = QPushButton("关闭")
        self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setDefault(True)
        self.btn_cancel.clicked.connect(self.reject)
        foot.addWidget(self.btn_cancel)

        root.addLayout(foot)

        self._refresh()

    # ── 数据 ──────────────────────────────────────────────────

    def _refresh(self) -> None:
        self.list.clear()
        libs = fts.list_libraries()
        self.empty_lb.setVisible(not libs)
        self.list.setVisible(bool(libs))
        self.btn_use.setEnabled(bool(libs))
        self.btn_del.setEnabled(bool(libs))

        for lib in libs:
            item = QListWidgetItem()
            name = getattr(lib, "name", "") or "未命名"
            count = getattr(lib, "char_count", 0)
            cov = getattr(lib, "coverage", "") or ""
            sub = f"{count} 个字符" + (f"　·　已采集 {cov}" if cov else "")
            item.setText(f"{name}\n{sub}")
            item.setData(Qt.UserRole, getattr(lib, "id", ""))
            item.setToolTip(f"id: {getattr(lib, 'id', '')}")
            self.list.addItem(item)

    def _selected_id(self) -> str:
        it = self.list.currentItem()
        if it is None:
            return ""
        return str(it.data(Qt.UserRole) or "")

    def _choose(self) -> None:
        fid = self._selected_id()
        if not fid:
            return
        self.font_picked.emit(fid)
        self.accept()

    def _new(self) -> None:
        self.new_requested.emit()
        self.accept()

    def _delete(self) -> None:
        fid = self._selected_id()
        if not fid:
            return
        name = self.list.currentItem().text().split("\n")[0]
        ok = ConfirmDialog.ask(
            self,
            "删除字形库",
            f"确定要删除「{name}」吗？这个操作不能撤销。",
            ok_text="删除",
            cancel_text="取消",
            danger=True,
        )
        if not ok:
            return
        try:
            fts.delete_library(fid)
        except Exception as e:            # noqa: BLE001
            from ..core.errors import AppError

            msg = e.user_message if isinstance(e, AppError) else str(e)
            ConfirmDialog.ask(self, "删除失败", msg, ok_text="知道了")
            return
        self._refresh()


# ─────────────────────────────────────────────────────────────
# 便捷入口
# ─────────────────────────────────────────────────────────────


def ask_font(parent, *, allow_new: bool = True):
    """弹窗管理字形库，返回选中的库 id（取消返回 ""）。"""
    dlg = FontManageDialog(parent)
    if not allow_new:
        dlg.btn_new.setVisible(False)
    picked: list[str] = []

    def _capture(fid: str) -> None:
        picked.append(fid)

    dlg.font_picked.connect(_capture)
    dlg.exec()
    return picked[0] if picked else ""


if __name__ == "__main__":
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ..core import config as cfg

    cfg.ensure_dirs()
    d = FontManageDialog()
    print("字形库管理对话框自检通过（列表项=%d）" % d.list.count())
