"""内容服务 —— 手动录入 + 随机生成（L3 服务层）。

设计依据：
- 模块设计说明书 §4.6
- 数据格式规范 §2.4、§2.6
- 接口契约与测试用例 §7

两条路：
1. **手动录入**：用户一格一格敲，或从 CSV/TSV 导入
2. **随机生成**：按 RandomSpec 生成（金额、数量、编号…）

★ 随机可复现：同 seed 同规格 → 同结果（契约 C5 的延伸）。
★ 生成的内容一定是**可打印字符**：不在 12 字符集里的会被过滤，
  并在结果里报告，让界面能提示「有 3 个字符没字形，已跳过」。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import charset as cs
from ..errors import ContentEmptyError
from ..models.content import ContentMatrix, ContentStats, ContentSpec, RandomSpec
from ..models.grid import GridSpec

#: 导入表格时允许的最大行列数（防有人塞进来一个巨大的 csv）
MAX_IMPORT_ROWS = 2000
MAX_IMPORT_COLS = 100


# ─────────────────────────────────────────────────────────────
# 随机生成
# ─────────────────────────────────────────────────────────────


class _Rng:
    """内容生成用的小发生器。

    ★ 架构禁令 N5 / 契约 C5：`core/` 下不得 import stdlib 的 random，
      统一走 `np.random.default_rng`。这里做一层薄封装，
      把需要的三种取数方式（整数 / 浮点 / 从列表挑）暴露成简单方法，
      免得调用点到处写 numpy 的 API。
    """

    __slots__ = ("_g",)

    def __init__(self, seed: int | None) -> None:
        self._g = np.random.default_rng(seed)

    def randint(self, lo: int, hi: int) -> int:
        """闭区间随机整数 [lo, hi]（与 random.randint 语义一致）。"""
        if lo > hi:
            lo, hi = hi, lo
        return int(self._g.integers(lo, hi + 1))

    def uniform(self, lo: float, hi: float) -> float:
        if lo > hi:
            lo, hi = hi, lo
        return float(self._g.uniform(lo, hi))

    def choice(self, seq: list[Any]) -> Any:
        return seq[int(self._g.integers(0, len(seq)))]


def _rng(seed: int | None) -> _Rng:
    """给随机生成用的发生器。

    ★ 用独立实例而不是全局发生器 —— 保证「同种子同结果」，
      也不受其他代码调用的影响。
    """
    return _Rng(seed)


def generate_value(spec: RandomSpec, rng: _Rng) -> str:
    """按规格生成一个值。"""
    t = spec.type

    if t == "integer":
        lo = int(round(spec.min))
        hi = int(round(spec.max))
        return str(rng.randint(lo, hi))

    if t == "decimal":
        d = max(0, min(6, int(spec.decimals)))
        return f"{rng.uniform(spec.min, spec.max):.{d}f}"

    if t == "pick":
        if not spec.candidates:
            return ""
        return str(rng.choice(list(spec.candidates)))

    # 不认识的类型：退回小数
    d = max(0, min(6, int(spec.decimals)))
    return f"{rng.uniform(spec.min, spec.max):.{d}f}"


def _random_text(spec: RandomSpec, rng: _Rng) -> str:
    """生成「可能带前导零/位数控制」的文本。

    位数控制只对 integer 生效（金额、编号类场景）。
    """
    base = generate_value(spec, rng)

    if spec.type == "integer" and spec.length_max > 0:
        want = rng.randint(max(1, spec.length_min), max(1, spec.length_max))
        digits = base.lstrip("-")
        if len(digits) < want:
            digits = digits.rjust(want, "0")      # 补前导零
        elif len(digits) > want:
            digits = digits[:want]
        base = ("-" + digits) if base.startswith("-") else digits

    return base


def generate_matrix(
    spec: ContentSpec,
    grid: GridSpec,
    seed: int | None = None,
    *,
    keep_existing: ContentMatrix | None = None,
) -> ContentMatrix:
    """按规格生成整个内容矩阵。

    Args:
        spec: 内容规格（含按列覆盖）
        grid: 网格参数（决定形状与哪些列要填）
        seed: 随机种子。None 表示真随机（每次不同）
        keep_existing: 已有内容。**非空的格子会保留**，
                       只填空格 —— 免得把用户手敲的内容覆盖掉。

    Returns:
        ContentMatrix，形状恒为 (grid.rows, grid.cols)。
    """
    rng = _rng(seed)
    rows, cols = grid.rows, grid.cols
    active = set(grid.effective_cols)

    mat = ContentMatrix.empty(rows, cols)

    for r in range(rows):
        for c in range(cols):
            if c not in active:
                continue                                  # 非激活列留空
            if keep_existing is not None and keep_existing.at(r, c):
                mat.set(r, c, keep_existing.at(r, c))
                continue
            col_spec = spec.spec_for_col(c)
            mat.set(r, c, _random_text(col_spec, rng))

    return mat


# ─────────────────────────────────────────────────────────────
# 手动录入
# ─────────────────────────────────────────────────────────────


def empty_matrix(grid: GridSpec, keep_existing: ContentMatrix | None = None) -> ContentMatrix:
    """建一个空矩阵（保留已有内容的形状）。"""
    if keep_existing is not None:
        m = keep_existing.copy()
        if m.rows != grid.rows or m.cols != grid.cols:
            m = ContentMatrix.empty(grid.rows, grid.cols)
        return m
    return ContentMatrix.empty(grid.rows, grid.cols)


def resize_matrix(matrix: ContentMatrix, grid: GridSpec) -> ContentMatrix:
    """网格改行列数后，把已有内容搬过去（能留的留下）。"""
    out = ContentMatrix.empty(grid.rows, grid.cols)
    for r in range(min(matrix.rows, grid.rows)):
        for c in range(min(matrix.cols, grid.cols)):
            out.set(r, c, matrix.at(r, c))
    return out


def fill_down(matrix: ContentMatrix, row: int, col: int) -> ContentMatrix:
    """把某格的值填到该列下面的所有格（表格常见操作：一个值重复到底）。"""
    new = matrix.copy()
    v = matrix.at(row, col)
    for r in range(row, matrix.rows):
        new.set(r, col, v)
    return new


def fill_right(matrix: ContentMatrix, row: int, col: int) -> ContentMatrix:
    """把某格的值填到该行右边的所有格。"""
    new = matrix.copy()
    v = matrix.at(row, col)
    for c in range(col, matrix.cols):
        new.set(row, c, v)
    return new


def clear_row(matrix: ContentMatrix, row: int) -> ContentMatrix:
    new = matrix.copy()
    for c in range(matrix.cols):
        new.set(row, c, "")
    return new


def clear_all(matrix: ContentMatrix) -> ContentMatrix:
    return ContentMatrix.empty(matrix.rows, matrix.cols)


def clear_col(matrix: ContentMatrix, col: int) -> ContentMatrix:
    new = matrix.copy()
    for r in range(matrix.rows):
        new.set(r, col, "")
    return new


# ─────────────────────────────────────────────────────────────
# 导入 / 导出（TSV / CSV）
# ─────────────────────────────────────────────────────────────


def parse_table(text: str, *, delimiter: str | None = None) -> list[list[str]]:
    """解析粘贴进来的表格数据。

    自动识别分隔符：制表符 > 逗号 > 分号。
    ★ 从 Excel / WPS 复制出来的数据是**制表符**分隔的，这是主场景。
    """
    text = (text or "").strip("\n\r")
    if not text.strip():
        return []

    if delimiter is None:
        first = text.splitlines()[0]
        if "\t" in first:
            delimiter = "\t"
        elif "," in first:
            delimiter = ","
        elif ";" in first:
            delimiter = ";"
        else:
            delimiter = "\t"

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [list(r) for r in reader]
    return rows


def matrix_from_table(
    rows: list[list[str]],
    template: GridSpec,
    *,
    keep_existing: ContentMatrix | None = None,
    fill_missing_from: ContentMatrix | None = None,
) -> ContentMatrix:
    """把解析出来的表格变成 ContentMatrix。

    超出网格的部分会被截断（并可由调用方提示用户）。
    """
    r_count = min(len(rows), MAX_IMPORT_ROWS, template.rows)
    out = ContentMatrix.empty(template.rows, template.cols)

    for r in range(r_count):
        row = rows[r]
        for c in range(min(len(row), MAX_IMPORT_COLS, template.cols)):
            out.set(r, c, (row[c] or "").strip())

    # 没被表格覆盖到的格子：保留原有内容
    if keep_existing is not None or fill_missing_from is not None:
        src = keep_existing or fill_missing_from
        for r in range(template.rows):
            for c in range(template.cols):
                if not out.at(r, c):
                    out.set(r, c, src.at(r, c))

    return out


def import_from_text(
    text: str,
    grid: GridSpec,
    *,
    keep_existing: ContentMatrix | None = None,
) -> tuple[ContentMatrix, list[str]]:
    """从粘贴文本导入。返回 (矩阵, 警告列表)。"""
    rows = parse_table(text)
    warnings: list[str] = []

    if not rows:
        raise ContentEmptyError(
            "没有识别到任何内容。",
            hint="请从表格软件里选中区域复制，再粘贴到这里。",
        )

    if len(rows) > grid.rows:
        warnings.append(
            f"粘贴了 {len(rows)} 行，超出网格的 {grid.rows} 行，多出的部分已忽略。"
        )
    max_cols = max((len(r) for r in rows), default=0)
    if max_cols > grid.cols:
        warnings.append(
            f"粘贴的内容有 {max_cols} 列，超出网格的 {grid.cols} 列，多出的部分已忽略。"
        )

    mat = matrix_from_table(rows, grid, keep_existing=keep_existing)
    return mat, warnings


def import_from_file(
    path: str | Path,
    grid: GridSpec,
    *,
    keep_existing: ContentMatrix | None = None,
) -> tuple[ContentMatrix, list[str]]:
    """从 .csv / .txt 文件导入。"""
    p = Path(path)
    warnings: list[str] = []
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            raw = p.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError as e:
            raise ContentEmptyError(f"读不了这个文件：{e}") from e

    if raw is None:
        raise ContentEmptyError(
            "文件编码不认识。",
            hint="请另存为 UTF-8 或 GBK 编码的文本文件再试。",
        )
    return import_from_text(raw, grid, keep_existing=keep_existing)


def export_to_tsv(matrix: ContentMatrix) -> str:
    """导出成制表符分隔文本（可以直接粘回 Excel）。"""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    for row in matrix.data:
        w.writerow(row)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
# 字符集检查（★ 契约 C9~C11：超范围跳过而不是报错）
# ─────────────────────────────────────────────────────────────


@dataclass
class CharsetReport:
    """内容里出现了哪些不可打印字符。"""

    unsupported: dict[str, int] = field(default_factory=dict)
    total_unsupported: int = 0
    affected_cells: int = 0

    @property
    def has_issue(self) -> bool:
        return bool(self.unsupported)

    @property
    def text(self) -> str:
        if not self.unsupported:
            return ""
        items = "、".join(
            f"「{ch}」×{n}" for ch, n in sorted(self.unsupported.items(), key=lambda kv: -kv[1])
        )
        return f"有 {self.total_unsupported} 个字符不在支持范围内，导出时会留空：{items}"


def check_charset(matrix: ContentMatrix) -> CharsetReport:
    """扫描内容里有没有超出 12 字符集的字符。"""
    rep = CharsetReport()
    for r in range(matrix.rows):
        for c in range(matrix.cols):
            v = matrix.at(r, c)
            if not v:
                continue
            touched = False
            for ch in v:
                if not cs.is_supported(ch):
                    rep.unsupported[ch] = rep.unsupported.get(ch, 0) + 1
                    rep.total_unsupported += 1
                    touched = True
            if touched:
                rep.affected_cells += 1
    return rep


def sanitize(matrix: ContentMatrix, *, report: bool = True) -> tuple[ContentMatrix, CharsetReport]:
    """把内容里不受支持的字符去掉。

    ★ 契约 C10：**跳过并留空，不报错、不静默丢弃**。
      返回的报告让界面能告诉用户「有几个字符被略过了」。
    """
    rep = CharsetReport()
    out = matrix.copy()
    for r in range(matrix.rows):
        for c in range(matrix.cols):
            v = matrix.at(r, c)
            if not v:
                continue
            kept, dropped = cs.filter_supported(v)
            for ch in dropped:
                rep.unsupported[ch] = rep.unsupported.get(ch, 0) + 1
                rep.total_unsupported += 1
            if dropped:
                rep.affected_cells += 1
            out.set(r, c, kept)
    return out, rep


def stats(matrix: ContentMatrix) -> ContentStats:
    return matrix.stats()


def preview_text(matrix: ContentMatrix, *, limit_rows: int = 8, limit_cols: int = 8) -> str:
    """生成一段给人看的预览文字（界面侧栏用）。"""
    lines: list[str] = []
    for r in range(min(matrix.rows, limit_rows)):
        cells = [matrix.at(r, c) or "·" for c in range(min(matrix.cols, limit_cols))]
        lines.append("  ".join(cells))
    if matrix.rows > limit_rows:
        lines.append(f"… 还有 {matrix.rows - limit_rows} 行")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 常用规格预设（界面上的快捷按钮）
# ─────────────────────────────────────────────────────────────


def preset_specs() -> dict[str, tuple[str, RandomSpec]]:
    """常用随机规格的预设。界面照这个生成按钮。"""
    return {
        "amount": ("金额（带小数）", RandomSpec(type="decimal", min=10.0, max=999.0, decimals=2)),
        "amount_small": ("小金额", RandomSpec(type="decimal", min=1.0, max=99.0, decimals=1)),
        "strike": ("体温 36~37", RandomSpec(type="decimal", min=36.0, max=37.0, decimals=1)),
        "count": ("数量（整数）", RandomSpec(type="integer", min=1, max=99, decimals=0)),
        "id4": ("编号（4 位，补零）", RandomSpec(type="integer", min=0, max=9999,
                                                 decimals=0, length_min=4, length_max=4)),
        "phone_last": ("电话号码后 8 位", RandomSpec(type="integer", min=0, max=99999999,
                                                      decimals=0, length_min=8, length_max=8)),
    }
