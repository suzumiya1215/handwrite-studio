"""程序入口 + 窗口调度 + 业务编排。

设计依据（模块设计说明书 §4.11、L0 装配层）：
    日志 → 配置 → 数据目录 → QApplication → 主窗口 → 事件循环

★ 这是唯一把「界面」和「内核」装配到一起的地方。
  ui/ 只管呈现与发信号；core/ 完全不知道 ui 的存在。
  中间的翻译工作由这里的 MainController 和
  core/services/session_service.py 分担：
      MainController   —— 窗口切换、弹窗、把用户意图串成流程
      DocumentSession —— 「当前这份文档」的网格/内容/抖动/导出计算

用法：
    开发：  python -m handwrite.main
    打包：  HandWriteStudio.exe（PyInstaller 入口指向本文件）
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal

from .core import config as cfg
from .core import logging_setup as logsetup
from .core.errors import AppError, ExportCancelledError
from .core.models import RecentEntry
from .core.services.session_service import DocumentSession

log = logsetup.get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# 导出工作线程
# ─────────────────────────────────────────────────────────────


class _ExportWorker(QThread):
    """在后台跑导出，避免大文件导出时界面卡死。

    进度与完成都通过信号回到主线程（Qt 的信号是线程安全的）。
    cancel 用 threading.Event：ProgressDialog 一点取消就 set，
    内核的 _check_cancel 轮询到就抛 ExportCancelledError。
    """

    progress = Signal(int, int)        # (done, total)
    done = Signal(object, str)         # (paths_or_None, error_str)

    def __init__(self, session: DocumentSession, settings: dict,
                 current_page: int, cancel, parent=None):
        super().__init__(parent)
        self._session = session
        self._settings = settings
        self._current = current_page
        self._cancel = cancel

    def run(self) -> None:            # noqa: N802
        try:
            paths = self._session.export(
                kind=self._settings["kind"],
                out=self._settings["out"],
                dpi=self._settings["dpi"],
                pages=self._settings["pages"],
                current=self._current,
                on_progress=lambda d, t: self.progress.emit(d, t),
                cancel_flag=self._cancel,
            )
            self.done.emit(paths, "")
        except ExportCancelledError:
            self.done.emit(None, "")
        except AppError as e:
            self.done.emit(None, e.user_message)
        except Exception as e:         # noqa: BLE001
            log.exception("导出线程异常")
            self.done.emit(None, f"导出出了点问题：{e}")


# ─────────────────────────────────────────────────────────────
# 主控制器
# ─────────────────────────────────────────────────────────────


class MainController:
    """把首页 / 采集向导 / 主工作台装进一个 QStackedWidget 里切换。

    ★ 窗口之间不互相 import —— 谁要跳转就发信号，由这里统一接。
    ★ 弹窗通过 `ask` / `notify` 两个回调注入：
      无界面（测试）环境下不会卡在模态框上。
    ★ 文件选择也走 `pick_file` / `pick_dir` / `pick_save` 可替换回调。
    """

    def __init__(
        self,
        app,
        config: cfg.AppConfig,
        font_name: str = "",
        *,
        ask=None,
        notify=None,
        pick_file=None,
        pick_dir=None,
        pick_save=None,
    ):
        from PySide6.QtWidgets import QMainWindow, QStackedWidget, QWidget

        from .ui import dialogs
        from .ui.font_manage_dialog import FontManageDialog
        from .ui.home import HomePage
        from .ui.studio import StudioWindow
        from .ui.wizard import WizardWindow

        self.app = app
        self.config = config
        self.font_name = font_name

        # ── 弹窗回调（默认真对话框；测试可替换）──
        self._ask = ask if ask is not None else self._default_ask
        self._notify = notify if notify is not None else self._default_notify
        self._dialogs = dialogs
        self._FontManageDialog = FontManageDialog

        # ── 文件选择回调 ──
        self._pick_file = pick_file if pick_file is not None else self._default_pick_file
        self._pick_dir = pick_dir if pick_dir is not None else self._default_pick_dir
        self._pick_save = pick_save if pick_save is not None else self._default_pick_save

        # ── 文档会话（业务内核）──
        self.session = DocumentSession()
        self._project_path: str | None = None

        # 采集向导的中间状态
        self._cap_source = None          # 原图 PIL
        self._cap_processed = None       # 抠图后的图

        # ── 外壳 ──
        self.window = QMainWindow()
        self.window.setObjectName("MainWindow")
        self.window.setWindowTitle(cfg.APP_NAME_ZH)
        self.window.setMinimumSize(960, 640)

        st = config.window
        self.window.resize(max(960, st.width), max(640, st.height))
        if st.x >= 0 and st.y >= 0:
            self.window.move(st.x, st.y)
        if st.maximized:
            self.window.setWindowState(self.window.windowState() | Qt.WindowMaximized)

        self.stack = QStackedWidget()
        self.stack.setObjectName("MainStack")
        self.window.setCentralWidget(self.stack)

        # ── 三个页面 ──
        self.home = HomePage()
        self.wizard = WizardWindow()
        self.studio = StudioWindow()

        self.page_home = self._wrap(self.home)
        self.page_wizard = self._wrap(self.wizard)
        self.page_studio = self._wrap(self.studio)

        for p in (self.page_home, self.page_wizard, self.page_studio):
            self.stack.addWidget(p)

        self._wire()
        self._restore_last_font()
        self.show_home()

        # ── 自动保存定时器（M6）──
        if config.autosave_enabled:
            from PySide6.QtCore import QTimer

            self._autosave_timer = QTimer(self.window)
            self._autosave_timer.setInterval(max(30, config.autosave_interval_sec) * 1000)
            self._autosave_timer.timeout.connect(self._autosave)
            self._autosave_timer.start()

    # ── 装配 ──────────────────────────────────────────────────

    def _wrap(self, widget) -> "QWidget":                # noqa: F821
        """把页面套一层（将来放顶部刘海留白 / 安全区用）。"""
        return widget

    def _wire(self) -> None:
        # 首页 → 各页
        self.home.new_font_requested.connect(self.open_wizard)
        self.home.use_font_requested.connect(lambda: self.open_studio())
        self.home.manage_font_requested.connect(self.open_font_manage)
        self.home.open_project_requested.connect(self.open_project_dialog)
        self.home.open_recent_requested.connect(self.open_project_path)
        self.home.help_requested.connect(self.show_help)
        self.home.about_requested.connect(self.show_about)

        # 各页 → 返回首页
        self.wizard.back_home.connect(self.show_home)
        self.studio.back_home.connect(self.show_home)
        self.studio.open_font_manage.connect(self.open_font_manage)

        # ── 工作台 ──
        st = self.studio
        st.request_new.connect(self.new_project)
        st.request_open.connect(self.open_project_dialog)
        st.request_save.connect(self.save_project)
        st.request_export.connect(self.export_project)
        st.request_help.connect(self.show_help)
        st.request_fill.connect(self.refresh_preview)
        st.request_open_manual.connect(self.open_manual_input)
        st.preview.apply_all_changed.connect(self._on_apply_all)
        st.preview.page_changed.connect(self._on_page_changed)

        # ── 采集向导 ──
        wz = self.wizard
        wz.source_picked.connect(self.capture_load_source)
        wz.request_preview.connect(self.capture_update_preview)
        wz.request_auto_boxes.connect(self.capture_auto_boxes)
        wz.finish_requested.connect(self.capture_finish)

        # 退出时保存窗口状态
        self.app.aboutToQuit.connect(self.save_window_state)

    # ── 页面切换 ──────────────────────────────────────────────

    def show_home(self) -> None:
        log.debug("切到首页")
        self.home.refresh(self._font_summary(), self._recent_entries())
        self.stack.setCurrentWidget(self.page_home)
        self.window.setWindowTitle(cfg.APP_NAME_ZH)

    def open_wizard(self) -> None:
        log.debug("切到采集向导")
        self._cap_source = None
        self._cap_processed = None
        self.wizard.set_step(1)
        self.stack.setCurrentWidget(self.page_wizard)
        self.window.setWindowTitle("新建字形库 · " + cfg.APP_NAME_ZH)

    def open_studio(self, project_path: str | None = None) -> None:
        log.debug("切到主工作台 project=%s", project_path)
        if project_path:
            self._load_project(project_path)
        self.studio.set_font_name(self._current_font_name())
        self.stack.setCurrentWidget(self.page_studio)
        self.window.setWindowTitle(cfg.APP_NAME_ZH)
        self._push_session_to_studio(keep_view=False)
        self.refresh_preview()

    # ─────────────────────────────────────────────────────────
    # 工作台：把会话状态推给界面
    # ─────────────────────────────────────────────────────────

    def _page(self) -> int:
        return self.studio.preview.page() if hasattr(self.studio, "preview") else 0

    def _push_session_to_studio(self, *, keep_view: bool = True) -> None:
        st = self.studio
        s = self.session

        if not s.has_template:
            st.set_status("还没有打开表格。点「新建」或「打开」选一个 PDF 或图片。")
            st.set_enabled_actions(save=False, export=False)
            from .ui.widgets import CanvasLayers

            st.set_layers(CanvasLayers(), keep_view=keep_view)
            return

        st.set_enabled_actions(save=True, export=True)
        st.set_font_name(s.font_name() or "未选择")
        st.set_page_meta(self._page(), s.page_count, dpi=150)
        st.set_apply_to_all(bool(s.project.apply_to_all_pages) if s.project else True)

        spec = s.grid_spec(self._page())
        st.set_grid_spec(spec)
        if s.project is not None:
            st.set_content_spec(s.project.content.spec)
            st.set_jitter_spec(s.project.jitter)
            st.set_seed(s.project.seed)

    def refresh_preview(self) -> None:
        """重算并重绘预览。参数一变就会走到这里。"""
        s = self.session
        if not s.has_template:
            return

        st = self.studio
        page = self._page()

        # 1) 把侧栏参数灌回 session
        s.apply_grid(page, **st.grid_values())
        if s.project is not None and s.project.apply_to_all_pages:
            for i in range(s.page_count):
                if i != page:
                    s.apply_grid(i, **st.grid_values())
        s.apply_content(**st.content_values())
        s.apply_jitter(st.jitter_values())
        s.apply_seed(st.seed_value())

        # 2) 网格改了 → 清缓存
        s.refresh_caches()

        # 3) 算网格线 + 格子
        try:
            lines = s.grid_lines(page)
            cells = s.cell_layers(page)
        except AppError as e:
            st.set_status(e.user_message)
            return

        # 4) 填充
        base, fill_img, result = None, None, None
        try:
            base, fill_img, result = s.fill_page(page)
        except AppError as e:
            st.set_status(e.user_message)
            base = s.page_bitmap(page)

        if base is None:
            return

        # 5) 一次性给画布三层
        from .ui.widgets import CanvasLayers

        active = s.grid_spec(page).active_cols or list(range(s.grid_spec(page).cols))
        st.set_layers(
            CanvasLayers(base=base, fill=fill_img, grid=lines, cells=cells,
                         active_cols=active),
            keep_view=True,
        )

        # 6) 状态栏统计
        stt = s.stats(page)
        bits = [
            f"{stt['rows']} 行 × {stt['cols']} 列",
            f"参与填充 {stt['active_cols']} 列",
            f"本页 {stt['cells']} 格",
            f"已填 {stt['filled']} 格 / {stt['chars']} 个字符",
        ]
        skipped = {}
        if result is not None:
            skipped = getattr(result, "skipped_chars", {}) or {}
        if skipped:
            chars = "".join(sorted(skipped))
            bits.append(f"跳过 {sum(skipped.values())} 个无字形字符（{chars}）")

        warn = s.validate_grid(page)
        if warn:
            bits.append("⚠ " + warn[0])

        if not self.session.lib:
            bits.append("⚠ 还没选字形库，先点右上角「字形库」")

        st.set_status("　·　".join(bits))

    def _on_page_changed(self, _index: int) -> None:
        self.refresh_preview()

    def _on_apply_all(self, on: bool) -> None:
        self.session.set_apply_to_all(on)
        if on:
            self.refresh_preview()

    # ─────────────────────────────────────────────────────────
    # 工作台：文件动作
    # ─────────────────────────────────────────────────────────

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        path = self._pick_file(
            "选一个表格文件（PDF 或图片）", "",
            "表格文件 (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;所有文件 (*)")
        if not path:
            return
        try:
            self.session.open_template(path)
            self._project_path = None
        except AppError as e:
            self._notify("打不开这个文件", e.user_message, e.hint or "")
            return
        except Exception as e:                    # noqa: BLE001
            log.exception("打开模板异常")
            self._notify("打不开这个文件", f"程序遇到了意外问题：{e}")
            return

        self.studio.set_font_name(self.session.font_name() or "未选择")
        self._push_session_to_studio(keep_view=False)
        self.refresh_preview()
        if self.session.warnings:
            self._notify("打开了，但有一点提醒", "；".join(self.session.warnings))

    def save_project(self) -> None:
        if not self.session.has_template or self.session.project is None:
            self._notify("还没东西可存", "先打开一张表格，填好之后再来保存。")
            return
        path = self._project_path
        if not path:
            default = str(cfg.projects_dir() / (self.session.project.name or "未命名") + ".hwpj")
            path = self._pick_save("另存为项目", default,
                                  "手写字迹项目 (*.hwpj)")
            if not path:
                return
            if not path.lower().endswith(".hwpj"):
                path += ".hwpj"
        try:
            saved = self.session.save_project(path)
            self._project_path = saved
            self.config.push_recent(saved)
            self.config.save()
            self.studio.set_status(f"已保存：{saved}")
        except AppError as e:
            self._notify("保存失败", e.user_message, e.hint or "")

    def export_project(self) -> None:
        s = self.session
        if not s.has_template:
            self._notify("还没打开表格", "先打开一张表格再导出。")
            return
        if s.lib is None:
            self._notify("还没选字形库", "导出前要先选一套字形库。点右上角「字形库」选一套。")
            return

        from .ui.export_dialog import ExportDialog, ProgressDialog, reveal_in_explorer

        default_path = s.suggest_export_path("pdf")
        dlg = ExportDialog(default_path=default_path, page_count=s.page_count,
                           default_dpi=self.config.export_dpi)
        if dlg.exec() != dlg.Accepted:
            return
        settings = dlg.result_settings()
        if not settings:
            return
        self.config.export_dpi = settings["dpi"]
        self.config.export_format = settings["kind"]
        self.config.save()

        progress = ProgressDialog("正在导出")
        cancel = threading.Event()
        progress.cancelled.connect(lambda: cancel.set())

        worker = _ExportWorker(self.session, settings, self._page(), cancel, self.window)
        worker.progress.connect(progress.set_progress)

        def _on_done(paths, error):
            if cancel.is_set():
                progress.reject()
                return
            if error:
                progress.reject()
                self._notify("导出失败", error)
                return
            progress.finish()
            if settings.get("open_folder") and paths:
                reveal_in_explorer(paths[0])
            self._notify("导出完成",
                         "共导出 %d 个文件。\n%s" % (len(paths), "\n".join(paths[:3])))

        worker.done.connect(_on_done)
        worker.start()
        progress.exec()

    def show_help(self) -> None:
        from .ui.dialogs import HelpDialog

        HelpDialog(self.window).exec()

    def show_about(self) -> None:
        from .ui.dialogs import AboutDialog

        AboutDialog(self.window).exec()

    def open_manual_input(self) -> None:
        s = self.session
        if not s.has_template:
            self._notify("还没打开表格", "先打开一张表格才能录内容。")
            return
        page = self._page()
        rows = s.grid_spec(page).rows
        cols = s.grid_spec(page).cols
        supported = list(getattr(s.lib, "characters", {}).keys()) if s.lib else None
        matrix = s.manual_matrix(page)

        from .ui.manual_input import ManualInputDialog

        dlg = ManualInputDialog(rows, cols, matrix, supported=supported,
                                parent=self.window)
        if dlg.exec() == dlg.Accepted:
            m = dlg.result_matrix()
            if m is not None:
                s.set_manual_matrix(m)
                self.studio.sidebar.set_content_values(mode="manual")
                self.refresh_preview()

    # ─────────────────────────────────────────────────────────
    # 字形库
    # ─────────────────────────────────────────────────────────

    def open_font_manage(self) -> None:
        dlg = self._FontManageDialog(self.window)
        dlg.font_picked.connect(self._on_font_picked)
        dlg.new_requested.connect(lambda: (dlg.accept(), self.open_wizard()))
        dlg.exec()

    def _on_font_picked(self, font_id: str) -> None:
        try:
            self.session.load_font(font_id)
            self.config.last_font_id = font_id
            self.config.save()
        except AppError as e:
            self._notify("字形库载入失败", e.user_message, e.hint or "")
            return
        self.studio.set_font_name(self.session.font_name())
        self.refresh_preview()

    def _font_summary(self):
        return self.session.font_summary()

    def _current_font_name(self) -> str:
        return self.session.font_name()

    def _restore_last_font(self) -> None:
        fid = self.config.last_font_id
        if not fid:
            return
        try:
            self.session.load_font(fid)
        except AppError as e:
            log.warning("上次用的字形库载入失败：%s", e.user_message)

    # ─────────────────────────────────────────────────────────
    # 项目存取
    # ─────────────────────────────────────────────────────────

    def open_project_dialog(self) -> None:
        path = self._pick_file(
            "打开项目", str(cfg.projects_dir()),
            "手写字迹项目 (*.hwpj);;所有文件 (*)")
        if path:
            self.open_project_path(path)

    def open_project_path(self, path: str) -> None:
        if not Path(path).exists():
            # 最近项目指向的文件已被移动/删除：从列表里摘掉，并提示用户
            self.config.remove_recent(path)
            self.config.save()
            self._notify("找不到这个项目", f"它可能已经被移动或删除了：\n{path}", path)
            return
        if not self._confirm_discard():
            return
        try:
            self._load_project(path)
        except AppError as e:
            self._notify("打不开这个项目", e.user_message, e.hint or "")
            return
        self.open_studio()

    def _load_project(self, path: str) -> None:
        self.session.load_project(path)
        self._project_path = str(path)
        self.config.push_recent(path)
        self.config.save()
        if self.session.warnings:
            self._notify("项目已打开，但有一点提醒", "；".join(self.session.warnings))

    def _recent_entries(self) -> list[RecentEntry]:
        try:
            return self.session.projects.list_recent()
        except Exception:                 # noqa: BLE001
            return []

    def _confirm_discard(self) -> bool:
        if self._project_path or self.session.has_template:
            return self._ask(
                "放弃当前内容？",
                "现在打开别的内容，当前没保存的改动会丢失。",
                "继续", "取消")
        return True

    # ─────────────────────────────────────────────────────────
    # 采集向导：抠图 / 框字 / 标注
    # ─────────────────────────────────────────────────────────

    def capture_load_source(self, path: str) -> None:
        from PIL import Image

        try:
            img = Image.open(path).convert("RGBA")
        except Exception as e:                # noqa: BLE001
            self._notify("图片打不开", f"读这张图片失败了：{e}")
            return
        self._cap_source = img
        self.wizard.set_source_image(img, path)
        # 用默认参数先出一张预览
        self._cap_processed = self.session.cutout(
            img, tolerance=self.config.capture_tolerance)
        self.wizard.set_preview_image(self._cap_processed)
        self.wizard.set_boxes_image(self._cap_processed)
        self.wizard.set_boxes([])

    def capture_update_preview(self) -> None:
        if self._cap_source is None:
            return
        opts = self.wizard.cutout_options()
        self._cap_processed = self.session.cutout(self._cap_source, **opts)
        self.wizard.set_preview_image(self._cap_processed)
        self.wizard.set_boxes_image(self._cap_processed)

    def capture_auto_boxes(self) -> None:
        if self._cap_processed is None:
            self._notify("还没抠图", "先选好图片、调好容差，再自动框选。")
            return
        boxes = self.session.auto_boxes(self._cap_processed)
        if not boxes:
            self._notify("没框出字来",
                         "容差可能不合适，调一下「容差」滑块再试；"
                         "也可以直接在图上拖框手动画。")
            return
        self.wizard.set_boxes(boxes, reset_undo=True)

    def capture_finish(self, name: str) -> None:
        labeled = self.wizard.labeled_boxes()
        if not labeled:
            self._notify("一个都没标", "先在第三步点一个字符，再点图里对应的字，"
                                     "至少标一个才能生成字形库。")
            return
        if self._cap_processed is None:
            self._notify("缺少原图", "请先回到第一步重新选图片。")
            return

        self.session.set_capture_image(self._cap_processed)
        try:
            lib = self.session.build_font(
                name, labeled, target_height=self.config.capture_glyph_height)
        except AppError as e:
            self._notify("生成失败", e.user_message, e.hint or "")
            return

        from .core.services import font_service as fts

        try:
            saved = fts.save_to_dir(lib, cfg.fonts_dir())
        except Exception as e:                # noqa: BLE001
            self._notify("保存字形库失败", f"{e}")
            return

        self.session.load_font(saved)
        self.config.last_font_id = lib.id
        self.config.save()
        self._notify(
            "字形库已生成",
            f"「{lib.name}」已生成，共 {len(lib.characters)} 个字符。\n"
            "现在去打开一张表格，就能用这套字迹填表了。")
        self.open_studio()

    # ─────────────────────────────────────────────────────────
    # 自动保存
    # ─────────────────────────────────────────────────────────

    def _autosave(self) -> None:
        s = self.session
        if not s.has_template or s.project is None:
            return
        try:
            s.projects.autosave(s.project)
        except Exception as e:               # noqa: BLE001
            log.warning("自动保存失败（忽略）：%s", e)

    # ─────────────────────────────────────────────────────────
    # 窗口状态 & 运行
    # ─────────────────────────────────────────────────────────

    def save_window_state(self) -> None:
        geo = self.window.geometry()
        st = cfg.WindowState(
            width=geo.width(), height=geo.height(),
            x=geo.x(), y=geo.y(),
            maximized=bool(self.window.windowState() & (1 << 1)),
        )
        self.config.window = st
        self.config.save()

    def run(self) -> int:
        self.window.show()
        return int(self.app.exec())

    # ─────────────────────────────────────────────────────────
    # 默认弹窗 / 文件选择（可被测试替换）
    # ─────────────────────────────────────────────────────────

    def _default_ask(self, title, message, ok_text="确定", cancel_text="取消",
                     extra="") -> bool:
        from .ui.dialogs import ConfirmDialog

        return ConfirmDialog.ask(
            self.window, title, message,
            ok_text=ok_text, cancel_text=cancel_text, extra=extra)

    def _default_notify(self, title, message, extra="") -> None:
        from .ui.dialogs import ConfirmDialog

        ConfirmDialog.ask(
            self.window, title, message, ok_text="知道了", cancel_text="", extra=extra)

    def _default_pick_file(self, title, start, filter_) -> str:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self.window, title, start, filter_)
        return path

    def _default_pick_dir(self, title, start) -> str:
        from PySide6.QtWidgets import QFileDialog

        return QFileDialog.getExistingDirectory(self.window, title, start)

    def _default_pick_save(self, title, default, filter_) -> str:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self.window, title, default, filter_)
        return path


# ─────────────────────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────────────────────


def bootstrap():
    """准备运行环境：日志 → 配置 → 数据目录。"""
    logsetup.init()
    log.info("启动 %s v%s", cfg.APP_NAME_ZH, cfg.APP_VERSION)
    try:
        cfg.ensure_dirs()
    except Exception as e:                # noqa: BLE001
        log.error("创建数据目录失败：%s", e)
        raise

    config = cfg.AppConfig.load()
    log.info("配置已载入：%s", cfg.config_file())
    return config


def main(argv: list[str] | None = None) -> int:
    from .ui.app import CrashGuard, create_app, load_stylesheet

    try:
        config = bootstrap()
    except Exception as e:                # noqa: BLE001
        log.exception("初始化失败")
        print(f"初始化失败：{e}", file=sys.stderr)
        return 2

    app, font_name = create_app(argv)
    load_stylesheet(app)

    guard = CrashGuard(app, log_fn=log.error)
    guard.install()

    if font_name:
        log.info("界面字体：%s", font_name)
    else:
        log.warning("没有找到系统中文字体，界面可能显示为方框")

    controller = MainController(app, config, font_name)
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())
