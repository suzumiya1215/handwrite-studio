"""QApplication 装配、中文字体、全局异常兜底。

设计依据：
- 模块设计说明书 §4.11（app.py + main.py）
- 需求文档 §8 非功能需求（界面不出现英文报错；不能白屏退出）

★ 这是**唯一**允许 import PySide6 的地方之一（另一个是 ui/ 下的其它文件）。
"""

from __future__ import annotations

import sys
import traceback
from typing import Callable

from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from ..core import config as cfg
from ..core.errors import AppError, has_chinese


# ─────────────────────────────────────────────────────────────
# 中文字体
# ─────────────────────────────────────────────────────────────

#: 候选中文字体（按优先级）。前几个是 Windows 常见的，最后是跨平台兜底
CJK_FONT_CANDIDATES = [
    "微软雅黑", "Microsoft YaHei UI", "Microsoft YaHei",
    "思源黑体", "Source Han Sans SC", "Noto Sans CJK SC",
    "等线", "DengXian", "黑体", "SimHei",
    "PingFang SC", "Hiragino Sans GB",
]


def pick_cjk_font(preferred: str = "") -> str:
    """挑一个系统里真实存在的中文字体名。

    找不到任何候选时返回空串（Qt 会用默认字体，可能显示为方框 ——
    但那也比硬指定一个不存在的字体名好，后者会让所有字都变方框）。
    """
    families = set(QFontDatabase.families())
    if preferred and preferred in families:
        return preferred
    for name in CJK_FONT_CANDIDATES:
        if name in families:
            return name
    return ""


def apply_app_font(app: QApplication, preferred: str = "", size: int = 13) -> str:
    """设置全局字体。返回实际使用的字体名。"""
    name = pick_cjk_font(preferred)
    if name:
        f = QFont(name, size)
    else:
        f = QFont()
        f.setPointSize(size)
    app.setFont(f)
    return name


# ─────────────────────────────────────────────────────────────
# 全局异常兜底
# ─────────────────────────────────────────────────────────────


class CrashGuard:
    """未捕获异常的兜底 —— 非技术用户最怕程序白屏退出。

    策略：任何异常都写日志 + 弹中文提示，**不让程序静默死掉**。
    """

    def __init__(self, app: QApplication, log_fn: Callable[[str], None] | None = None):
        self.app = app
        self.log_fn = log_fn or _default_log
        self._shown_once: set[str] = set()

    def install(self) -> None:
        sys.excepthook = self._hook

    def _hook(self, exc_type, exc_value, exc_tb) -> None:
        # Ctrl+C 不当作崩溃
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        self.log_fn(f"[未捕获异常]\n{detail}")

        if isinstance(exc_value, AppError):
            title = "出错了"
            message = exc_value.user_message
            if exc_value.hint:
                message += f"\n\n{exc_value.hint}"
        else:
            title = "出现了意外错误"
            message = (
                "程序遇到了一个没有预料到的问题。\n\n"
                f"错误类型：{exc_type.__name__}\n"
                f"错误信息：{exc_value}\n\n"
                "你的数据没有丢失。可以把日志文件发给开发者协助排查。"
            )

        # 同一条消息只弹一次，避免密集异常把用户淹没
        key = f"{exc_type.__name__}:{exc_value}"
        if key in self._shown_once:
            return
        self._shown_once.add(key)

        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(title)
            box.setText(message)
            box.setStandardButtons(QMessageBox.Ok)
            box.button(QMessageBox.Ok).setText("知道了")
            box.exec()
        except Exception:                       # 弹窗本身失败也不能再抛
            pass


def _default_log(text: str) -> None:
    try:
        from ..core.logging_setup import get_logger
        get_logger(__name__).error(text)
    except Exception:
        print(text, file=sys.stderr)


# ─────────────────────────────────────────────────────────────
# 创建应用
# ─────────────────────────────────────────────────────────────


def create_app(argv: list[str] | None = None) -> tuple[QApplication, str]:
    """创建并配置 QApplication。

    Returns:
        (app, 实际使用的中文字体名)
    """
    QCoreApplication.setApplicationName(cfg.APP_NAME_ZH)
    QCoreApplication.setApplicationVersion(cfg.APP_VERSION)
    QCoreApplication.setOrganizationName(cfg.APP_ORG)

    # 高 DPI 支持（Qt6 默认开启缩放，这里只设舍入策略）
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    app = QApplication(argv if argv is not None else sys.argv)

    font_name = apply_app_font(app)

    # 图标（没有也不影响运行）
    try:
        icon_path = cfg.resource_path("icons", "app.ico")
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
    except Exception:
        pass

    return app, font_name


def load_stylesheet(app: QApplication) -> bool:
    """载入 style.qss。失败不阻断（只是没那么好看）。"""
    try:
        qss = cfg.resource_path("style.qss")
        if qss.exists():
            app.setStyleSheet(qss.read_text(encoding="utf-8"))
            return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────
# 自检
# ─────────────────────────────────────────────────────────────


def assert_no_english_ui_text(text: str) -> None:
    """开发期自检：界面文案不该是纯英文（需求文档 §8 的硬要求）。

    允许英文出现在：括号里的快捷键、版本号、路径、品牌名。
    这里只做「整句无中文」的粗筛。
    """
    if not text.strip():
        return
    if has_chinese(text):
        return
    # 纯符号/数字不算
    if not any(ch.isalpha() for ch in text):
        return
    # 已知豁免
    for ok in ("OK", "PDF", "PNG", "JPG", "DPI", "GPU", "Windows",
               "HandWriteStudio", "http", "v1.0", "API"):
        if ok in text:
            return
    raise AssertionError(f"疑似英文界面文案：{text!r}")
