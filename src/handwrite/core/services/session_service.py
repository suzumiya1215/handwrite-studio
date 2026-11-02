"""文档会话（L3 服务层）—— 把「界面参数」翻译成「内核调用」，并把结果灌回界面。

设计依据：
- 模块设计说明书 §4.11（装配层职责）
- 接口契约 C3 / C4 / C5 / C13

为什么单独抽一个类而不是全塞进 MainController：

    MainController 的职责是「窗口切换 + 弹窗」。如果再把
    「建 GridSpec → 建 ContentSpec → 建 JitterSpec → 算 cells →
    生成 matrix → 填充 → 导出」这一长串也放进去，那个类会变成
    六七百行的大杂烩，谁都改不动。

    DocumentSession 专门负责「当前正在编辑的这一份文档」：
    它持有 template_doc / project / 字形库 / 各页缓存，
    对外只暴露几个动词：open_template / refresh / export / save。

★ 这个类**不 import Qt 的窗口**（只用到 QtCore 无关的东西），
  也不弹任何框 —— 出错一律抛 AppError，由控制器翻译成中文提示。
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from .. import config as cfg
from .. import geometry as geo
from .. import imageops
from ..errors import AppError
from ..logging_setup import get_logger
from ..models import Project
from ..models.content import ContentMatrix, ContentSpec, RandomSpec
from ..models.jitter import (
    CellJitter,
    CharPosJitter,
    CharShapeJitter,
    DotJitter,
    JitterSpec,
)
from ..models.project import ContentSettings, PageSettings
from . import content_service as cs
from . import export_service as ex
from . import fill_service as fs
from . import font_service as fts
from . import grid_service as gs
from . import project_service as prj
from . import template_service as ts

log = get_logger(__name__)

#: 预览用的 DPI。150 够看清字，又不至于每动一下滑块就重算几百万像素。
PREVIEW_DPI = 150

#: 抖动预设 → (倍率, 说明)。1.0 就是滑块上显示的基准值。
JITTER_PRESET_FACTORS = {
    "light": 0.5,
    "natural": 1.0,
    "bold": 1.8,
}


class DocumentSession:
    """当前正在编辑的文档。

    典型用法（控制器里）::

        s = DocumentSession()
        s.open_template("表格.pdf")       # 载入模板
        s.apply_grid(**ui.grid_values())  # 把侧栏参数灌进来
        s.refresh()                       # 重算网格 + 内容 + 填充
        img = s.page_image(0)             # 拿给画布显示
        s.export_pdf("out.pdf")
    """

    def __init__(self, data_dir: str | Path | None = None):
        self.templates = ts
        self.grid = gs
        self.content = cs
        self.fill = fs
        self.fonts = fts
        self.exporter = ex.ExportService()
        self.projects = prj.ProjectService(data_dir)

        self.doc: ts.TemplateDoc | None = None
        self.project: Project | None = None
        self.lib = None                     # FontLibrary | None
        self.lib_path: Path | None = None

        # 每页的渲染缓存（dpi → PIL.Image）
        self._page_cache: dict[tuple[int, int], Any] = {}
        # 每页的填充结果
        self._fill_cache: dict[int, Any] = {}
        self._cells_cache: dict[int, list] = {}

        self.warnings: list[str] = []

    # ─────────────────────────────────────────────────────────
    # 模板
    # ─────────────────────────────────────────────────────────

    @property
    def has_template(self) -> bool:
        return self.doc is not None

    @property
    def page_count(self) -> int:
        return ts.page_count(self.doc)

    def open_template(self, path: str | Path) -> None:
        """打开模板（PDF / 图片）。会关掉上一个。"""
        self.close_template()
        doc = ts.open_document(path, preview_dpi=PREVIEW_DPI)
        self.doc = doc
        self.warnings = list(getattr(doc, "warnings", []) or [])

        # 新建一份项目骨架，把模板尺寸带进去
        p = prj.ProjectService.new_project(
            name=Path(path).stem,
            font_id=getattr(self.lib, "id", ""),
            font_name=getattr(self.lib, "name", ""),
        )
        p.template_path = str(path)
        p.template_original_path = str(path)
        p.page_count = ts.page_count(doc)
        p.pages = [PageSettings(index=i, grid=gs.GridSpec())
                   for i in range(p.page_count)]
        self.project = p

        # 默认网格铺在页面上：按 A4 的常见表格给一套合理初值
        w_mm, h_mm = ts.suggest_page_size(doc)
        for pg in p.pages:
            pg.grid = gs.GridSpec(
                start_x_mm=round(w_mm * 0.07, 1),
                start_y_mm=round(h_mm * 0.16, 1),
                col_width_mm=15.0,
                row_height_mm=12.0,
                cell_padding_mm=1.0,
                rows=max(1, int((h_mm * 0.78) // 12.0)),
                cols=10,
            )
        log.info("打开模板 %s：%d 页，尺寸 %.1f×%.1fmm",
                 path, p.page_count, w_mm, h_mm)

    def close_template(self) -> None:
        ts.close_document(self.doc)
        self.doc = None
        self._page_cache.clear()
        self._fill_cache.clear()
        self._cells_cache.clear()

    def page_size_mm(self, index: int = 0) -> tuple[float, float]:
        if self.doc is None:
            return (210.0, 297.0)
        return ts.page_size(self.doc, index)

    def page_bitmap(self, index: int = 0, *, dpi: int = PREVIEW_DPI):
        """取某一页的位图（带缓存）。"""
        key = (int(index), int(dpi))
        if key not in self._page_cache:
            if self.doc is None:
                raise AppError("还没有打开表格，没法预览。")
            self._page_cache[key] = ts.render_page(self.doc, index, dpi)
        return self._page_cache[key]

    # ─────────────────────────────────────────────────────────
    # 字形库
    # ─────────────────────────────────────────────────────────

    def load_font(self, path_or_id: str | Path) -> None:
        """载入字形库（可以是 .hwfont 文件、目录，或已登记的 id）。"""
        p = Path(path_or_id)
        if not p.exists():
            found = fts.find_by_id(str(path_or_id))
            if found is None:
                raise AppError(
                    "找不到这套字形库。",
                    detail=f"路径或编号：{path_or_id}",
                    hint="它可能被移走或删掉了，去「管理字形库」里看看。",
                )
            p = found
        self.lib = fts.load_any(p, deep=True)
        self.lib_path = p
        if self.project is not None:
            self.project.font_id = self.lib.id
            self.project.font_name = self.lib.name
            self.project.font_embedded = False
        log.info("载入字形库 %s（%d 个字符）", self.lib.name, len(self.lib.characters))

    def font_summary(self):
        """当前字形库的摘要（首页卡片用）。"""
        if self.lib is None:
            libs = fts.list_libraries()
            return libs[0] if libs else None
        return fts.FontSummary(
            id=self.lib.id,
            name=self.lib.name,
            path=str(self.lib_path or ""),
            char_count=len(self.lib.characters),
            sample_count=sum(len(g.samples) for g in self.lib.characters.values()),
            coverage=fts.coverage_text(self.lib),
            updated_at=self.lib.updated_at,
        )

    def font_name(self) -> str:
        return getattr(self.lib, "name", "") or ""

    # ─────────────────────────────────────────────────────────
    # 参数
    # ─────────────────────────────────────────────────────────

    def current_page(self) -> int:
        return 0

    def grid_spec(self, index: int = 0) -> gs.GridSpec:
        if self.project is None or index >= len(self.project.pages):
            return gs.GridSpec()
        return self.project.pages[index].grid

    def apply_grid(self, index: int = 0, **kw) -> None:
        """按侧栏的值更新某一页的 GridSpec。"""
        if self.project is None or index >= len(self.project.pages):
            return
        pg = self.project.pages[index]
        spec = pg.grid
        pg.grid = replace(
            spec,
            start_x_mm=_f(kw.get("start_x_mm"), spec.start_x_mm),
            start_y_mm=_f(kw.get("start_y_mm"), spec.start_y_mm),
            col_width_mm=max(1.0, _f(kw.get("col_width_mm"), spec.col_width_mm)),
            row_height_mm=max(1.0, _f(kw.get("row_height_mm"), spec.row_height_mm)),
            cell_padding_mm=max(0.0, _f(kw.get("cell_padding_mm"), spec.cell_padding_mm)),
            rows=max(1, int(kw.get("rows", spec.rows))),
            cols=max(1, int(kw.get("cols", spec.cols))),
            active_cols=list(kw.get("active_cols", spec.active_cols) or []),
        )
        if self.project.apply_to_all_pages:
            for i, other in enumerate(self.project.pages):
                if i != index:
                    self.project.pages[i] = replace(other, grid=pg.grid)

    def apply_content(self, **kw) -> None:
        """更新填写内容设置。"""
        if self.project is None:
            return
        cur = self.project.content.spec
        r = cur.random
        min_v = _f(kw.get("min"), r.min)
        max_v = _f(kw.get("max"), r.max)
        if max_v < min_v:
            min_v, max_v = max_v, min_v
        len_min = max(1, int(kw.get("length_min", r.length_min)))
        len_max = max(len_min, int(kw.get("length_max", r.length_max)))

        new_random = replace(
            r,
            type="decimal",
            min=min_v,
            max=max_v,
            decimals=max(0, int(kw.get("decimals", r.decimals))),
            length_min=len_min,
            length_max=len_max,
        )
        mode = kw.get("mode") or cur.mode
        self.project.content = ContentSettings(
            spec=replace(cur, mode=mode, random=new_random),
            matrix=self.project.content.matrix,
        )

    def apply_jitter(self, values: dict) -> None:
        """按侧栏的值更新抖动参数。

        ★ 侧栏用的是「半幅」表示法（±15%），存储用 [min, max]，
          这里做一次换算。preset 的强度倍率也在这里乘上去。
        """
        if self.project is None:
            return
        v = values or {}
        preset = v.get("preset") or self.project.jitter.preset or "natural"
        f = JITTER_PRESET_FACTORS.get(preset, 1.0)

        cur = self.project.jitter

        def scaled(key: str, base: float, factor: float = 1.0) -> float:
            raw = v.get(key)
            if raw is None:
                return base
            return float(raw) * factor

        cell = CellJitter(
            offset_x=scaled("cell.offset_x", cur.cell.offset_x, f),
            offset_y=scaled("cell.offset_y", cur.cell.offset_y, f),
            rotate_deg=scaled("cell.rotate_deg", cur.cell.rotate_deg, f),
        )
        char_pos = CharPosJitter(
            offset_x=scaled("char_pos.offset_x", cur.char_pos.offset_x, f),
            offset_y=scaled("char_pos.offset_y", cur.char_pos.offset_y, f),
            spacing=scaled("char_pos.spacing", cur.char_pos.spacing, f),
        )

        def half(key: str, cur_min: float, cur_max: float) -> tuple[float, float]:
            """把「±half」还原成 [1-half, 1+half]。"""
            raw = v.get(key)
            if raw is None:
                return (cur_min, cur_max)
            h = abs(float(raw)) * f
            h = min(0.5, h)                  # 夹一下，别把字缩成 0
            return (1.0 - h, 1.0 + h)

        sc_min, sc_max = half("char_shape.scale_half",
                              cur.char_shape.scale_min, cur.char_shape.scale_max)
        sx_min, sx_max = half("char_shape.stretch_x_half",
                              cur.char_shape.stretch_x_min, cur.char_shape.stretch_x_max)
        sy_min, sy_max = half("char_shape.stretch_y_half",
                              cur.char_shape.stretch_y_min, cur.char_shape.stretch_y_max)
        dt_min, dt_max = half("dot.scale_half", cur.dot.scale_min, cur.dot.scale_max)

        char_shape = CharShapeJitter(
            scale_min=sc_min, scale_max=sc_max,
            bold_px_max=scaled("char_shape.bold_px_max",
                               cur.char_shape.bold_px_max, f),
            slant_deg=scaled("char_shape.slant_deg", cur.char_shape.slant_deg, f),
            stretch_x_min=sx_min, stretch_x_max=sx_max,
            stretch_y_min=sy_min, stretch_y_max=sy_max,
        )
        dot = DotJitter(
            offset=scaled("dot.offset", cur.dot.offset, f),
            scale_min=dt_min, scale_max=dt_max,
        )
        self.project.jitter = JitterSpec(
            preset=preset, cell=cell, char_pos=char_pos,
            char_shape=char_shape, dot=dot,
        )

    def apply_seed(self, seed) -> None:
        if self.project is not None:
            self.project.seed = seed

    def set_apply_to_all(self, on: bool) -> None:
        if self.project is not None:
            self.project.apply_to_all_pages = bool(on)

    # ─────────────────────────────────────────────────────────
    # 计算
    # ─────────────────────────────────────────────────────────

    def page_geometry(self, index: int = 0) -> gs.PageGeometry:
        w, h = self.page_size_mm(index)
        return gs.PageGeometry(index=index, width_mm=w, height_mm=h)

    def cells(self, index: int = 0, *, dpi: int = PREVIEW_DPI) -> list:
        """某一页的格子列表（带缓存）。"""
        key = index
        if key not in self._cells_cache:
            spec = self.grid_spec(index)
            page = self.page_geometry(index)
            try:
                self._cells_cache[key] = gs.build_cells(spec, page, dpi)
            except AppError:
                # 网格跑到纸外面去了 —— 不该让预览整个崩掉
                self._cells_cache[key] = []
        return self._cells_cache[key]

    def grid_lines(self, index: int = 0, *, dpi: int = PREVIEW_DPI) -> list:
        spec = self.grid_spec(index)
        page = self.page_geometry(index)
        return gs.grid_lines(spec, page, dpi)

    def cell_layers(self, index: int = 0, *, dpi: int = PREVIEW_DPI) -> list:
        """给画布用的 (row, col, rect_px) 三元组。

        ★ 画布的「内容坐标系」就是底图像素坐标（C4：px 在贴图时一次算好），
          所以格子矩形也必须是 px —— 不能传 mm（否则会被 zoom 二次放大）。
        rect_px 是 (left, top, right, bottom) 元组，这里包成带 .left/.top/...
        小对象，直接喂给 canvas 的 _paint_hover / _off_column_rects。
        """
        out = []
        for c in self.cells(index, dpi=dpi):
            l, t, r, b = c.rect_px
            out.append((c.row, c.col, _CellRect(c.row, c.col, l, t, r, b)))
        return out

    def matrix(self, index: int = 0, *, regenerate: bool = True):
        """某一页的内容矩阵。"""
        if self.project is None:
            return None
        spec = self.project.content.spec
        grid = self.grid_spec(index)

        if spec.mode == "manual":
            m = self.project.content.matrix
            if m is None:
                m = cs.empty_matrix(grid)
            else:
                m = cs.resize_matrix(m, grid)
            return m

        if not regenerate:
            m = self.project.content.matrix
            if m is not None:
                return cs.resize_matrix(m, grid)

        m = cs.generate_matrix(spec, grid, seed=self.project.seed)
        return m

    # ─────────────────────────────────────────────────────────
    # 填充
    # ─────────────────────────────────────────────────────────

    def fill_page(self, index: int = 0, *, dpi: int = PREVIEW_DPI):
        """渲染某一页的填充结果。返回 (底图, 填充层, FillResult)。

        ★ 底图和填充层尺寸必须一致（画布直接叠），这里强制对齐。
        """
        base = self.page_bitmap(index, dpi=dpi)
        cells = self.cells(index, dpi=dpi)
        matrix = self.matrix(index)

        if not cells or matrix is None or self.lib is None:
            return base, None, None

        result = fs.render_page(
            base, cells, matrix, self.lib, self._jitter(), self._seed()
        )
        return base, result.image, result

    def _jitter(self) -> JitterSpec:
        if self.project is not None:
            return self.project.jitter
        return JitterSpec()

    def _seed(self) -> int:
        if self.project is not None and self.project.seed is not None:
            return int(self.project.seed)
        return 12345                       # 固定兜底，保证预览稳定

    def refresh_caches(self) -> None:
        self._cells_cache.clear()
        self._fill_cache.clear()

    def validate_grid(self, index: int = 0) -> list[str]:
        """检查网格有没有跑到纸外面。返回中文提示列表。"""
        spec = self.grid_spec(index)
        page = self.page_geometry(index)
        out = []
        try:
            out = gs.validate(spec, page)
        except Exception:
            out = []
        oob = []
        try:
            oob = gs.out_of_bounds(spec, page)
        except Exception:
            oob = []
        return [str(x) for x in out] + [str(x) for x in oob]

    def stats(self, index: int = 0) -> dict:
        """状态栏要显示的一行统计。"""
        cells = self.cells(index)
        matrix = self.matrix(index)
        st = cs.stats(matrix) if matrix is not None else None
        active = self.grid_spec(index).active_cols
        n_cols = self.grid_spec(index).cols
        return {
            "cells": len(cells),
            "filled": getattr(st, "filled", 0),
            "chars": getattr(st, "chars", 0),
            "rows": self.grid_spec(index).rows,
            "cols": n_cols,
            "active_cols": len(active) if active else n_cols,
        }

    # ─────────────────────────────────────────────────────────
    # 手动录入
    # ─────────────────────────────────────────────────────────

    def manual_matrix(self, index: int = 0):
        """给手动录入弹窗的初始内容。"""
        return self.matrix(index, regenerate=False)

    def set_manual_matrix(self, matrix: ContentMatrix) -> None:
        if self.project is None:
            return
        self.project.content = ContentSettings(
            spec=replace(self.project.content.spec, mode="manual"),
            matrix=matrix,
        )

    # ─────────────────────────────────────────────────────────
    # 导出
    # ─────────────────────────────────────────────────────────

    def export(self, *, kind: str = "pdf", out: str = "", dpi: int = 300,
               pages: str | int = "all", current: int = 0,
               on_progress=None, cancel_flag=None) -> list[str]:
        """导出。返回产物路径列表。"""
        if self.doc is None:
            raise AppError("还没有打开表格，无从导出。")
        if self.lib is None:
            raise AppError(
                "还没有选字形库，没法往表格里写字。",
                hint="先点工具条右上角的「字形库」，选一套已经采集好的字迹。",
            )

        targets = self._resolve_pages(pages, current)
        rendered: list[tuple[int, Any]] = []
        for i in targets:
            _, fill_img, _ = self.fill_page(i, dpi=int(dpi))
            if fill_img is None:
                raise AppError(
                    "这一页还没有可写的内容。",
                    hint="检查一下有没有勾选任何「参与填充的列」。",
                )
            rendered.append((i, fill_img))

        if kind == "png":
            out_dir = out or self.exporter.suggest_output_dir(self.project)
            return self.exporter.export_png(
                rendered, out_dir, project=self.project,
                on_progress=on_progress, cancel_flag=cancel_flag,
            )

        out_path = out or self.exporter.suggest_output_path(self.project, "pdf")
        return [self.exporter.export_pdf(
            rendered, self.doc, out_path, dpi=int(dpi), project=self.project,
            on_progress=on_progress, cancel_flag=cancel_flag,
        )]

    def _resolve_pages(self, pages, current: int) -> list[int]:
        n = self.page_count
        if isinstance(pages, int):
            return [max(0, min(n - 1, pages))]
        s = str(pages or "all").strip().lower()
        if s in ("all", ""):
            return list(range(n))
        if s == "current":
            return [max(0, min(n - 1, current))]
        # "1,3,5-7" 这种
        out: list[int] = []
        for part in re.split(r"[,，\s]+", s):
            if not part:
                continue
            if "-" in part:
                a, _, b = part.partition("-")
                try:
                    lo, hi = int(a), int(b)
                except ValueError:
                    continue
                for k in range(min(lo, hi), max(lo, hi) + 1):
                    if 1 <= k <= n:
                        out.append(k - 1)
            else:
                try:
                    k = int(part)
                except ValueError:
                    continue
                if 1 <= k <= n:
                    out.append(k - 1)
        return sorted(set(out)) or list(range(n))

    def suggest_export_path(self, kind: str = "pdf") -> str:
        if kind == "png":
            return self.exporter.suggest_output_dir(self.project)
        return self.exporter.suggest_output_path(self.project, "pdf")

    # ─────────────────────────────────────────────────────────
    # 项目存取
    # ─────────────────────────────────────────────────────────

    def save_project(self, path: str | Path, *, thumbnail=None) -> str:
        if self.project is None:
            raise AppError("还没有打开表格，没东西可存。")
        tpl_bytes = None
        if self.doc is not None:
            try:
                tpl_bytes = Path(self.doc.path).read_bytes()
            except OSError as e:
                log.warning("模板读不出来，项目里就不内嵌了：%s", e)
        self.project.updated_at = prj.now_iso()
        return self.projects.save(self.project, path,
                                  template_bytes=tpl_bytes, thumbnail=thumbnail)

    def load_project(self, path: str | Path) -> None:
        self.close_template()
        p = self.projects.load(path)
        self.project = p

        # 模板：优先用项目内嵌的副本，其次用原路径
        tpl = p.template_path
        if p.template_original_path and Path(p.template_original_path).exists():
            tpl = p.template_original_path
        if tpl and Path(tpl).exists():
            self.doc = ts.open_document(tpl, preview_dpi=PREVIEW_DPI)
            self.warnings = list(getattr(self.doc, "warnings", []) or [])
        else:
            self.warnings = list(p.warnings or [])
            self.warnings.append(
                "原来的表格文件找不到了，网格和内容都还在，"
                "点「打开」重新选一次那张表格就能接着做。"
            )

        if p.font_id:
            try:
                self.load_font(p.font_id)
            except AppError as e:
                self.warnings.append(str(e.user_message))

    # ─────────────────────────────────────────────────────────
    # 采集：把框选出来的字形攒成字形库
    # ─────────────────────────────────────────────────────────

    def build_font(self, name: str, labeled: list[tuple],
                   target_height: int = 128):
        """按「框 + 字符」列表造一套字形库。

        Args:
            labeled: [(rect(QRect, 图像坐标), char), ...]
        """
        from PIL import Image

        if not labeled:
            raise AppError("一个字形都没标，导不出字形库。",
                           hint="回到第 3 步，点一个字符再点对应的框。")

        lib = fts.FontLibrary(
            id=fts.new_library_id(name),
            name=name or "我的字迹",
            charset=list(_chars_used(labeled)),
            glyph_height=target_height,
            created_at=prj.now_iso(),
            updated_at=prj.now_iso(),
        )

        src = getattr(self, "_capture_image", None)
        if src is None:
            raise AppError("采集用的图片丢了，请重新选一次图片。")

        for rect, ch in labeled:
            box = (int(rect.left()), int(rect.top()),
                   int(rect.right()), int(rect.bottom()))
            box = _clamp_box(box, src.size)
            if box is None:
                continue
            piece = src.crop(box)
            try:
                fts.add_samples(lib, ch, [piece])
            except Exception as e:              # 单个字失败不该毁掉整套
                log.warning("字形 %r 加入失败：%s", ch, e)

        if not lib.characters:
            raise AppError("字形库里一个有效的字形都没有。",
                           hint="框可能太靠边被裁掉了，重新框一次试试。")
        return lib

    def set_capture_image(self, img) -> None:
        """记下采集用的处理结果图（切成字形时要用）。"""
        self._capture_image = img

    # ─────────────────────────────────────────────────────────
    # 采集辅助（给控制器用）
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def cutout(src_image, *, tolerance: int = 29, invert: bool = False,
               rotate: float = 0.0):
        """抠图：旋转校正 → 去背景 → 可选反色。

        ★ 旋转必须放在去背景**之前**：先转正好再判定背景色，
          否则转出来的白边会被当成"内容"留在图上。
        """
        from PIL import Image

        img = src_image
        if rotate:
            # ★ expand=True + 白底填充：转完尺寸会变大，不填白会留黑边
            img = img.rotate(
                -rotate,
                resample=Image.BICUBIC,
                expand=True,
                fillcolor=(255, 255, 255, 255) if img.mode == "RGBA" else (255, 255, 255),
            )
        try:
            out = imageops.remove_background(img, tolerance=int(tolerance))
        except Exception as e:
            log.warning("去背景失败，退回原图：%s", e)
            out = img
        if invert:
            out = _invert_rgba(out)
        return out

    @staticmethod
    def auto_boxes(processed_image) -> list[tuple[int, int, int, int]]:
        """自动框选：切连通块 + 按阅读顺序排。"""
        try:
            boxes = imageops.split_components(processed_image)
            return imageops.sort_reading_order(boxes)
        except Exception as e:
            log.warning("自动框选失败：%s", e)
            return []


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────


def _f(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _chars_used(labeled: list[tuple]) -> list[str]:
    seen: list[str] = []
    for _rect, ch in labeled:
        if ch and ch not in seen:
            seen.append(ch)
    return seen


def _clamp_box(box, size) -> tuple[int, int, int, int] | None:
    w, h = size
    l, t, r, b = box
    l, r = sorted((l, r))
    t, b = sorted((t, b))
    l = max(0, min(w, l))
    r = max(0, min(w, r))
    t = max(0, min(h, t))
    b = max(0, min(h, b))
    if r - l < 3 or b - t < 3:
        return None
    return (l, t, r, b)


def _invert_rgba(img):
    """反色：只翻 RGB，保留 alpha（否则透明处会变成不透明的白）。"""
    from PIL import Image

    if img.mode != "RGBA":
        img = img.convert("RGBA")
    r, g, b, a = img.split()
    r = Image.eval(r, lambda v: 255 - v)
    g = Image.eval(g, lambda v: 255 - v)
    b = Image.eval(b, lambda v: 255 - v)
    return Image.merge("RGBA", (r, g, b, a))


class _CellRect:
    """给画布用的轻量格子矩形（带 .left/.top/.right/.bottom 属性）。"""

    __slots__ = ("row", "col", "left", "top", "right", "bottom")

    def __init__(self, row, col, left, top, right, bottom):
        self.row = row
        self.col = col
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def __iter__(self):
        # 方便解包成 (row, col, rect)
        return iter((self.row, self.col, self))
