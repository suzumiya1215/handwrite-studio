"""首页 —— 三张卡片引导用户走三条路。

设计依据：
- 界面原型.html 屏 1（home-hero / home-cards / home-foot）
- 需求文档 §7 界面结构
- 模块设计说明书 §4.11

三条路：
    ① 新建字形库  → CaptureWizard（采集向导）
    ② 我的字形库  → StudioWindow（直接开工作台，用已有字形库）
    ③ 打开项目    → StudioWindow（载入 .hwpj）

本模块只负责「呈现 + 发信号」，不直接创建窗口、不碰文件系统 ——
窗口切换由 main.py 的 MainController 统一调度（依赖方向：ui → core，
窗口之间不互相 import）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import config as cfg
from ..core.models import RecentEntry


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────


def _label(text: str, role: str = "", parent: QWidget | None = None) -> QLabel:
    """建一个 QLabel 并挂上 objectName（供 style.qss 选配色）。"""
    lb = QLabel(text, parent)
    if role:
        lb.setObjectName(role)
    lb.setWordWrap(True)
    return lb


def _button(text: str, variant: str = "", parent: QWidget | None = None) -> QPushButton:
    """建按钮。variant: "primary" | "ghost" | ""（普通）。"""
    btn = QPushButton(text, parent)
    if variant == "primary":
        btn.setObjectName("Primary")
        btn.setCursor(Qt.PointingHandCursor)
    elif variant == "ghost":
        btn.setObjectName("Ghost")
        btn.setCursor(Qt.PointingHandCursor)
    else:
        btn.setCursor(Qt.PointingHandCursor)
    return btn


def _step_badge(number: str, color_kind: str = "primary") -> QLabel:
    """卡片左上角的圆形序号。"""
    badge = QLabel(number)
    badge.setFixedSize(26, 26)
    badge.setAlignment(Qt.AlignCenter)
    badge.setObjectName("StepBadgeOk" if color_kind == "ok" else "StepBadge")
    return badge


# ─────────────────────────────────────────────────────────────
# 卡片 ① 新建字形库
# ─────────────────────────────────────────────────────────────


class NewFontCard(QFrame):
    """卡片①：新建字形库。首次使用时视觉最突出（hero 卡）。"""

    capture_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("role", "hero")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        lay.addWidget(_step_badge("1"), 0, Qt.AlignLeft)

        title = _label("新建字形库")
        title.setObjectName("CardTitle")
        lay.addWidget(title)

        desc = _label(
            "拍几张自己写的字，做成你的专属字形。"
            "全程只需三步：抠图 → 框字 → 标注。"
        )
        desc.setObjectName("CardDesc")
        lay.addWidget(desc)

        lay.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_start = _button("开始采集", "primary")
        self.btn_start.setMinimumHeight(36)
        self.btn_start.clicked.connect(self.capture_requested.emit)
        row.addWidget(self.btn_start)

        tag = _label("约 10 分钟")
        tag.setObjectName("Tag")
        row.addWidget(tag, 0, Qt.AlignVCenter)
        row.addStretch(1)
        lay.addLayout(row)


# ─────────────────────────────────────────────────────────────
# 卡片 ② 我的字形库
# ─────────────────────────────────────────────────────────────


class MyFontCard(QFrame):
    """卡片②：我的字形库。已有字形库时高亮。"""

    use_requested = Signal()
    manage_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        lay.addWidget(_step_badge("2", "ok"), 0, Qt.AlignLeft)

        title = _label("我的字形库")
        title.setObjectName("CardTitle")
        lay.addWidget(title)

        desc = _label("选择一套已采集好的字迹，开始填表。")
        desc.setObjectName("CardDesc")
        lay.addWidget(desc)

        # ── 字形库缩略（字帖预览 + 元信息）──
        self.preview_box = QFrame()
        self.preview_box.setObjectName("FontMini")
        pl = QHBoxLayout(self.preview_box)
        pl.setContentsMargins(10, 8, 10, 8)
        pl.setSpacing(10)

        self.thumb = QLabel("0 1 2\n3 4 5")
        self.thumb.setObjectName("Thumb")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setFixedSize(64, 48)
        pl.addWidget(self.thumb)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(2)
        self.font_name_lb = _label("还没有字形库", "FontName")
        self.font_sub_lb = _label("先点左边的「开始采集」", "FontSub")
        meta_col.addWidget(self.font_name_lb)
        meta_col.addWidget(self.font_sub_lb)
        pl.addLayout(meta_col, 1)
        lay.addWidget(self.preview_box)

        lay.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_use = _button("选择并使用", "primary")
        self.btn_use.clicked.connect(self.use_requested.emit)
        self.btn_manage = _button("管理", "ghost")
        self.btn_manage.clicked.connect(self.manage_requested.emit)
        row.addWidget(self.btn_use)
        row.addWidget(self.btn_manage)
        row.addStretch(1)
        lay.addLayout(row)

        self.set_font_summary(None)

    def set_font_summary(self, font) -> None:
        """显示当前字形库摘要。font 为 FontSummary 或 None。

        无字形库时按钮置灰 —— 让用户一眼看出「这条路现在走不通」。
        """
        if font is None or getattr(font, "sample_count", 0) <= 0:
            self.font_name_lb.setText("还没有字形库")
            self.font_sub_lb.setText("先点左边的「开始采集」")
            self.thumb.setText("—")
            self.btn_use.setEnabled(False)
            self.btn_manage.setEnabled(False)
            self._restyle()
            return

        self.font_name_lb.setText(getattr(font, "name", "") or "我的字迹")

        coverage = getattr(font, "coverage", "")
        updated = getattr(font, "updated_at", "")
        bits = []
        if coverage:
            bits.append("已采集 " + coverage)
        if updated:
            bits.append(updated)
        self.font_sub_lb.setText(" · ".join(bits) or "可以开始填表")

        # 缩略图用「覆盖字符数」示意；M2 起可从 FontLibraryService 取真实样张
        char_count = getattr(font, "char_count", 0)
        self.thumb.setText("%d 个字符" % char_count if char_count else "字迹")

        self.btn_use.setEnabled(True)
        self.btn_manage.setEnabled(True)
        self._restyle()

    def _restyle(self) -> None:
        """property 变化后让 QSS 重新套用。"""
        self.style().unpolish(self)
        self.style().polish(self)


# ─────────────────────────────────────────────────────────────
# 卡片 ③ 打开项目
# ─────────────────────────────────────────────────────────────


class RecentRow(QFrame):
    """最近项目里的一行。悬停高亮、双击打开。"""

    activated = Signal(str)          # 传 path

    def __init__(self, entry: RecentEntry, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RecentRow")
        self.entry = entry
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(8)

        name = _label(entry.label, "RecentName")
        if not entry.exists:
            name.setObjectName("RecentNameMissing")
            name.setToolTip("文件已不在原位置：" + str(entry.path))
        lay.addWidget(name, 1)

        when = _label(entry.updated_at or "", "RecentTime")
        lay.addWidget(when, 0, Qt.AlignRight)

    def mouseDoubleClickEvent(self, event) -> None:      # noqa: N802 (Qt 命名)
        self.activated.emit(str(self.entry.path))
        super().mouseDoubleClickEvent(event)


class OpenProjectCard(QFrame):
    """卡片③：打开项目。列出最近项目。"""

    open_requested = Signal()
    recent_requested = Signal(str)   # 传 path

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        lay.addWidget(_step_badge("3"), 0, Qt.AlignLeft)

        title = _label("打开项目")
        title.setObjectName("CardTitle")
        lay.addWidget(title)

        desc = _label("继续上次没做完的表格填写。")
        desc.setObjectName("CardDesc")
        lay.addWidget(desc)

        # 最近项目列表（用滚动区兜住，条目多了不会把卡片撑破）
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(2)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("RecentScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(72)
        self._scroll.setWidget(self._list_host)
        lay.addWidget(self._scroll, 1)

        self.btn_open = _button("打开 .hwpj 文件")
        self.btn_open.setMinimumHeight(34)
        self.btn_open.clicked.connect(self.open_requested.emit)
        lay.addWidget(self.btn_open)

        self.set_recent([])

    def set_recent(self, entries: list[RecentEntry]) -> None:
        """重建最近项目列表。entries 为空时显示一句占位提示。"""
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        if not entries:
            empty = _label("还没有打开过项目", "RecentEmpty")
            empty.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._list_lay.addWidget(empty)
            self._list_lay.addStretch(1)
            return

        for entry in entries[:8]:                # 最多显示 8 条
            row = RecentRow(entry)
            row.activated.connect(self.recent_requested.emit)
            self._list_lay.addWidget(row)
        self._list_lay.addStretch(1)


# ─────────────────────────────────────────────────────────────
# 首页整页
# ─────────────────────────────────────────────────────────────


class HomePage(QWidget):
    """首页。发射 4 个语义信号，由 MainController 决定去哪。"""

    new_font_requested = Signal()
    use_font_requested = Signal()
    manage_font_requested = Signal()
    open_project_requested = Signal()
    open_recent_requested = Signal(str)
    help_requested = Signal()
    about_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("HomePage")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 顶部 hero ──
        hero = QWidget()
        hero.setObjectName("HomeHero")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(28, 26, 28, 20)
        hl.setSpacing(6)

        h1 = _label("手 写 字 迹 填 充 器")
        h1.setObjectName("HomeTitle")
        h1.setAlignment(Qt.AlignCenter)
        hl.addWidget(h1)

        h2 = _label("把表格变成你自己的字迹")
        h2.setObjectName("HomeSub")
        h2.setAlignment(Qt.AlignCenter)
        hl.addWidget(h2)

        root.addWidget(hero)

        # ── 三卡片区 ──
        cards_host = QWidget()
        cards_host.setObjectName("HomeCards")
        cl = QHBoxLayout(cards_host)
        cl.setContentsMargins(24, 8, 24, 12)
        cl.setSpacing(16)

        self.card_new = NewFontCard()
        self.card_my = MyFontCard()
        self.card_open = OpenProjectCard()
        for c in (self.card_new, self.card_my, self.card_open):
            c.setMinimumWidth(240)
            cl.addWidget(c, 1)
        root.addWidget(cards_host, 1)

        # ── 连接信号 ──
        self.card_new.capture_requested.connect(self.new_font_requested.emit)
        self.card_my.use_requested.connect(self.use_font_requested.emit)
        self.card_my.manage_requested.connect(self.manage_font_requested.emit)
        self.card_open.open_requested.connect(self.open_project_requested.emit)
        self.card_open.recent_requested.connect(self.open_recent_requested.emit)

        # ── 底部 ──
        foot = QWidget()
        foot.setObjectName("HomeFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 8, 24, 12)
        fl.setSpacing(16)

        self.link_help = _button("帮助", "ghost")
        self.link_help.setObjectName("FootLink")
        self.link_help.clicked.connect(self.help_requested.emit)
        fl.addWidget(self.link_help)

        self.link_about = _button("关于", "ghost")
        self.link_about.setObjectName("FootLink")
        self.link_about.clicked.connect(self.about_requested.emit)
        fl.addWidget(self.link_about)

        fl.addStretch(1)

        ver = _label("v" + cfg.APP_VERSION_SHORT, "FootVersion")
        fl.addWidget(ver, 0, Qt.AlignVCenter)

        root.addWidget(foot)

    # ── 对外刷新接口 ──────────────────────────────────────────

    def refresh(self, font_summary=None, recent: list[RecentEntry] | None = None) -> None:
        """按当前数据刷新首页。main.py 在显示首页前调用。"""
        self.card_my.set_font_summary(font_summary)
        self.card_open.set_recent(recent or [])
