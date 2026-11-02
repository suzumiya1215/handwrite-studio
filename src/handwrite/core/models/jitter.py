"""抖动参数模型与预设。

设计依据：
- 数据格式规范 §2.5（jitter 对象与三档预设取值表）
- 模块设计说明书 §5.5

四层抖动（顺序不可乱，这是产品精髓）：
    1. 单字形变（char_shape）—— 大小、拉伸、倾斜、加粗
    2. 单字位置（char_pos）  —— 偏移、字间距
    3. 整格变换（cell）      —— 整组字一起平移 + 旋转
    4. 小数点（dot）         —— 位置、大小单独再抖一次
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

PRESETS = ("mild", "natural", "strong", "custom")


# ─────────────────────────────────────────────────────────────
# 四组参数
# ─────────────────────────────────────────────────────────────


@dataclass
class CellJitter:
    """第 3 层：整格变换。"""

    offset_x: float = 0.08      # 单元宽比例
    offset_y: float = 0.08      # 单元高比例
    rotate_deg: float = 3.0     # 实际取 ±rotate_deg 均匀分布

    def clamp(self) -> tuple["CellJitter", list[str]]:
        w: list[str] = []
        if not (0 <= self.offset_x <= 0.5):
            self.offset_x = min(max(self.offset_x, 0.0), 0.5)
            w.append("整格水平偏移超出范围（0~0.5），已箝位")
        if not (0 <= self.offset_y <= 0.5):
            self.offset_y = min(max(self.offset_y, 0.0), 0.5)
            w.append("整格垂直偏移超出范围（0~0.5），已箝位")
        if not (0 <= self.rotate_deg <= 15):
            self.rotate_deg = min(max(self.rotate_deg, 0.0), 15.0)
            w.append("整格旋转超出范围（0~15 度），已箝位")
        return self, w


@dataclass
class CharPosJitter:
    """第 2 层：单字位置。"""

    offset_x: float = 0.10
    offset_y: float = 0.10
    spacing: float = 0.08

    def clamp(self) -> tuple["CharPosJitter", list[str]]:
        w: list[str] = []
        for name, val in (("水平偏移", self.offset_x), ("垂直偏移", self.offset_y)):
            if not (0 <= val <= 1.0):
                w.append(f"单字{name}超出范围（0~1.0），已箝位")
        if not (0 <= self.spacing <= 0.5):
            w.append("字间距抖动超出范围（0~0.5），已箝位")
        self.offset_x = min(max(self.offset_x, 0.0), 1.0)
        self.offset_y = min(max(self.offset_y, 0.0), 1.0)
        self.spacing = min(max(self.spacing, 0.0), 0.5)
        return self, w


@dataclass
class CharShapeJitter:
    """第 1 层：单字形变。"""

    scale_min: float = 0.85
    scale_max: float = 1.15
    bold_px_max: float = 1.0
    slant_deg: float = 6.0
    stretch_x_min: float = 0.92
    stretch_x_max: float = 1.08
    stretch_y_min: float = 0.92
    stretch_y_max: float = 1.08

    def clamp(self) -> tuple["CharShapeJitter", list[str]]:
        w: list[str] = []
        # 缩放
        if self.scale_min > self.scale_max:
            self.scale_min, self.scale_max = self.scale_max, self.scale_min
            w.append("字号缩放的最小值大于最大值，已交换")
        if not (0.4 <= self.scale_min and self.scale_max <= 2.0):
            self.scale_min = min(max(self.scale_min, 0.4), 2.0)
            self.scale_max = min(max(self.scale_max, 0.4), 2.0)
            w.append("字号缩放超出范围（0.4~2.0），已箝位")
        # 加粗
        if not (0 <= self.bold_px_max <= 3):
            self.bold_px_max = min(max(self.bold_px_max, 0.0), 3.0)
            w.append("笔画加粗超出范围（0~3 px），已箝位")
        # 倾斜
        if not (0 <= self.slant_deg <= 20):
            self.slant_deg = min(max(self.slant_deg, 0.0), 20.0)
            w.append("单字倾斜超出范围（0~20 度），已箝位")
        # 拉伸
        for axis, lo, hi in (
            ("水平", self.stretch_x_min, self.stretch_x_max),
            ("垂直", self.stretch_y_min, self.stretch_y_max),
        ):
            if lo > hi:
                w.append(f"{axis}拉伸的最小值大于最大值，已交换")
        if self.stretch_x_min > self.stretch_x_max:
            self.stretch_x_min, self.stretch_x_max = self.stretch_x_max, self.stretch_x_min
        if self.stretch_y_min > self.stretch_y_max:
            self.stretch_y_min, self.stretch_y_max = self.stretch_y_max, self.stretch_y_min
        self.stretch_x_min = min(max(self.stretch_x_min, 0.5), 1.5)
        self.stretch_x_max = min(max(self.stretch_x_max, 0.5), 1.5)
        self.stretch_y_min = min(max(self.stretch_y_min, 0.5), 1.5)
        self.stretch_y_max = min(max(self.stretch_y_max, 0.5), 1.5)
        return self, w


@dataclass
class DotJitter:
    """第 4 层：小数点（``.`` 与 ``,``）专用。

    ⚠️ 命名说明：这里的 ``dot`` 指**抖动层次**（小数点抖动），
       与字形库里存放 ``.`` 的目录名 ``dot`` **只是恰好同名**，两者无关。
       （架构规则测试 N4 已针对此做了精确化处理，不会误报。）
    """

    offset: float = 0.10
    scale_min: float = 0.80
    scale_max: float = 1.10

    def clamp(self) -> tuple["DotJitter", list[str]]:
        w: list[str] = []
        if not (0 <= self.offset <= 0.3):
            self.offset = min(max(self.offset, 0.0), 0.3)
            w.append("小数点位置抖动超出范围（0~0.3），已箝位")
        if self.scale_min > self.scale_max:
            self.scale_min, self.scale_max = self.scale_max, self.scale_min
            w.append("小数点缩放的最小值大于最大值，已交换")
        self.scale_min = min(max(self.scale_min, 0.3), 1.5)
        self.scale_max = min(max(self.scale_max, 0.3), 1.5)
        return self, w


# ─────────────────────────────────────────────────────────────
# 三档预设取值表（照抄数据格式规范 §2.5，不得随意改）
# ─────────────────────────────────────────────────────────────


JITTER_PRESETS: dict[str, dict] = {
    "mild": {
        "cell":      {"offset_x": 0.03, "offset_y": 0.03, "rotate_deg": 1.0},
        "char_pos":  {"offset_x": 0.03, "offset_y": 0.03, "spacing": 0.03},
        "char_shape": {"scale_min": 0.95, "scale_max": 1.05, "bold_px_max": 0.5,
                       "slant_deg": 2.0,
                       "stretch_x_min": 0.97, "stretch_x_max": 1.03,
                       "stretch_y_min": 0.97, "stretch_y_max": 1.03},
        "dot":       {"offset": 0.03, "scale_min": 0.92, "scale_max": 1.05},
    },
    "natural": {
        "cell":      {"offset_x": 0.08, "offset_y": 0.08, "rotate_deg": 3.0},
        "char_pos":  {"offset_x": 0.10, "offset_y": 0.10, "spacing": 0.08},
        "char_shape": {"scale_min": 0.85, "scale_max": 1.15, "bold_px_max": 1.0,
                       "slant_deg": 6.0,
                       "stretch_x_min": 0.92, "stretch_x_max": 1.08,
                       "stretch_y_min": 0.92, "stretch_y_max": 1.08},
        "dot":       {"offset": 0.10, "scale_min": 0.80, "scale_max": 1.10},
    },
    "strong": {
        "cell":      {"offset_x": 0.16, "offset_y": 0.16, "rotate_deg": 6.0},
        "char_pos":  {"offset_x": 0.20, "offset_y": 0.20, "spacing": 0.16},
        "char_shape": {"scale_min": 0.75, "scale_max": 1.28, "bold_px_max": 1.5,
                       "slant_deg": 12.0,
                       "stretch_x_min": 0.85, "stretch_x_max": 1.18,
                       "stretch_y_min": 0.85, "stretch_y_max": 1.18},
        "dot":       {"offset": 0.20, "scale_min": 0.65, "scale_max": 1.25},
    },
}

#: 中文档位名（界面显示）
PRESET_LABELS: dict[str, str] = {
    "mild": "轻微",
    "natural": "自然",
    "strong": "明显",
    "custom": "自定义",
}


# ─────────────────────────────────────────────────────────────
# 总参数
# ─────────────────────────────────────────────────────────────


@dataclass
class JitterSpec:
    """四层抖动的总参数。"""

    preset: str = "natural"
    cell: CellJitter = field(default_factory=CellJitter)
    char_pos: CharPosJitter = field(default_factory=CharPosJitter)
    char_shape: CharShapeJitter = field(default_factory=CharShapeJitter)
    dot: DotJitter = field(default_factory=DotJitter)

    # ── 预设 ─────────────────────────────────────────────
    @staticmethod
    def from_preset(name: str) -> "JitterSpec":
        """由预设名构造。不认识的预设退回 natural。"""
        p = JITTER_PRESETS.get(name)
        if p is None:
            name, p = "natural", JITTER_PRESETS["natural"]
        return JitterSpec(
            preset=name,
            cell=CellJitter(**p["cell"]),
            char_pos=CharPosJitter(**p["char_pos"]),
            char_shape=CharShapeJitter(**p["char_shape"]),
            dot=DotJitter(**p["dot"]),
        )

    def apply_preset(self, name: str) -> "JitterSpec":
        """返回应用了某预设的**新**参数。"""
        return JitterSpec.from_preset(name)

    def as_custom(self) -> "JitterSpec":
        """标记为自定义（用户手动改过任一参数后调用）。"""
        if self.preset != "custom":
            self.preset = "custom"
        return self

    @property
    def preset_label(self) -> str:
        return PRESET_LABELS.get(self.preset, self.preset)

    def matches_preset(self, name: str) -> bool:
        """当前取值是否与某预设完全一致。"""
        p = JITTER_PRESETS.get(name)
        if p is None:
            return False
        ref = JitterSpec.from_preset(name)
        return (
            self.cell == ref.cell
            and self.char_pos == ref.char_pos
            and self.char_shape == ref.char_shape
            and self.dot == ref.dot
        )

    # ── 校验 ─────────────────────────────────────────────
    def clamp(self) -> tuple["JitterSpec", list[str]]:
        """越界箝位。返回 (自身, 警告列表)。

        ★ 越界不报错，箝位 + 警告（模块设计说明书 §7 降级表）。
        """
        warnings: list[str] = []
        for part in (self.cell, self.char_pos, self.char_shape, self.dot):
            _, w = part.clamp()
            warnings.extend(w)
        return self, warnings

    def validate(self) -> list[str]:
        """只检查不修改，返回警告列表。"""
        import copy
        probe = copy.deepcopy(self)
        _, w = probe.clamp()
        return w

    # ── 序列化 ───────────────────────────────────────────
    def to_dict(self) -> dict:
        def _d(o) -> dict:
            return {k: v for k, v in o.__dict__.items()}

        return {
            "preset": self.preset,
            "cell": _d(self.cell),
            "char_pos": _d(self.char_pos),
            "char_shape": _d(self.char_shape),
            "dot": _d(self.dot),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JitterSpec":
        d = d or {}
        if not d:
            return cls.from_preset("natural")
        # 只有 preset 没有其它字段 → 用预设展开
        if d.get("preset") and d.get("preset") != "custom" and "cell" not in d:
            return cls.from_preset(d["preset"])
        base = cls.from_preset("natural")
        base.preset = d.get("preset", "natural")
        if "cell" in d:
            base.cell = _build(CellJitter, d["cell"])
        if "char_pos" in d:
            base.char_pos = _build(CharPosJitter, d["char_pos"])
        if "char_shape" in d:
            base.char_shape = _build(CharShapeJitter, d["char_shape"])
        if "dot" in d:
            base.dot = _build(DotJitter, d["dot"])
        return base

    def copy(self) -> "JitterSpec":
        import copy as _copy
        return _copy.deepcopy(self)

    def with_change(self, **parts) -> "JitterSpec":
        """返回改了某些层的**新**参数，并标记为 custom。"""
        new = self.copy()
        for key, value in parts.items():
            setattr(new, key, value)
        return new.as_custom()


def _build(cls, d: dict):
    """按 dict 构造 dataclass，忽略未知键。"""
    obj = cls()
    for k, v in (d or {}).items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    return obj
