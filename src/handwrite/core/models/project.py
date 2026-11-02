"""项目数据模型。

设计依据：
- 数据格式规范 §2.2（project.json）
- 模块设计说明书 §5.6

注意区分两类字段：
- **持久化字段**  —— 写进 project.json
- **运行时字段**  —— 打开时临时置的（如 unresolved_font），不序列化
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .content import ContentSpec, ContentMatrix
from .grid import GridSpec
from .jitter import JitterSpec

# ─────────────────────────────────────────────────────────────
# 每页设置
# ─────────────────────────────────────────────────────────────


@dataclass
class PageSettings:
    """单页的网格与内容设置（apply_to_all_pages = False 时逐页使用）。"""

    index: int = 0
    grid: GridSpec = field(default_factory=GridSpec)
    #: 该页内容（手动模式下有效；随机模式由 seed 重新生成）
    matrix: ContentMatrix | None = None

    def to_dict(self) -> dict:
        return {"index": self.index, "grid": self.grid.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "PageSettings":
        return cls(
            index=int(d.get("index", 0)),
            grid=GridSpec.from_dict(d.get("grid") or {}),
        )


@dataclass
class ContentSettings:
    """内容来源设置（project.json 的 content 字段）。

    与 ContentSpec 的区别：这个带运行时缓存（matrix）。
    """

    spec: ContentSpec = field(default_factory=ContentSpec)
    #: 手动模式的内容。**运行时字段，不写进 project.json**（写 content.tsv）
    matrix: ContentMatrix | None = None

    def to_dict(self) -> dict:
        return self.spec.to_dict()

    @classmethod
    def from_dict(cls, d: dict) -> "ContentSettings":
        return cls(spec=ContentSpec.from_dict(d or {}))


# ─────────────────────────────────────────────────────────────
# 项目
# ─────────────────────────────────────────────────────────────


@dataclass
class Project:
    """一个填表任务（对应一个 .hwpj 文件）。"""

    id: str = ""
    name: str = ""
    # ── 字形库引用（存 id 不存路径，改名/挪位置不影响）──
    font_id: str = ""
    font_name: str = ""
    font_embedded: bool = False
    # ── 模板 ────────────────────────────────────────────
    template_path: str = ""            # 容器内相对路径（template/template.pdf）
    template_original_path: str = ""   # 用户最初选的位置，仅展示
    page_count: int = 1
    apply_to_all_pages: bool = True
    # ── 页设置 ──────────────────────────────────────────
    pages: list[PageSettings] = field(default_factory=list)
    # ── 内容 / 抖动 ─────────────────────────────────────
    content: ContentSettings = field(default_factory=ContentSettings)
    jitter: JitterSpec = field(default_factory=lambda: JitterSpec.from_preset("natural"))
    #: ★ 随机种子。None = 真随机；一旦需要「预览=导出」就必须固化成整数
    seed: int | None = None
    # ── 导出 ────────────────────────────────────────────
    export_dpi: int = 300
    export_pages: str = "all"
    export_dir: str = ""
    # ── 时间戳 ──────────────────────────────────────────
    created_at: str = ""
    updated_at: str = ""
    app_version: str = ""

    # ── 运行时字段（不序列化）────────────────────────────
    #: 引用的字形库找不到（打开时置位，界面提示重新选择）
    unresolved_font: bool = False
    #: 打开/校验时发现的可恢复问题
    warnings: list[str] = field(default_factory=list)
    #: 所在文件路径（保存后回填）
    file_path: str = ""
    #: 是否有未保存改动
    dirty: bool = False

    # ── 便捷 ─────────────────────────────────────────────
    def grid_for(self, page_index: int = 0) -> GridSpec:
        """取某页的网格参数。apply_to_all_pages 时所有页共用第 0 页的。

        ★ 这样调用方不必关心是不是逐页设置，统一走这个方法。
        """
        if self.apply_to_all_pages:
            if self.pages:
                return self.pages[0].grid
            return GridSpec()
        for ps in self.pages:
            if ps.index == page_index:
                return ps.grid
        return GridSpec()

    def ensure_pages(self, count: int = 1) -> None:
        """保证 pages 列表至少有 count 项（缺的用默认补）。"""
        existing = {ps.index for ps in self.pages}
        for i in range(count):
            if i not in existing:
                self.pages.append(PageSettings(index=i))
        self.page_count = max(self.page_count, count)

    def page_numbers(self) -> list[int]:
        """要导出的页号列表。"""
        if self.export_pages == "all":
            return list(range(self.page_count))
        if self.export_pages == "current":
            return [0]
        # 支持 "1,3,5" 这种
        try:
            return [int(x.strip()) - 1 for x in str(self.export_pages).split(",")
                    if x.strip().isdigit()]
        except (ValueError, AttributeError):
            return list(range(self.page_count))

    def mark_dirty(self) -> None:
        self.dirty = True
        self.touch()

    def mark_clean(self) -> None:
        self.dirty = False

    def touch(self) -> None:
        from datetime import datetime
        self.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    def display_name(self) -> str:
        return self.name or "未命名项目"

    # ── 序列化 ───────────────────────────────────────────
    def to_dict(self) -> dict:
        """构造 project.json 的内容。

        ★ 只写持久化字段，不写运行时字段。
        """
        return {
            "format": "hwpj",
            "version": 1,
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "app_version": self.app_version,
            "font": {
                "id": self.font_id,
                "name": self.font_name,
                "embedded": self.font_embedded,
            },
            "template": {
                "original_path": self.template_original_path,
                "file": self.template_path,
                "page_count": self.page_count,
                "apply_to_all_pages": self.apply_to_all_pages,
            },
            "pages": [ps.to_dict() for ps in self.pages],
            "content": self.content.to_dict(),
            "jitter": self.jitter.to_dict(),
            "seed": self.seed,
            "export": {
                "dpi": self.export_dpi,
                "pages": self.export_pages,
                "output_dir": self.export_dir,
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        d = d or {}
        font = d.get("font") or {}
        tpl = d.get("template") or {}
        exp = d.get("export") or {}
        p = cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            font_id=str(font.get("id", "")),
            font_name=str(font.get("name", "")),
            font_embedded=bool(font.get("embedded", False)),
            template_path=str(tpl.get("file", "")),
            template_original_path=str(tpl.get("original_path", "")),
            page_count=int(tpl.get("page_count", 1)),
            apply_to_all_pages=bool(tpl.get("apply_to_all_pages", True)),
            pages=[PageSettings.from_dict(x) for x in (d.get("pages") or [])],
            content=ContentSettings.from_dict(d.get("content") or {}),
            jitter=JitterSpec.from_dict(d.get("jitter") or {}),
            seed=d.get("seed"),
            export_dpi=int(exp.get("dpi", 300)),
            export_pages=str(exp.get("pages", "all")),
            export_dir=str(exp.get("output_dir") or ""),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
            app_version=str(d.get("app_version", "")),
        )
        return p


# ─────────────────────────────────────────────────────────────
# 最近项目列表项
# ─────────────────────────────────────────────────────────────


@dataclass
class RecentEntry:
    """最近项目列表项。"""

    path: str
    name: str = ""
    font_name: str = ""
    page_count: int = 1
    updated_at: str = ""
    exists: bool = True

    @property
    def label(self) -> str:
        return self.name or self.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]

    @property
    def subtitle(self) -> str:
        bits = []
        if self.font_name:
            bits.append(f"字形：{self.font_name}")
        if self.page_count:
            bits.append(f"{self.page_count} 页")
        if not self.exists:
            bits.append("文件已不存在")
        return " · ".join(bits)
