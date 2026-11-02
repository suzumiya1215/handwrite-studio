"""字形库数据模型。

设计依据：
- 数据格式规范 §1（.hwfont 格式）
- 模块设计说明书 §5.1

关键不变量：
- ``GlyphSet.count == len(GlyphSet.samples)``
- 字形图规格：RGBA、前景纯黑、背景 alpha=0、高 = glyph_height
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import charset as cs

# ─────────────────────────────────────────────────────────────
# 单个样本
# ─────────────────────────────────────────────────────────────


@dataclass
class GlyphSample:
    """一个字形的手写样本。

    Attributes:
        file: 相对 ``glyphs/<字符目录>/`` 的文件名（ASCII，如 ``001.png``）
        width: 原始像素宽（写进 manifest.sample_sizes）
        height: 原始像素高
        image: 懒加载的图像缓存。**不参与序列化** —— 加载时按需读盘，
               避免打开一个 12 字符的库就把几十张图全读进内存。
    """

    file: str
    width: int
    height: int
    image: Any = None            # PIL.Image.Image | None

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def aspect(self) -> float:
        """宽高比。标点类的样本靠它做排版。"""
        return self.width / self.height if self.height else 1.0

    def to_manifest_entry(self) -> dict:
        return {"file": self.file, "w": self.width, "h": self.height}


# ─────────────────────────────────────────────────────────────
# 一个字符的全部样本
# ─────────────────────────────────────────────────────────────


@dataclass
class GlyphSet:
    """一个字符（如 ``"3"`` / ``","``）的全部手写样本。"""

    char: str
    samples: list[GlyphSample] = field(default_factory=list)

    @property
    def count(self) -> int:
        """样本数。★ 必须与 len(samples) 一致（manifest 校验依赖它）。"""
        return len(self.samples)

    @property
    def is_empty(self) -> bool:
        return not self.samples

    def files(self) -> list[str]:
        return [s.file for s in self.samples]

    def sizes(self) -> list[list[int]]:
        """[[宽, 高], ...]，写进 manifest.sample_sizes。"""
        return [[s.width, s.height] for s in self.samples]

    def next_filename(self) -> str:
        """生成下一个可用的文件名（三位数字递增）。"""
        used = {s.file for s in self.samples}
        i = 1
        while f"{i:03d}.png" in used:
            i += 1
        return f"{i:03d}.png"

    def add(self, sample: GlyphSample) -> None:
        self.samples.append(sample)

    def remove(self, filename: str) -> bool:
        """按文件名删一个样本。返回是否删掉了。"""
        for i, s in enumerate(self.samples):
            if s.file == filename:
                del self.samples[i]
                return True
        return False

    def dirname(self) -> str:
        """对应的目录名（ASCII）。"""
        return cs.char_to_dirname(self.char)


# ─────────────────────────────────────────────────────────────
# 覆盖率报告
# ─────────────────────────────────────────────────────────────


@dataclass
class CoverageReport:
    """字形库对某个字符集的覆盖情况。"""

    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    total: int = 0

    @property
    def ratio(self) -> float:
        if self.total <= 0:
            return 0.0
        return len(self.present) / self.total

    @property
    def coverage_ratio(self) -> float:
        """ratio 的别名（测试用例里两种写法都有）。"""
        return self.ratio

    @property
    def percent_text(self) -> str:
        return f"{int(round(self.ratio * 100))}%"

    @property
    def display(self) -> str:
        """状态栏显示的文案，如 ``12/12``。"""
        return f"{len(self.present)}/{self.total}"

    @property
    def is_complete(self) -> bool:
        return not self.missing


# ─────────────────────────────────────────────────────────────
# 字形库
# ─────────────────────────────────────────────────────────────


@dataclass
class FontLibrary:
    """一套采集好的字形库（对应一个 ``.hwfont`` 文件）。

    字段与 manifest.json 一一对应（见数据格式规范 §1.2）。
    """

    id: str
    name: str
    charset: list[str] = field(default_factory=lambda: list(cs.CHARSET_ENABLED_V1))
    glyph_height: int = 128
    characters: dict[str, GlyphSet] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    baseline_offset: int = 0
    notes: str = ""
    app_version: str = ""

    #: 加载来源目录（新建的库为 None）
    root: str | None = None

    # ── 构造辅助 ──────────────────────────────────────────
    @classmethod
    def create(cls, name: str, lib_id: str, *, glyph_height: int = 128) -> "FontLibrary":
        """新建一个空库。"""
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        return cls(
            id=lib_id,
            name=name,
            charset=list(cs.CHARSET_ENABLED_V1),
            glyph_height=glyph_height,
            created_at=now,
            updated_at=now,
            app_version="",
        )

    # ── 查询 ─────────────────────────────────────────────
    def has(self, ch: str) -> bool:
        """该字符是否**有可用样本**。

        ★ FillService 和 render_page 的跳过计数都必须用这一个判据，
          否则会出现「计数说有、实际没填」的不一致。
        """
        gs = self.characters.get(ch)
        return gs is not None and not gs.is_empty

    def get(self, ch: str) -> GlyphSet | None:
        return self.characters.get(ch)

    def chars_present(self) -> list[str]:
        """按 v1 顺序返回已有样本的字符。"""
        return [c for c in cs.CHARSET_V1 if self.has(c)]

    def missing(self, chars) -> set[str]:
        """给定字符集中，本库缺失（无样本）的那些。"""
        return {c for c in chars if not self.has(c)}

    def coverage(self, chars: list[str] | None = None) -> CoverageReport:
        """覆盖率报告。默认针对 v1 全集。"""
        target = list(chars) if chars else list(cs.CHARSET_V1)
        present = [c for c in target if self.has(c)]
        missing = [c for c in target if not self.has(c)]
        return CoverageReport(present=present, missing=missing, total=len(target))

    def total_samples(self) -> int:
        return sum(gs.count for gs in self.characters.values())

    # ── 修改（返回新对象，不改原对象） ─────────────────────
    def copy(self) -> "FontLibrary":
        """深拷贝（样本的图像缓存也复制引用，图像本身不可变所以安全）。"""
        import copy as _copy
        return _copy.deepcopy(self)

    def with_sample(self, ch: str, sample: GlyphSample) -> "FontLibrary":
        """返回一个加了这个样本的**新**库（不可变更新）。"""
        new = self.copy()
        gs = new.characters.get(ch)
        if gs is None:
            gs = GlyphSet(char=ch)
            new.characters[ch] = gs
        gs.add(sample)
        new.touch()
        return new

    def without_sample(self, ch: str, filename: str) -> "FontLibrary":
        """返回一个删了这个样本的**新**库。"""
        new = self.copy()
        gs = new.characters.get(ch)
        if gs is not None:
            gs.remove(filename)
        new.touch()
        return new

    def touch(self) -> None:
        """刷新 updated_at。"""
        self.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    # ── 序列化 ───────────────────────────────────────────
    def to_manifest(self, *, app_version: str = "") -> dict:
        """构造 manifest.json 的内容。"""
        return {
            "format": "hwfont",
            "version": 1,
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "charset": list(self.charset),
            "glyph_height": self.glyph_height,
            "baseline_offset": self.baseline_offset,
            "characters": {
                ch: {"count": gs.count, "files": gs.files()}
                for ch, gs in self.characters.items()
            },
            "sample_sizes": {
                ch: gs.sizes()
                for ch, gs in self.characters.items()
                if gs.samples
            },
            "notes": self.notes,
            "app_version": app_version or self.app_version,
        }

    @classmethod
    def from_manifest(cls, m: dict, *, root: str | None = None) -> "FontLibrary":
        """从 manifest 内容构造（不做磁盘校验，校验在 FontService 里）。"""
        characters: dict[str, GlyphSet] = {}
        raw_chars = m.get("characters") or {}
        sizes = m.get("sample_sizes") or {}
        for ch, entry in raw_chars.items():
            files = list(entry.get("files") or [])
            size_list = sizes.get(ch) or []
            samples: list[GlyphSample] = []
            for i, fn in enumerate(files):
                if i < len(size_list):
                    w, h = size_list[i][0], size_list[i][1]
                else:
                    w = h = int(m.get("glyph_height", 128))
                samples.append(GlyphSample(file=fn, width=w, height=h))
            characters[ch] = GlyphSet(char=ch, samples=samples)

        return cls(
            id=str(m.get("id", "")),
            name=str(m.get("name", "")),
            charset=list(m.get("charset") or cs.CHARSET_ENABLED_V1),
            glyph_height=int(m.get("glyph_height", 128)),
            characters=characters,
            created_at=str(m.get("created_at", "")),
            updated_at=str(m.get("updated_at", "")),
            baseline_offset=int(m.get("baseline_offset", 0)),
            notes=str(m.get("notes", "")),
            app_version=str(m.get("app_version", "")),
            root=root,
        )


# ─────────────────────────────────────────────────────────────
# 列表页用的摘要
# ─────────────────────────────────────────────────────────────


@dataclass
class FontSummary:
    """字形库列表项（不加载全部字形图，只看 manifest）。"""

    id: str
    name: str
    path: str
    char_count: int = 0
    sample_count: int = 0
    coverage: str = ""          # 如 "12/12"
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_complete(self) -> bool:
        return self.coverage.endswith("/12") and self.coverage.startswith("12/")
