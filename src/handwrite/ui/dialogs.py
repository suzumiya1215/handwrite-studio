"""通用对话框：帮助、关于、危险操作确认。

M1 只做「关于」和「帮助」两个纯展示框 + 一个通用确认框。
后续里程碑按需在这里加（如「字形库管理」「覆盖确认」）。

设计依据：需求文档 §8（界面不出现英文）；界面原型.html 首页底部「帮助 / 关于」
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core import config as cfg
from ..core.charset import CHARSET_V1


# ─────────────────────────────────────────────────────────────
# 公共外壳
# ─────────────────────────────────────────────────────────────


class _Dialog(QDialog):
    """统一外观的对话框基类：标题栏 + 内容区 + 底部按钮。"""

    def __init__(self, title: str, parent: QWidget | None = None, width: int = 480):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("AppDialog")
        self.setModal(True)
        self.resize(width, 360)

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(20, 18, 20, 16)
        self.root.setSpacing(12)

        self.title_lb = QLabel(title)
        self.title_lb.setObjectName("DialogTitle")
        self.root.addWidget(self.title_lb)

        self._btn_row: QHBoxLayout | None = None

    def _button_bar(self) -> QHBoxLayout:
        if self._btn_row is None:
            self._btn_row = QHBoxLayout()
            self._btn_row.setSpacing(10)
            self._btn_row.addStretch(1)
            self.root.addLayout(self._btn_row)
        return self._btn_row

    def add_button(self, text: str, variant: str = "", default: bool = False) -> QPushButton:
        b = QPushButton(text)
        if variant:
            b.setObjectName(variant)
        b.setCursor(Qt.PointingHandCursor)
        b.setDefault(default)
        b.setMinimumWidth(88)
        self._button_bar().addWidget(b)
        return b


# ─────────────────────────────────────────────────────────────
# 关于
# ─────────────────────────────────────────────────────────────


ABOUT_TEXT = f"""<p style="margin:0 0 10px 0">
<b style="font-size:15px">{cfg.APP_NAME_ZH}</b><br>
<span style="color:#5A6172">版本 {cfg.APP_VERSION}</span>
</p>
<p style="margin:0 0 10px 0">
把你的手写字体，铺到表格的每一个格子里。
</p>
<p style="margin:0 0 10px 0">
当前支持的字符共 <b>{len(CHARSET_V1)}</b> 个：<br>
<span style="font-size:15px;letter-spacing:2px">
{" ".join(CHARSET_V1)}
</span>
</p>
<p style="margin:0;color:#8A90A0;font-size:12px">
程序名（英文）：{cfg.APP_NAME_EN}<br>
数据目录：{cfg.data_dir()}
</p>"""


class AboutDialog(_Dialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("关于", parent, width=460)

        body = QTextBrowser()
        body.setObjectName("DialogBody")
        body.setOpenExternalLinks(False)
        body.setHtml(ABOUT_TEXT)
        body.setFrameShape(QTextBrowser.NoFrame)
        self.root.addWidget(body, 1)

        btn = self.add_button("关闭", "Primary", default=True)
        btn.clicked.connect(self.accept)


# ─────────────────────────────────────────────────────────────
# 帮助
# ─────────────────────────────────────────────────────────────


HELP_TEXT = """<h3 style="margin:0 0 8px 0">三步走完整个流程</h3>

<p style="margin:0 0 4px 0"><b>第一步 · 新建字形库</b></p>
<p style="margin:0 0 12px 0;color:#5A6172">
拿一张白纸，工工整整写下下面这些字符，每个字多写几个样子（建议 3~5 个），
用手机拍下来。然后在程序里依次做：抠图 → 框字 → 标注。
</p>

<p style="margin:0 0 4px 0"><b>第二步 · 打开表格</b></p>
<p style="margin:0 0 12px 0;color:#5A6172">
把要填的空白表格（PDF 或图片）拖进来，
在预览图上拖出网格，框住要填字的那些格子。
</p>

