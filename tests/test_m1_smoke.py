"""M1 冒烟测试：三个窗口能建、能切、文案是中文。

★ 用离屏平台（offscreen）跑，不需要真显示器。
  CI 或远程会话里也能跑。

之所以不直接测「点按钮」而是测「信号通不通」：
    Qt 的 mouseClick 在 offscreen 下不模拟事件循环，
    测信号连接关系更能反映实际装配正确性。
"""

from __future__ import annotations

import os

import pytest

# 必须在导入 PySide6 之前设好
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="没装 PySide6，跳过界面测试")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


# ─────────────────────────────────────────────────────────────
# 各窗口能否构造
# ─────────────────────────────────────────────────────────────


def test_home_page_builds(qapp):
    from handwrite.ui.home import HomePage

    page = HomePage()
    assert page.card_new is not None
    assert page.card_my is not None
    assert page.card_open is not None


def test_wizard_builds(qapp):
    """M4 起向导是真界面：三步齐全，能前进后退。"""
    from handwrite.ui.wizard import WIZARD_STEPS, WizardWindow

    w = WizardWindow()
    assert len(w.stepbar._items) == 3
    assert len(WIZARD_STEPS) == 3
    assert w.step() == 1
    w.next_step()
    assert w.step() == 2
    w.next_step()
    assert w.step() == 3
    w.prev_step()
    assert w.step() == 2


def test_studio_builds(qapp):
    """M4 起工作台可用：工具条全亮、五组参数就位。"""
    from handwrite.ui.studio import StudioWindow

    w = StudioWindow()
    assert w.btn_back.isEnabled()
    for b in (w.btn_new, w.btn_open, w.btn_save, w.btn_export, w.font_pick):
        assert b.isEnabled(), b.text()
    # 五组参数面板
    for attr in ("grp_grid", "grp_size", "grp_cols", "grp_content", "grp_jitter"):
        assert hasattr(w.sidebar, attr), attr


def test_dialogs_build(qapp):
    from handwrite.ui.dialogs import AboutDialog, ConfirmDialog, HelpDialog

    AboutDialog()
    HelpDialog()
    ConfirmDialog("标题", "内容", ok_text="确定", cancel_text="取消")


# ─────────────────────────────────────────────────────────────
# 控制器：页面切换
# ─────────────────────────────────────────────────────────────


class _RecordingDialogs:
    """记录弹窗请求的替身 —— 绝不阻塞。

    ★ 为什么必须替换：
      真对话框走 exec()，会一直等用户点击。无界面环境下没人能点，
      进程就永远卡在那儿（表现为测试被强杀）。
    """

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.asks: list[tuple[str, str, str]] = []
        self.notifies: list[tuple[str, str, str]] = []

    def ask(self, title, message, ok_text, cancel_text, extra) -> bool:
        self.asks.append((title, message, extra))
        return self.answer

    def notify(self, title, message, extra="") -> None:
        self.notifies.append((title, message, extra))


@pytest.fixture()
def controller(qapp, tmp_path, monkeypatch):
    """在隔离的数据目录里建一个控制器实例（弹窗已替换为不阻塞替身）。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from handwrite.core import config as cfg

    cfg.ensure_dirs()
    conf = cfg.AppConfig.load()

    from handwrite.main import MainController

    spy = _RecordingDialogs()
    ctl = MainController(qapp, conf, ask=spy.ask, notify=spy.notify)
    ctl.spy = spy                     # 测试里读它断言弹了什么
    return ctl


def test_controller_starts_on_home(controller):
    assert controller.stack.currentWidget() is controller.page_home


def test_new_font_goes_to_wizard(controller):
    controller.home.new_font_requested.emit()
    assert controller.stack.currentWidget() is controller.page_wizard


def test_wizard_back_returns_home(controller):
    controller.open_wizard()
    controller.wizard.back_home.emit()
    assert controller.stack.currentWidget() is controller.page_home


def test_use_font_goes_to_studio(controller):
    controller.home.use_font_requested.emit()
    assert controller.stack.currentWidget() is controller.page_studio


def test_studio_back_returns_home(controller):
    controller.open_studio()
    controller.studio.back_home.emit()
    assert controller.stack.currentWidget() is controller.page_home


def test_recent_missing_file_removed(controller, tmp_path):
    """最近项目指向不存在的文件时，点了应把它从列表里摘掉，并告知用户。"""
    ghost = str(tmp_path / "不存在.hwpj")
    controller.config.recent_projects = [ghost]
    controller.open_project_path(ghost)

    assert ghost not in controller.config.recent_projects
    assert controller.spy.notifies, "应该弹过一次提示"
    title, _msg, extra = controller.spy.notifies[0]
    assert "找不到" in title
    assert ghost in extra, "提示里应带上具体路径"


def test_existing_recent_file_not_removed(controller, tmp_path):
    """文件还在时，不该被误删出最近列表（反向用例）。"""
    real = tmp_path / "在的.hwpj"
    real.write_text("{}", encoding="utf-8")
    controller.config.recent_projects = [str(real)]

    controller.open_project_path(str(real))

    assert str(real) in controller.config.recent_projects


# ─────────────────────────────────────────────────────────────
# 文案：界面上不该出现成片的英文
# ─────────────────────────────────────────────────────────────

#: 允许出现在界面上的纯英文（品牌名、路径、版本号）
_ALLOWED_ENGLISH = {
    "OK", "PDF", "PNG", "JPG", "DPI", "GPU",
    "HandWriteStudio", "hwpj",
}


def _visible_texts(widget) -> list[str]:
    from PySide6.QtWidgets import QLabel, QAbstractButton

    out = []
    for lb in widget.findChildren(QLabel):
        t = lb.text().strip()
        if t:
            out.append(t)
    for b in widget.findChildren(QAbstractButton):
        t = b.text().strip()
        if t:
            out.append(t)
    return out


def _is_english_sentence(text: str) -> bool:
    """整句英文（含 3 个以上连续拉丁字母、且一个汉字都没有）才算违规。"""
    import re

    if re.search(r"[\u4e00-\u9fff]", text):
        return False
    if not re.search(r"[A-Za-z]{3,}", text):
        return False
    # 去掉允许项再判断
    residue = text
    for ok in _ALLOWED_ENGLISH:
        residue = residue.replace(ok, "")
    return bool(re.search(r"[A-Za-z]{3,}", residue))


@pytest.mark.parametrize("which", ["home", "wizard", "studio"])
def test_no_english_ui_text(qapp, which):
    from handwrite.ui.home import HomePage
    from handwrite.ui.studio import StudioWindow
    from handwrite.ui.wizard import WizardWindow

    widget = {"home": HomePage, "wizard": WizardWindow, "studio": StudioWindow}[which]()

    bad = [t for t in _visible_texts(widget) if _is_english_sentence(t)]
    assert not bad, f"{which} 界面出现英文文案：{bad}"
