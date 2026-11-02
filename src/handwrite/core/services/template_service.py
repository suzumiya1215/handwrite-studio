"""模板服务 —— PDF / 图片载入与页面栅格化（L3 服务层）。

设计依据：
- 模块设计说明书 §4.4
- 数据格式规范 §2.1
- 接口契约与测试用例 §5

职责边界：
- 只负责「把用户给的 PDF / 图片变成可用的页面位图 + 页面尺寸」
- 不负责网格、不负责填充、不碰 Qt

★ 契约 C3：模板只提供**背景位图**，内容由填充引擎画在上面。
★ 契约 C12：预览用低 DPI（150），导出用高 DPI（300），
  同一次会话里不要反复换 DPI 重渲染 —— 界面在缩放时只做位图缩放。
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import geometry as geo
from ..errors import TemplateUnreadableError
from ..models.template import TemplateDoc, TemplatePage

#: 识别的图片扩展名
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"})
#: 识别的 PDF 扩展名
PDF_EXTS = frozenset({".pdf"})

#: 单页栅格化的像素上限（防止 A0 图纸 + 600dpi 直接把内存打爆）
MAX_PAGE_PIXELS = 64_000_000

#: 打开文档时预览用的 DPI
DEFAULT_PREVIEW_DPI = 150


def _ext(path: str | Path) -> str:
    return os.path.splitext(str(path))[1].lower()


def is_pdf(path: str | Path) -> bool:
    return _ext(path) in PDF_EXTS


def is_image(path: str | Path) -> bool:
    return _ext(path) in IMAGE_EXTS


def is_supported(path: str | Path) -> bool:
    return is_pdf(path) or is_image(path)


# ─────────────────────────────────────────────────────────────
# 打开
# ─────────────────────────────────────────────────────────────


def open_document(path: str | Path, *, preview_dpi: int = DEFAULT_PREVIEW_DPI) -> TemplateDoc:
    """打开模板文件（PDF 或图片）。

    Args:
        path: 文件路径
        preview_dpi: 打开时记录的预览 DPI（栅格化时再传，不在这里渲染）

    Returns:
        TemplateDoc（内含每页的毫米尺寸）

    Raises:
        TemplateUnreadableError: 文件不存在 / 格式不支持 / 打开失败（中文原因）
    """
    p = Path(path)
    if not p.exists():
        raise TemplateUnreadableError(f"模板文件不存在：{p.name}")
    if not p.is_file():
        raise TemplateUnreadableError(f"这个路径不是文件：{p}")

    if is_pdf(p):
        return _open_pdf(p, preview_dpi=preview_dpi)
    if is_image(p):
        return _open_image(p, preview_dpi=preview_dpi)

    raise TemplateUnreadableError(
        f"不支持的模板格式「{_ext(p) or '无扩展名'}」。"
        f"请用 PDF 或图片（PNG / JPG / BMP / TIFF / WEBP）。"
    )


def _open_pdf(p: Path, *, preview_dpi: int) -> TemplateDoc:
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover - 依赖缺失时的兜底
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError as exc:
            raise TemplateUnreadableError("缺少 PDF 支持库（PyMuPDF），请重新安装本程序。") from exc

    try:
        doc = pymupdf.open(str(p))
    except Exception as exc:
        raise TemplateUnreadableError(f"打不开这个 PDF：{p.name}（{exc}）") from exc

    if doc.needs_pass:
        doc.close()
        raise TemplateUnreadableError(f"这个 PDF 有密码保护，请先用别的工具去掉密码：{p.name}")

    pages: list[TemplatePage] = []
    warnings: list[str] = []
    for i, page in enumerate(doc):
        try:
            r = page.rect
            w_mm = geo.px_to_mm(r.width, 72)   # PDF 用户单位 = 1/72 英寸 = pt
            h_mm = geo.px_to_mm(r.height, 72)
        except Exception as exc:  # pragma: no cover
            warnings.append(f"第 {i + 1} 页尺寸读不出来，已按 A4 处理（{exc}）")
            w_mm, h_mm = 210.0, 297.0
        pages.append(TemplatePage(index=i, width_mm=round(w_mm, 2), height_mm=round(h_mm, 2)))

    if not pages:
        doc.close()
        raise TemplateUnreadableError(f"这个 PDF 一页都没有：{p.name}")

    return TemplateDoc(
        path=str(p),
        pages=pages,
        source_format="pdf",
        preview_dpi=preview_dpi,
        original_name=p.name,
        handle=doc,
        warnings=warnings,
    )


def _open_image(p: Path, *, preview_dpi: int) -> TemplateDoc:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise TemplateUnreadableError("缺少图像库（Pillow），请重新安装本程序。") from exc

    try:
        with Image.open(str(p)) as im:
            w, h = im.size
            dpi_info = im.info.get("dpi") or (0, 0)
    except Exception as exc:
        raise TemplateUnreadableError(f"打不开这张图片：{p.name}（{exc}）") from exc

    warnings: list[str] = []
    # 优先用图片自带的 DPI；没有的话按 96（屏幕默认）处理
    try:
        src_dpi = float(dpi_info[0]) if dpi_info and dpi_info[0] else 0.0
    except (TypeError, ValueError):
        src_dpi = 0.0
    if src_dpi <= 1.0:
        src_dpi = 96.0
        warnings.append("这张图片没写 DPI，已按 96 计算尺寸；如果尺寸不对，请改用 PDF 模板。")

    w_mm = geo.px_to_mm(w, int(round(src_dpi)))
    h_mm = geo.px_to_mm(h, int(round(src_dpi)))

    page = TemplatePage(index=0, width_mm=round(w_mm, 2), height_mm=round(h_mm, 2))
    return TemplateDoc(
        path=str(p),
        pages=[page],
        source_format="image",
        preview_dpi=preview_dpi,
        original_name=p.name,
        handle=None,
        warnings=warnings,
    )


def close_document(doc: TemplateDoc | None) -> None:
    """释放底层句柄（PDF 需要显式关，图片不需要）。"""
    if doc is None or doc.handle is None:
        return
    try:
        doc.handle.close()
    except Exception:
        pass
    finally:
        doc.handle = None


# ─────────────────────────────────────────────────────────────
# 栅格化
# ─────────────────────────────────────────────────────────────


def render_page(doc: TemplateDoc, index: int, dpi: int) -> "object":
    """把第 index 页渲染成 RGBA 位图（PIL Image）。

    ★ 只做背景，不带任何填充内容。
    ★ 超过像素上限时按比例降 DPI 并在 doc.warnings 里记一条。
    """
    if doc is None:
        raise TemplateUnreadableError("还没有打开模板")
    if not (0 <= index < len(doc.pages)):
        raise TemplateUnreadableError(f"页码超出范围：第 {index + 1} 页（共 {len(doc.pages)} 页）")

    page_info = doc.pages[index]

    if doc.source_format == "image":
        return _render_image(doc, dpi)

    return _render_pdf_page(doc, page_info, dpi)


def _guard_dpi(w_mm: float, h_mm: float, dpi: int) -> int:
    """像素上限保护：太大就降 DPI。"""
    px_w, px_h = geo.page_size_px(w_mm, h_mm, dpi)
    if px_w * px_h <= MAX_PAGE_PIXELS:
        return dpi
    k = (MAX_PAGE_PIXELS / float(px_w * px_h)) ** 0.5
    return max(36, int(dpi * k))


def _render_pdf_page(doc: TemplateDoc, page_info: TemplatePage, dpi: int) -> "object":
    """把 PDF 某页渲染成**白底**的 RGBA 位图。

    ★ 为什么不留透明：手写填充是「叠在纸上」的效果，
      底图透明的话，程序里看着还行，但一旦导出成 PNG/JPG
      或送进打印，透明区域会变成黑块。
      所以这里强制**先铺白纸，再印模板内容**（纸张观感，也是打印观感）。

    ★ 为什么不用 alpha=True：PyMuPDF 对某些 PDF 返回的
      alpha 通道全是 255、RGB 全是 0（等价于"全黑"），
      直接 frombytes 会得到一张纯黑图 —— 手写字叠上去根本看不见。
      改用「渲染到白底色块上」，语义明确、不依赖 PDF 的 alpha 行为。
    """
    if doc.handle is None:
        raise TemplateUnreadableError("模板句柄已关闭，请重新打开文件")

    dpi = _guard_dpi(page_info.width_mm, page_info.height_mm, dpi)
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        import fitz as pymupdf  # type: ignore

    try:
        page = doc.handle[page_info.index]
        zoom = dpi / 72.0
        mat = pymupdf.Matrix(zoom, zoom)
        # 白底渲染：alpha=False 得到不透明 RGB，PDF 的空白自然就是白纸
        pix = page.get_pixmap(matrix=mat, alpha=False)
        from PIL import Image

        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return im.convert("RGBA")
    except TemplateUnreadableError:
        raise
    except Exception as exc:
        raise TemplateUnreadableError(f"第 {page_info.index + 1} 页渲染失败：{exc}") from exc


def _render_image(doc: TemplateDoc, dpi: int) -> "object":
    """把图片模板渲染成**白底**的 RGBA 位图。

    ★ 与 PDF 同理：透明区域必须先铺白，否则导出/打印会变黑块。
    """
    from PIL import Image

    try:
        with Image.open(doc.path) as im:
            page = doc.pages[0]
            want_w, want_h = geo.page_size_px(page.width_mm, page.height_mm, dpi)
            if (want_w, want_h) != im.size and want_w > 0 and want_h > 0:
                im = im.resize((want_w, want_h), Image.LANCZOS)
            return _flatten_on_white(im)
    except TemplateUnreadableError:
        raise
    except Exception as exc:
        raise TemplateUnreadableError(f"图片渲染失败：{exc}") from exc


def _flatten_on_white(im: "object") -> "object":
    """把任意模式图片拍平成白底 RGBA（透明处变白）。"""
    from PIL import Image

    if im.mode == "RGBA":
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        return bg
    if im.mode == "P":
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        return bg
    if im.mode in ("LA",):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        return bg
    return im.convert("RGBA")


def page_size(doc: TemplateDoc, index: int) -> tuple[float, float]:
    """取第 index 页的毫米尺寸 (宽, 高)。"""
    if doc is None or not (0 <= index < len(doc.pages)):
        raise TemplateUnreadableError(f"页码超出范围：第 {index + 1} 页")
    p = doc.pages[index]
    return p.width_mm, p.height_mm


def page_count(doc: TemplateDoc | None) -> int:
    return len(doc.pages) if doc else 0


def describe(doc: TemplateDoc | None) -> str:
    """给界面状态栏用的一句话摘要。"""
    if doc is None:
        return "未选择模板"
    fmt = "PDF" if doc.source_format == "pdf" else "图片"
    p0 = doc.pages[0]
    size = f"{p0.width_mm:.0f}×{p0.height_mm:.0f}mm"
    if len(doc.pages) == 1:
        return f"{fmt}模板：{doc.original_name or Path(doc.path).name}（{size}）"
    return f"{fmt}模板：{doc.original_name or Path(doc.path).name}（{len(doc.pages)} 页，第 1 页 {size}）"


def suggest_page_size(doc: TemplateDoc | None) -> tuple[float, float]:
    """给「新建空白文档」用的默认页面尺寸（A4）。"""
    if doc is not None and doc.pages:
        return page_size(doc, 0)
    return 210.0, 297.0


def is_a4(w_mm: float, h_mm: float, tol: float = 3.0) -> bool:
    """判断是不是 A4（横竖都算），供界面提示用。"""
    portrait = abs(w_mm - 210.0) <= tol and abs(h_mm - 297.0) <= tol
    landscape = abs(w_mm - 297.0) <= tol and abs(h_mm - 210.0) <= tol
    return portrait or landscape
