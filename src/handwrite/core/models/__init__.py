"""数据模型层 —— 跨模块传递的唯一合法类型。

设计依据：模块设计说明书 §5（数据对象定义）

规则：
- 全部是纯 dataclass，**不继承任何 Qt 类**
- 只依赖 base 层（geometry / charset / config）
- 服务层之间只靠这些对象说话，不靠文件句柄、不靠 Qt 类型
"""

from .glyph import CoverageReport, FontLibrary, FontSummary, GlyphSample, GlyphSet
from .template import PageGeometry, TemplateDoc, TemplatePage
from .grid import CellRect, GridSpec
from .content import ContentMatrix, ContentSpec, ContentStats, RandomSpec
from .jitter import (
    CellJitter,
    CharPosJitter,
    CharShapeJitter,
    DotJitter,
    JitterSpec,
    JITTER_PRESETS,
    PRESET_LABELS,
)
from .project import ContentSettings, PageSettings, Project, RecentEntry

__all__ = [
    # glyph
    "FontLibrary", "GlyphSample", "GlyphSet", "CoverageReport", "FontSummary",
    # template
    "TemplateDoc", "TemplatePage", "PageGeometry",
    # grid
    "GridSpec", "CellRect",
    # content
    "ContentMatrix", "ContentSpec", "RandomSpec", "ContentStats",
    # jitter
    "JitterSpec", "JITTER_PRESETS", "PRESET_LABELS",
    "CellJitter", "CharPosJitter", "CharShapeJitter", "DotJitter",
    # project
    "Project", "PageSettings", "ContentSettings", "RecentEntry",
]
