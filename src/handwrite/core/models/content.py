"""内容数据模型。

设计依据：
- 数据格式规范 §2.4（content 对象）、§2.6（content.tsv）
- 模块设计说明书 §5.4

关键不变量：
- ``ContentMatrix`` 的形状**恒等于** (grid.rows, grid.cols)
- 非激活列的值是空串 ""
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────
# 随机生成规格
# ─────────────────────────────────────────────────────────────

#: 支持的随机类型
RANDOM_TYPES = ("integer", "decimal", "pick")


@dataclass
class RandomSpec:
    """随机内容生成规格。"""

    type: str = "decimal"          # integer | decimal | pick
    min: float = 36.0
    max: float = 37.0
    decimals: int = 1
    length_min: int = 2
    length_max: int = 4
    #: type == "pick" 时的候选列表
    candidates: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        w: list[str] = []
        if self.type not in RANDOM_TYPES:
            w.append(f"不认识的随机类型「{self.type}」，将按小数处理")
        if self.min > self.max:
            w.append("最小值大于最大值，已自动交换")
        if self.decimals < 0 or self.decimals > 6:
            w.append(f"小数位数 {self.decimals} 超出范围（0~6），已箝位")
        if self.length_min < 1:
            w.append("最短位数至少为 1")
        if self.length_min > self.length_max:
            w.append("最短位数大于最长位数，已交换")
        if self.type == "pick" and not self.candidates:
            w.append("候选列表为空，将无法生成内容")
        return w

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "min": self.min,
            "max": self.max,
            "decimals": self.decimals,
            "length_min": self.length_min,
            "length_max": self.length_max,
            "candidates": list(self.candidates),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RandomSpec":
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


@dataclass
class ContentSpec:
    """内容来源设置（对应 project.json 的 content 对象）。"""

    mode: str = "random"                     # random | manual
    random: RandomSpec = field(default_factory=RandomSpec)
    #: 按列覆盖随机规则：{列下标: RandomSpec}。空 = 所有列同规则
    per_column: dict[int, RandomSpec] = field(default_factory=dict)
    manual_file: str | None = None

    def spec_for_col(self, col: int) -> RandomSpec:
        """取某列该用的随机规格（有按列覆盖就用覆盖的）。"""
        return self.per_column.get(col, self.random)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "random": self.random.to_dict(),
            "per_column": {str(k): v.to_dict() for k, v in self.per_column.items()},
            "manual_file": self.manual_file,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContentSpec":
        s = cls()
        raw = d or {}
        s.mode = raw.get("mode", "random")
        s.random = RandomSpec.from_dict(raw.get("random") or {})
        s.per_column = {
            int(k): RandomSpec.from_dict(v)
            for k, v in (raw.get("per_column") or {}).items()
        }
        s.manual_file = raw.get("manual_file")
        return s


# ─────────────────────────────────────────────────────────────
# 内容矩阵
# ─────────────────────────────────────────────────────────────


@dataclass
class ContentMatrix:
    """要填进表格的内容矩阵。

    形状**恒为** (rows, cols)，非激活列是空串。
    """

    rows: int
    cols: int
    data: list[list[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 补齐/截断到 (rows, cols) —— 构造即保证不变量
        normalized: list[list[str]] = []
        for r in range(self.rows):
            row = list(self.data[r]) if r < len(self.data) else []
            if len(row) < self.cols:
                row.extend([""] * (self.cols - len(row)))
            elif len(row) > self.cols:
                row = row[: self.cols]
            normalized.append(row)
        self.data = normalized

    # ── 访问 ─────────────────────────────────────────────
    def at(self, row: int, col: int) -> str:
        """取一格内容，越界返回空串（不抛异常，界面高频调用）。"""
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return self.data[row][col]
        return ""

    def set(self, row: int, col: int, value: str) -> None:
        """写一格。越界静默忽略（界面编辑时可能拿到过时坐标）。"""
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.data[row][col] = "" if value is None else str(value)

    def row_values(self, row: int) -> list[str]:
        if 0 <= row < self.rows:
            return self.data[row]
        return [""] * self.cols

    def col_values(self, col: int) -> list[str]:
        return [self.at(r, col) for r in range(self.rows)]

    # ── 统计 ─────────────────────────────────────────────
    def non_empty(self) -> int:
        """非空格子数。"""
        return sum(1 for row in self.data for v in row if v)

    @property
    def total_cells(self) -> int:
        return self.rows * self.cols

    @property
    def empty_ratio(self) -> float:
        if self.total_cells == 0:
            return 0.0
        return 1.0 - self.non_empty() / self.total_cells

    def char_total(self) -> int:
        """所有格子的字符总数。"""
        return sum(len(v) for row in self.data for v in row)

    def max_len(self) -> int:
        return max((len(v) for row in self.data for v in row), default=0)

    def all_texts(self) -> list[str]:
        return [v for row in self.data for v in row if v]

    def stats(self) -> "ContentStats":
        return ContentStats(
            rows=self.rows,
            cols=self.cols,
            filled=self.non_empty(),
            empty=self.total_cells - self.non_empty(),
            chars=self.char_total(),
            max_len=self.max_len(),
        )

    # ── 转换 ─────────────────────────────────────────────
    def to_plain_matrix(self) -> list[list[str]]:
        """返回二维列表的**拷贝**（防止调用方改到内部数据）。"""
        return [list(r) for r in self.data]

    def copy(self) -> "ContentMatrix":
        return ContentMatrix(self.rows, self.cols, self.to_plain_matrix())

    def with_value(self, row: int, col: int, value: str) -> "ContentMatrix":
        """返回一个改了某格的**新**矩阵。"""
        new = self.copy()
        new.set(row, col, value)
        return new

    @classmethod
    def empty(cls, rows: int, cols: int) -> "ContentMatrix":
        return cls(rows, cols, [])

    @classmethod
    def from_matrix(cls, data: list[list[str]], rows: int, cols: int) -> "ContentMatrix":
        return cls(rows, cols, data)


# ─────────────────────────────────────────────────────────────
# 统计
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContentStats:
    """内容统计（界面状态栏显示）。"""

    rows: int = 0
    cols: int = 0
    filled: int = 0
    empty: int = 0
    chars: int = 0
    max_len: int = 0

    @property
    def summary(self) -> str:
        return (
            f"{self.rows} 行 × {self.cols} 列 · "
            f"已填 {self.filled} 格 · 共 {self.chars} 字"
        )
