"""模板数据模型。

设计依据：
- 数据格式规范 §2.7（template/meta.json）
- 模块设计说明书 §5.2

坐标口径：
- 页面原点在**左上角**（不是 PDF 内部的左下角）
- 尺寸以 **mm** 为存储单位
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import geometry as geo

# ─────────────────────────────────────────────────────────────
# 单页
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TemplatePage:
    """模板的一页。"""

    index: int
    width_mm: float
    height_mm: float

    @property
    def size_mm(self) -> tuple[float, float]:
        return self.width_mm, self.height_mm

    @property
    def is_landscape(self) -> bool:
        return self.width_mm > self.height_mm

    def size_px(self, dpi: int) -> tuple[int, int]:
        return geo.page_size_px(self.width_mm, self.height_mm, dpi)

    @property
    def is_a4(self) -> bool:
        """粗略判断是否 A4（210×297，允许 2mm 误差）。"""
        w, h = sorted([self.width_mm, self.height_mm])
        return abs(w - 210) < 2 and abs(h - 297) < 2

    def to_meta(self) -> dict:
        return {
            "width_pt": round(geo.mm_to_pt(self.width_mm), 3),
            "height_pt": round(geo.mm_to_pt(self.height_mm), 3),
            "width_mm": round(self.width_mm, 2),
            "height_mm": round(self.height_mm, 2),
        }


# ─────────────────────────────────────────────────────────────
# 文档
# ─────────────────────────────────────────────────────────────


@dataclass
class TemplateDoc:
    """一个载入的模板文档（PDF 或多页图片）。"""

    path: str
    pages: list[TemplatePage] = field(default_factory=list)
    source_format: str = "pdf"          # "pdf" | "image"
    preview_dpi: int = geo.DPI_PREVIEW
    original_name: str = ""
    #: 底层句柄（fitz.Document / PIL.Image）。**不参与序列化**。
    handle: Any = None
    #: 载入时发现的问题（如字体缺失），界面提示用
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page(self, index: int) -> TemplatePage:
        if not (0 <= index < len(self.pages)):
            raise IndexError(f"页号超出范围：{index}（共 {len(self.pages)} 页）")
        return self.pages[index]

    def geometry(self, index: int) -> "PageGeometry":
        """取某页的 PageGeometry（M5 网格计算的输入）。"""
        return PageGeometry.of(self.page(index))

    def all_geometries(self) -> list["PageGeometry"]:
        return [PageGeometry.of(p) for p in self.pages]

    def display_name(self) -> str:
        """展示名（优先原文件名）。"""
        return self.original_name or self.path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


# ─────────────────────────────────────────────────────────────
# 页几何（网格计算的输入）
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PageGeometry:
    """一页的几何信息 —— 网格计算的输入。

    只保留「算格子需要的东西」，不带页号以外的状态。
    """

    index: int
    width_mm: float
    height_mm: float

    @classmethod
    def of(cls, page: TemplatePage) -> "PageGeometry":
        return cls(index=page.index, width_mm=page.width_mm, height_mm=page.height_mm)

    @classmethod
    def a4(cls, index: int = 0) -> "PageGeometry":
        """A4 竖版，测试里常用。"""
        return cls(index=index, width_mm=210.0, height_mm=297.0)

    @property
    def size_mm(self) -> tuple[float, float]:
        return self.width_mm, self.height_mm

    @property
    def rect_mm(self) -> geo.Rect:
        """整页矩形（mm，原点左上）。"""
        return geo.Rect(0.0, 0.0, self.width_mm, self.height_mm)

    def size_px(self, dpi: int) -> tuple[int, int]:
        return geo.page_size_px(self.width_mm, self.height_mm, dpi)

    def rect_px(self, dpi: int) -> geo.Rect:
        w, h = self.size_px(dpi)
        return geo.Rect(0.0, 0.0, float(w), float(h))