<p style="margin:0 0 4px 0"><b>第三步 · 填内容、导出</b></p>
<p style="margin:0 0 12px 0;color:#5A6172">
可以一个个手输，也可以让程序按规则随机生成（比如一串随机数字、随机金额），
看着满意了就导出成 PDF。
</p>

<h3 style="margin:14px 0 8px 0">为什么要多写几个样子？</h3>
<p style="margin:0 0 12px 0;color:#5A6172">
同一个字写好几个不同样子，程序才能让相邻的格子长得不一样。
每个格子还会自动做一点点歪斜、大小、位置的随机变化，
看上去就像真的一笔一划写出来的。
</p>

<h3 style="margin:14px 0 8px 0">小技巧</h3>
<ul style="margin:0;color:#5A6172">
<li>写字用黑色水笔，纸要白、要平，光线均匀不反光。</li>
<li>字与字之间留出空隙，别连笔，方便程序自动框选。</li>
<li>同一套字形库可以反复用，以后填任何表都不用再采集。</li>
<li>做好的项目可以存成 .hwpj 文件，下次打开接着改。</li>
</ul>"""


class HelpDialog(_Dialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("帮助", parent, width=560)
        self.resize(560, 520)

        body = QTextBrowser()
        body.setObjectName("DialogBody")
        body.setHtml(HELP_TEXT)
        body.setFrameShape(QTextBrowser.NoFrame)
        self.root.addWidget(body, 1)

        btn = self.add_button("知道了", "Primary", default=True)
        btn.clicked.connect(self.accept)


# ─────────────────────────────────────────────────────────────
# 通用确认
# ─────────────────────────────────────────────────────────────


class ConfirmDialog(_Dialog):
    """危险操作确认。默认焦点在「取消」上 —— 防误按回车。"""

    def __init__(
        self,
        title: str,
        message: str,
        parent: QWidget | None = None,
        ok_text: str = "确定",
        cancel_text: str = "取消",
        danger: bool = False,
        extra: str = "",
    ):
        super().__init__(title, parent, width=420)

        msg = QLabel(message)
        msg.setObjectName("DialogMessage")
        msg.setWordWrap(True)
        msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.root.addWidget(msg, 1)

        if extra:
            sub = QLabel(extra)
            sub.setObjectName("Hint")
            sub.setWordWrap(True)
            self.root.addWidget(sub)

        ok = self.add_button(ok_text, "Danger" if danger else "Primary")
        ok.clicked.connect(self.accept)

        cancel = self.add_button(cancel_text, "Ghost", default=True)
        cancel.clicked.connect(self.reject)

    @staticmethod
    def ask(
        parent: QWidget | None,
        title: str,
        message: str,
        ok_text: str = "确定",
        cancel_text: str = "取消",
        danger: bool = False,
        extra: str = "",
    ) -> bool:
        """一句话弹确认框，返回 True 表示用户点了确定。"""
        dlg = ConfirmDialog(
            title, message, parent,
            ok_text=ok_text, cancel_text=cancel_text, danger=danger, extra=extra,
        )
        return dlg.exec() == QDialog.Accepted


# ─────────────────────────────────────────────────────────────
# 自检
# ─────────────────────────────────────────────────────────────


def _selftest() -> int:
    """开发期自检：所有对话框文案都不该是纯英文。

    用法：python -m handwrite.ui.dialogs
    （不起 Qt 窗口，只检查文案字符串）
    """
    from .app import assert_no_english_ui_text

    samples = [
        ("关于标题", "关于"),
        ("帮助标题", "帮助"),
        ("确认-确定", "确定"),
        ("确认-取消", "取消"),
        ("关闭", "关闭"),
    ]
    bad = []
    for name, text in samples:
        try:
            assert_no_english_ui_text(text)
        except AssertionError as e:
            bad.append(f"{name}: {e}")
    if bad:
        print("自检失败：", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        return 1
    print("对话框文案自检通过（%d 项）" % len(samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
