"""导出服务 —— PNG / PDF 落盘（L3 服务层）。

设计依据：
- 模块设计说明书 §4.8
- 接口契约与测试用例 §9
- 数据格式规范 §5（DPI 口径）

三条铁律：
1. **导出 PDF 一律用位图页**（不做矢量），保证「所见即所得」——
   手写字迹是像素，矢量化只会让笔画变脏。
2. **覆盖保护**：默认不覆盖已有文件，重名自动加 `(1)` `(2)`。
3. **可取消**：`cancel_flag`（threading.Event / 任意带 is_set() 的对象）
   一置位就抛 ExportCancelledError，不留半个文件。

★ 为什么用 PyMuPDF 写而不是自己拼 PDF：
  项目里 PyMuPDF 已经是必需依赖（读模板 + 渲染），复用它写 PDF
  比手写 xref/对象表可靠得多；且它支持「整页位图 + 任意页面尺寸」，
  正好是本项目唯一需要的输出形态。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .. import geometry as geo
from ..errors import ExportCancelledError, ExportFailedError
from ..models.project import Project

#: 导出文件名后缀（中文，用户一眼能看出哪份是手写版）
SUFFIX = "手写版"
#: 支持的导出 DPI
ALLOWED_DPI = (150, 200, 300, 600)
#: PNG 默认命名模板
DEFAULT_NAME_PATTERN = "{name}_" + SUFFIX + "_{page:03d}.png"


def _fmt_size_mb(nbytes: int) -> str:
    return f"{nbytes / 1024 / 1024:.2f} MB"


def _mupdf() -> Any:
    """统一拿 PyMuPDF（新版包名 pymupdf，旧版 fitz）。"""
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore

            return pymupdf
        except ImportError as exc:  # pragma: no cover
            raise ExportFailedError("缺少 PDF 支持库（PyMuPDF），请重新安装本程序。") from exc


class ExportService:
    """导出器。无状态，可复用；所有方法都不改入参。"""

    # ── 命名 ─────────────────────────────────────────────

    def suggest_output_path(self, project: Project | None, kind: str = "pdf") -> str:
        """给出一份不冲突的输出路径建议。

        - 优先放在**模板同目录**（用户对自己的文件夹有预期）
        - 没有模板就放桌面
        - 名字 = `<项目名>_手写版.<ext>`，重名自动加 (1)(2)…
        """
        ext = "." + str(kind).lower().lstrip(".")
        base_dir = self._base_dir(project)
        name = self._base_name(project)
        stem = f"{name}_{SUFFIX}"
        return self._dedupe(os.path.join(base_dir, stem + ext))

    def suggest_output_dir(self, project: Project | None) -> str:
        """PNG 批量导出的目录建议：`<项目名>_手写版_导出`（自动去重）。"""
        base_dir = self._base_dir(project)
        name = self._base_name(project)
        return self._dedupe(os.path.join(base_dir, f"{name}_{SUFFIX}_导出"))

    def _base_dir(self, project: Project | None) -> str:
        for cand in (
            getattr(project, "template_path", "") if project else "",
            getattr(project, "file_path", "") if project else "",
        ):
            if cand and Path(cand).parent.exists():
                return str(Path(cand).parent)
        return str(self._desktop())

    @staticmethod
    def _desktop() -> Path:
        home = Path.home()
        for name in ("Desktop", "桌面"):
            p = home / name
            if p.exists():
                return p
        return home

    @staticmethod
    def _base_name(project: Project | None) -> str:
        name = (getattr(project, "name", "") or "").strip()
        if name:
            return _safe_name(name)
        tp = getattr(project, "template_path", "") if project else ""
        if tp:
            return _safe_name(Path(tp).stem)
        return "未命名"

    @staticmethod
    def _dedupe(path: str) -> str:
        """重名加 (1)(2)…；目录同理。"""
        p = Path(path)
        if not p.exists():
            return str(p)
        stem, ext = p.stem, p.suffix
        parent = p.parent
        for i in range(1, 10000):
            cand = parent / f"{stem}({i}){ext}"
            if not cand.exists():
                return str(cand)
        raise ExportFailedError("同名文件太多了（超过 1 万个），请换个导出位置。")

    # ── PNG ──────────────────────────────────────────────

    def export_png(
        self,
        pages: Sequence[tuple[int, Any]],
        out_dir: str,
        *,
        name_pattern: str = DEFAULT_NAME_PATTERN,
        project: Project | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        cancel_flag: Any = None,
    ) -> list[str]:
        """把每页写成 PNG。

        Args:
            pages: [(页码, 位图), ...]，页码从 0 起，命名时 +1
            out_dir: 输出目录（不存在会自动建）
            name_pattern: 支持 {name} {page} {page3} {index} 占位符

        Returns:
            写出的文件路径列表（按输入顺序）
        """
        d = Path(out_dir)
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ExportFailedError(f"建不了导出目录：{d}（{exc}）") from exc
        if not os.access(str(d), os.W_OK):
            raise ExportFailedError(f"这个目录不能写：{d}")

        base = self._base_name(project)
        total = len(pages)
        out_paths: list[str] = []

        for i, (page_index, img) in enumerate(pages):
            _check_cancel(cancel_flag)
            fn = name_pattern.format(
                name=base,
                page=page_index + 1,
                page3=f"{page_index + 1:03d}",
                index=i,
            )
            fn = _safe_name(Path(fn).stem) + (Path(fn).suffix or ".png")
            target = Path(self._dedupe(str(d / fn)))

            try:
                _to_rgb(img).save(str(target), "PNG")
            except OSError as exc:
                raise ExportFailedError(f"写不了这个文件：{target.name}（{exc}）") from exc
            except Exception as exc:
                raise ExportFailedError(f"导出 PNG 失败：{target.name}（{exc}）") from exc

            out_paths.append(str(target))
            if on_progress:
                on_progress(i + 1, total)

        return out_paths

    # ── PDF ──────────────────────────────────────────────

    def export_pdf(
        self,
        pages: Sequence[tuple[int, Any]],
        template_doc: Any = None,
        out_path: str = "",
        *,
        dpi: int = 300,
        on_progress: Callable[[int, int], None] | None = None,
        cancel_flag: Any = None,
        project: Project | None = None,
    ) -> str:
        """把每页位图合成一份 PDF。

        Args:
            pages: [(页码, 位图), ...]
            template_doc: TemplateDoc；用来取每页毫米尺寸（决定 PDF 页面大小）
            out_path: 输出文件路径，空则自动建议
            dpi: 位图对应的 DPI，模板缺失时用来反推页面尺寸

        Returns:
            写出的 PDF 路径
        """
        if not pages:
            raise ExportFailedError("没有可导出的页面。")

        if not out_path:
            out_path = self.suggest_output_path(project, "pdf")
        target = Path(out_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ExportFailedError(f"建不了导出目录：{target.parent}（{exc}）") from exc
        if target.exists():
            raise ExportFailedError(f"这个文件已经存在了：{target.name}（换个名字或先删掉）")
        if not os.access(str(target.parent), os.W_OK):
            raise ExportFailedError(f"这个目录不能写：{target.parent}")

        mupdf = _mupdf()
        doc = mupdf.open()
        total = len(pages)

        try:
            for i, (page_index, img) in enumerate(pages):
                _check_cancel(cancel_flag)
                rgb = _to_rgb(img)
                w_mm, h_mm = self._page_mm(template_doc, page_index, rgb, dpi)
                self._add_page(doc, rgb, w_mm, h_mm)
                if on_progress:
                    on_progress(i + 1, total)

            _check_cancel(cancel_flag)

            try:
                doc.save(str(target), deflate=True, garbage=3)
            except OSError as exc:
                raise ExportFailedError(f"写不了这个文件：{target.name}（{exc}）") from exc
        except ExportCancelledError:
            _quiet_unlink(target)
            raise
        except ExportFailedError:
            _quiet_unlink(target)
            raise
        except Exception as exc:
            _quiet_unlink(target)
            raise ExportFailedError(f"导出 PDF 失败：{exc}") from exc
        finally:
            try:
                doc.close()
            except Exception:
                pass

        return str(target)

    @staticmethod
    def _page_mm(template_doc: Any, page_index: int, img: Any, dpi: int) -> tuple[float, float]:
        """定 PDF 页面物理尺寸：优先用模板尺寸，否则按位图 + DPI 反推。"""
        try:
            pages = getattr(template_doc, "pages", None)
            if pages and 0 <= page_index < len(pages):
                p = pages[page_index]
                return float(p.width_mm), float(p.height_mm)
        except Exception:
            pass
        w, h = img.size
        return geo.px_to_mm(w, dpi), geo.px_to_mm(h, dpi)

    @staticmethod
    def _add_page(doc: Any, rgb: Any, w_mm: float, h_mm: float) -> None:
        """写一页：整页铺满一张位图（所见即所得）。"""
        import io as _io

        w_pt = geo.mm_to_pt(w_mm)
        h_pt = geo.mm_to_pt(h_mm)
        try:
            page = doc.new_page(width=w_pt, height=h_pt)
            buf = _io.BytesIO()
            rgb.save(buf, "PNG")
            page.insert_image(page.rect, stream=buf.getvalue())
        except Exception as exc:
            raise ExportFailedError(f"PDF 页面写入失败：{exc}") from exc

    # ── 状态查询 ─────────────────────────────────────────

    @staticmethod
    def folder_size(paths: Iterable[str]) -> str:
        total = 0
        for p in paths:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return _fmt_size_mb(total)

    @staticmethod
    def verify_pdf(path: str, expect_pages: int | None = None) -> bool:
        """写完后自查：能打开、页数对得上。"""
        mupdf = _mupdf()
        try:
            with mupdf.open(path) as d:
                if expect_pages is not None and d.page_count != expect_pages:
                    return False
                return d.page_count > 0
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────────────


def _check_cancel(cancel_flag: Any) -> None:
    if cancel_flag is None:
        return
    try:
        if cancel_flag.is_set():
            raise ExportCancelledError("导出已取消。")
    except AttributeError:
        if cancel_flag:
            raise ExportCancelledError("导出已取消。")


def _to_rgb(img: Any) -> Any:
    """导出统一转 RGB（白底）——PDF/PNG 都不要透明通道，免得打印时变黑块。"""
    from PIL import Image as _I

    if img is None:
        raise ExportFailedError("有一页是空的，没法导出。")
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = _I.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def _quiet_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


_SAFE_MAP = {
    "\\": "＼", "/": "／", ":": "：", "*": "＊", "?": "？",
    '"': "”", "<": "＜", ">": "＞", "|": "｜",
}


def _safe_name(name: str) -> str:
    """清掉文件名里 Windows 不认的字符。"""
    s = "".join(_SAFE_MAP.get(c, c) for c in str(name)).strip().rstrip(".")
    return s or "未命名"
