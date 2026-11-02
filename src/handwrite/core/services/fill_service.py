"""填充引擎 —— 项目的技术核心（L3 服务层）。

设计依据：
- 模块设计说明书 §4.7（FillService）
- 模块设计说明书 §6 契约 C5~C8（随机性）、C12（图像）
- 接口契约与测试用例 §9
- 需求文档 §6（四层抖动是产品精髓）

★ 最重要的一条约束：**这是一个纯函数**。
  render_page(位图, 格子, 内容, 字形库, 抖动, 种子) -> FillResult
  不持有任何服务、不读盘、不查配置。给它什么就画什么。
  这样它能脱离整个应用被单测，也是 M2/M3 能并行的技术前提。

四层抖动（顺序绝对不可乱）::

    ① 单字形变   挑样本 → 缩放/拉伸/倾斜/加粗
    ② 单字位置   每个字在格内微调位置 + 字间距
    ③ 整格变换   ★ 先把整格字符合成到一张透明图层，再整体平移+旋转
    ④ 小数点     . 和 , 单独再抖一次（位置贴近基线、尺寸更小）

★ ③ 为什么必须"先合再转"：
  如果逐字各自旋转，每个字会朝不同方向歪，看起来像散架的活字印刷。
  真实手写的一格里，字是"整组"歪的。

★ 随机怎么保证可复现：
  每格用独立的 `np.random.default_rng(seed + row*1000 + col)`。
  这样重掷某一格时，其他格子的随机序列完全不受影响。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image

from ..imageops import blank_rgba
from ..models.content import ContentMatrix
from ..models.glyph import FontLibrary, GlyphSample
from ..models.grid import CellRect
from ..models.jitter import JitterSpec

#: 一格里最多画多少个字（防有人往一格塞 500 个字符）
MAX_CHARS_PER_CELL = 64

#: 每格随机种子的行/列步长（契约 C6）。
#: 用 1000 而不是 1，避免 (1,0) 和 (0,1) 撞种子。
SEED_ROW_STRIDE = 1000
SEED_COL_STRIDE = 1

#: 小数点字符（第 4 层抖动只作用于它们）
DOT_CHARS = frozenset({".", ","})

#: 标点相对于**数字高度**的比例（排版时用）
#:
#: ★ 口径说明：这是「句号高度 ÷ 数字高度」，不是「÷ 整格高度」。
#:   真实手写的句号约是数字高度的 1/4~1/3；取 0.30 落在中间偏下，
#:   既不会小到看不见，也不会大到抢戏。
PUNCT_SCALE = 0.30

#: 标点基线位置 —— 相对于内容区高度的比例（0.86 ≈ 贴数字基线偏上一点）
PUNCT_BASELINE = 0.86

#: 标点两侧额外收紧的比例（相对内容区宽度）。
#:
#: 数字之间按正常字距排，但句号两侧要贴紧前一个数字 ——
#: 真实写法里 `20.6` 的点和 0、6 之间几乎没有空隙。
PUNCT_TIGHTEN = 0.012


# ─────────────────────────────────────────────────────────────
# 结果
# ─────────────────────────────────────────────────────────────


@dataclass
class FillResult:
    """一次渲染的结果。"""

    image: Image.Image
    filled_cells: int = 0
    skipped_chars: dict[str, int] = field(default_factory=dict)
    total_chars: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped_chars.values())

    @property
    def skipped_text(self) -> str:
        """给状态栏用的中文描述。"""
        if not self.skipped_chars:
            return ""
        items = "、".join(f"{ch}×{n}" for ch, n in sorted(self.skipped_chars.items()))
        return f"有 {self.skipped_total} 个字符没有字形，已跳过：{items}"


@dataclass
class CellPlan:
    """一个格子的绘制计划（先算后画，便于测试与调试）。"""

    row: int
    col: int
    cell: CellRect
    text: str
    #: 每个字符：(字符, 样本序号, 目标矩形px)
    items: list[tuple[str, int, tuple[float, float, float, float]]] = field(default_factory=list)
    cell_offset: tuple[float, float] = (0.0, 0.0)
    cell_rotate: float = 0.0
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def drawn_chars(self) -> int:
        return len(self.items)


# ─────────────────────────────────────────────────────────────
# 随机数（契约 C5~C7）
# ─────────────────────────────────────────────────────────────


def rng_for_cell(seed: int, row: int, col: int) -> np.random.Generator:
    """给某格派生一个独立的随机数发生器。

    ★ 用 default_rng（PCG64）而不是老的 np.random.seed 全局状态：
      全局状态在多格循环里会互相干扰，也无法保证顺序无关。
    """
    s = int(seed) + row * SEED_ROW_STRIDE + col * SEED_COL_STRIDE
    return np.random.default_rng(s)


def _uniform(rng: np.random.Generator, lo: float, hi: float) -> float:
    """均匀分布取值。lo == hi 时直接返回，避免 numpy 报错。"""
    if hi <= lo:
        return float(lo)
    return float(rng.uniform(lo, hi))


def _symmetric(rng: np.random.Generator, amp: float) -> float:
    """±amp 均匀分布。"""
    if amp <= 0:
        return 0.0
    return float(rng.uniform(-amp, amp))


# ─────────────────────────────────────────────────────────────
# 第一层：单字形变
# ─────────────────────────────────────────────────────────────


def _pick_sample(lib: FontLibrary, ch: str, rng: np.random.Generator) -> GlyphSample | None:
    """挑一个样本。★ 用 rng 挑，保证同种子必挑到同一张（契约 C5）。"""
    gs = lib.get(ch)
    if gs is None or not gs.samples:
        return None
    idx = int(rng.integers(0, len(gs.samples)))
    return gs.samples[idx]


def _apply_shape_jitter(
    img: Image.Image,
    spec: JitterSpec,
    rng: np.random.Generator,
) -> Image.Image:
    """第一层抖动：缩放 + 拉伸 + 倾斜 + 加粗。返回新图。"""
    cs = spec.char_shape

    scale = _uniform(rng, cs.scale_min, cs.scale_max)
    stretch_x = _uniform(rng, cs.stretch_x_min, cs.stretch_x_max)
    stretch_y = _uniform(rng, cs.stretch_y_min, cs.stretch_y_max)
    slant = _symmetric(rng, cs.slant_deg)
    bold = _uniform(rng, 0.0, cs.bold_px_max)

    sx = scale * stretch_x
    sy = scale * stretch_y

    w, h = img.size
    nw = max(1, int(round(w * sx)))
    nh = max(1, int(round(h * sy)))

    # 倾斜用仿射变换做（比 shear 更可控）
    if abs(slant) > 0.05:
        shear = math.tan(math.radians(slant))
        # 目标尺寸要留出倾斜造成的额外宽度
        extra = abs(shear) * nh
        nw = max(1, int(round(nw + extra)))
        out = img.resize((max(1, int(round(w * sx))), nh), Image.LANCZOS)
        out = out.transform(
            (nw, nh),
            Image.AFFINE,
            (1, -shear, shear * nh if shear > 0 else 0, 0, 1, 0),
            resample=Image.BICUBIC,
        )
    else:
        out = img.resize((nw, nh), Image.LANCZOS)

    # 加粗：用最大值滤波膨胀 alpha（手写加力时笔画变粗）
    if bold > 0.15:
        from PIL import ImageFilter
        alpha = out.getchannel("A")
        k = max(1, int(round(bold)))
        alpha = alpha.filter(ImageFilter.MaxFilter(size=k * 2 + 1))
        out = out.copy()
        out.putalpha(alpha)

    # 抖动后重新二值化，避免插值产生灰边
    arr = np.asarray(out.convert("RGBA"), dtype=np.uint8).copy()
    solid = arr[..., 3] > 110
    arr[..., 3] = np.where(solid, 255, 0).astype(np.uint8)
    arr[..., 0:3] = 0
    return Image.fromarray(arr, mode="RGBA")


# ─────────────────────────────────────────────────────────────
# 排版：把一格里的字排好（第二层：单字位置）
# ─────────────────────────────────────────────────────────────


def _glyph_box(
    sample: GlyphSample,
    spec: JitterSpec,
    rng: np.random.Generator,
) -> Image.Image:
    """取样本图并按第一层抖动处理。"""
    img = sample.image
    if img is None:
        img = blank_rgba((max(1, sample.width), max(1, sample.height)))
    return _apply_shape_jitter(img.convert("RGBA"), spec, rng)


def _layout_cell(
    cell: CellRect,
    text: str,
    lib: FontLibrary,
    spec: JitterSpec,
    rng: np.random.Generator,
) -> CellPlan:
    """给一格排好版：算出每个字的目标矩形。

    这一步**只算不画** —— 便于单测直接断言坐标。
    """
    plan = CellPlan(row=cell.row, col=cell.col, cell=cell, text=text)

    cl, ct, cr, cb = cell.content_px
    content_w = max(1.0, cr - cl)
    content_h = max(1.0, cb - ct)

    # 逐字取样本；没字形的记进 skipped
    glyphs: list[tuple[str, GlyphSample, Image.Image]] = []
    for ch in text:
        s = _pick_sample(lib, ch, rng)
        if s is None:
            plan.skipped[ch] = plan.skipped.get(ch, 0) + 1
            continue
        glyphs.append((ch, s, _glyph_box(s, spec, rng)))

    if not glyphs:
        return plan

    # ── 算每个字的目标尺寸（第二层）──
    #
    # ★ 这里的关键是「抖动后的字形怎么定尺」。
    #   早先的实现把每个数字都拉到 content_h 高 —— 结果是
    #   第一层的 scale 抖动被完全抹平（所有字一样大），只剩下
    #   stretch_x/stretch_y 造成的变形，看着像"字被压扁"而不是"字有大有小"。
    #
    #   正确口径：
    #   ① 先用所有数字的**基准高度**做归一化，得到一个统一的字号上限
    #   ② 每个字按 `自己的抖动高度 / 最高的抖动高度` 取一个相对系数，
    #      这样 scale 抖动就变成了真实的"这个字略大/略小"
    #   ③ 标点独立按数字高度 × PUNCT_SCALE 定尺，不参与上面的比例
    cp = spec.char_pos

    # 先量出各字抖动后的原始高宽
    raw_sizes = [(ch, img.size[0], img.size[1]) for ch, _s, img in glyphs]

    # 参考高度：所有「数字类」字形的抖动后高度均值，用来做相对比例
    digit_hs = [gh for ch, _gw, gh in raw_sizes if ch not in DOT_CHARS]
    ref_h = (sum(digit_hs) / len(digit_hs)) if digit_hs else 1.0
    if ref_h <= 0:
        ref_h = 1.0

    heights: list[float] = []
    widths: list[float] = []
    for ch, gw, gh in raw_sizes:
        ratio = (gw / gh) if gh else 1.0
        if ch in DOT_CHARS:
            # 标点：等比缩放，高度 = 数字高度 × PUNCT_SCALE，宽度按自身比例
            th = content_h * PUNCT_SCALE
            tw_ = th * ratio
            min_side = content_h * 0.06
            if th < min_side:
                th = min_side
                tw_ = th * ratio
            if tw_ < min_side:
                tw_ = min_side
        else:
            # 数字：相对缩放（抖动幅度映射成字号差异），再夹到合理区间。
            #
            # ★ 上限必须保证「不超过内容区高度」：
            #   k 最大 1.12 时，若 content_h 已贴着格子内容区，
            #   字就会顶出去（实测过：`1` 高 106 > 内容区 95）。
            #   所以先按内容区算出绝对上限，再和 1.12 取小。
            k = gh / ref_h if ref_h else 1.0
            # 上限 1.0 → 最高只到内容区高度（再高就必然出界）；
            # 留 2% 裕度，避免字形紧贴内容区上下边而看着"顶格"。
            k_max = 0.98
            k = min(max(k, 0.86), k_max)
            th = content_h * k
            tw_ = th * ratio
        heights.append(th)
        widths.append(tw_)

    # 字间距：每个字之间留一点随机空隙
    #   ★ 标点两侧特殊处理：句号要紧贴相邻的数字，
    #     否则 `20 . 6` 会被撑成一串松散的符号，不像手写的数。
    gaps: list[float] = []
    for i in range(len(glyphs) - 1):
        left_ch = glyphs[i][0]
        right_ch = glyphs[i + 1][0]
        if left_ch in DOT_CHARS or right_ch in DOT_CHARS:
            # 贴紧：只留极小固定缝（保证不重叠），不给随机余量
            gaps.append(content_w * PUNCT_TIGHTEN)
            continue
        base = content_w * 0.02
        extra = content_w * cp.spacing * abs(_symmetric(rng, 1.0))
        gaps.append(base + extra)

    total_w = sum(widths) + sum(gaps)

    # ── 收敛到内容区 ──
    #
    # ★ 为什么不给偏移预留余量：
    #   早期版本先把字串压到 `content_w - 抖动余量`，结果字串只占 79%，
    #   看上去字特别小、两边空一大片。
    #   实际上「偏移出界」由下面的**偏移限幅**兜住就够了 ——
    #   字可以排满，但整串最多只能平移到刚好贴边，绝不会出去。
    if total_w > content_w and total_w > 0:
        k = content_w / total_w
        widths = [w * k for w in widths]
        heights = [h * k for h in heights]
        gaps = [g * k for g in gaps]
        total_w = content_w

    # 起始位置：水平居中，偏移量按「剩余空间」限幅（保证怎么抖都不出界）
    slack = max(0.0, content_w - total_w) / 2.0
    start_x = cl + slack
    offset = _symmetric(rng, content_w * cp.offset_x)
    if offset > slack:
        offset = slack
    elif offset < -slack:
        offset = -slack
    start_x += offset

    x = start_x
    for i, (ch, s, img) in enumerate(glyphs):
        w, h = widths[i], heights[i]

        # 垂直：数字按内容区居中；标点贴近基线
        if ch in DOT_CHARS:
            y = ct + content_h * PUNCT_BASELINE - h
        else:
            y = ct + (content_h - h) / 2.0
        # ★ 垂直抖动同样要限幅：拿「基线位置 + 剩余空间」夹一下，
        #   不然标点（本来就贴底）往下抖一点就掉出格子。
        y_target = y + _symmetric(rng, content_h * cp.offset_y)
        y_min = ct
        y_max = ct + content_h - h
        if y_max < y_min:
            y_max = y_min
        y = min(max(y_target, y_min), y_max)

        plan.items.append((ch, i, (x, y, x + w, y + h)))
        x += w + (gaps[i] if i < len(gaps) else 0.0)

    plan.skipped = dict(plan.skipped)
    return plan


# ─────────────────────────────────────────────────────────────
# 第三层：整格变换
# ─────────────────────────────────────────────────────────────


def _draw_cell_layer(
    plan: CellPlan,
    lib: FontLibrary,
    spec: JitterSpec,
    rng: np.random.Generator,
) -> Image.Image:
    """把一格的**所有字符**画到一张透明图层上（还没有整格变换）。

    ★ 这是"先合再转"的前半句：整格旋转必须在**合成后**的图层上做。
    """
    cell = plan.cell
    cl, ct, cr, cb = cell.content_px
    # 图层要留出旋转后可能溢出内容区的余量，否则转角会被裁掉
    pad_x = int((cr - cl) * 0.35) + 4
    pad_y = int((cb - ct) * 0.35) + 4

    layer_w = max(1, int(cr - cl) + pad_x * 2)
    layer_h = max(1, int(cb - ct) + pad_y * 2)
    layer = blank_rgba((layer_w, layer_h))

    origin_x = cl - pad_x
    origin_y = ct - pad_y

    for ch, idx, (x0, y0, x1, y1) in plan.items:
        s = _pick_sample_cached(lib, ch, idx, plan)
        img = _glyph_box(s, spec, rng) if s is not None else None
        if img is None:
            continue

        tw = max(1, int(round(x1 - x0)))
        th = max(1, int(round(y1 - y0)))
        img = img.resize((tw, th), Image.LANCZOS)

        px = int(round(x0 - origin_x))
        py = int(round(y0 - origin_y))
        layer.alpha_composite(img, (px, py))

    return layer


def _pick_sample_cached(lib: FontLibrary, ch: str, idx: int, plan: CellPlan) -> GlyphSample | None:
    """按排版时定下的序号取样本（保证同一格内前后一致）。"""
    gs = lib.get(ch)
    if gs is None or not gs.samples:
        return None
    return gs.samples[idx % len(gs.samples)]


def _apply_cell_transform(
    layer: Image.Image,
    cell: CellRect,
    spec: JitterSpec,
    rng: np.random.Generator,
) -> tuple[Image.Image, tuple[float, float], float]:
    """第三层：整格平移 + 旋转。

    ★ 对**整个图层**做一次变换，而不是逐字各转
      —— 逐字各转会让字歪向不同方向，像散架的活字印刷。

    Returns:
        (变换后的图层, (dx, dy), 旋转角度)
    """
    cj = spec.cell
    cl, ct, cr, cb = cell.content_px
    cw = max(1.0, cr - cl)
    chh = max(1.0, cb - ct)

    dx = _symmetric(rng, cw * cj.offset_x)
    dy = _symmetric(rng, chh * cj.offset_y)
    angle = _symmetric(rng, cj.rotate_deg)

    out = layer
    if abs(angle) > 0.01:
        out = out.rotate(
            angle, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0, 0)
        )

    return out, (dx, dy), angle


# ─────────────────────────────────────────────────────────────
# 单格渲染（对外可测的粒度）
# ─────────────────────────────────────────────────────────────


def render_cell(
    cell: CellRect,
    text: str,
    lib: FontLibrary,
    spec: JitterSpec,
    seed: int,
) -> tuple[Image.Image | None, tuple[float, float], CellPlan]:
    """渲染一个格子。

    Returns:
        (图层, (绝对左上角 x, y), 排版计划)。没有任何可画的字时图层为 None。

    ★ 图层坐标是**页面绝对坐标**对齐的，调用方直接用返回的 (x, y) 贴即可。
    """
    rng = rng_for_cell(seed, cell.row, cell.col)
    plan = _layout_cell(cell, text, lib, spec, rng)

    if not plan.items:
        return None, (0.0, 0.0), plan

    # ① 先把这一格的**所有字**合成到一张透明图层（还没有整格变换）
    content_layer = _draw_cell_layer(plan, lib, spec, rng)

    # ② 再对整张图层做整格变换（★ 先合再转）
    transformed, (dx, dy), angle = _apply_cell_transform(content_layer, cell, spec, rng)
    plan.cell_offset = (dx, dy)
    plan.cell_rotate = angle

    # ③ 换算变换后图层的绝对左上角
    cl, ct, cr, cb = cell.content_px
    pad_x = int((cr - cl) * 0.35) + 4
    pad_y = int((cb - ct) * 0.35) + 4

    # rotate(expand=True) 会让画布变大，多出来的量要补偿回去
    grew_x = (transformed.width - content_layer.width) / 2.0
    grew_y = (transformed.height - content_layer.height) / 2.0

    abs_x = cl - pad_x + dx - grew_x
    abs_y = ct - pad_y + dy - grew_y

    return transformed, (abs_x, abs_y), plan


# ─────────────────────────────────────────────────────────────
# 整页渲染（对外主接口）
# ─────────────────────────────────────────────────────────────


def render_page(
    page_bitmap: Image.Image,
    cells: list[CellRect],
    matrix: ContentMatrix,
    lib: FontLibrary,
    spec: JitterSpec,
    seed: int,
    on_progress: Callable[[int, int], None] | None = None,
) -> FillResult:
    """把内容填到页面位图上。★ 纯函数：不改入参、不读盘、不查全局。

    Args:
        page_bitmap: 底图（模板渲染结果）。**本函数不会修改它**，
                     而是复制一份再画（契约 C13：输入不可污染）。
        cells: 要填的格子（已含 px 坐标）。
        matrix: 内容矩阵。取 (row, col) 的值。
        lib: 字形库（样本图需已加载）。
        spec: 四层抖动参数。
        seed: 随机种子。**同种子同输入 → 逐像素相同结果**（契约 C5）。
        on_progress: 进度回调 (已完成格数, 总格数)。

    Returns:
        FillResult
    """
    # ★ 复制入参，绝不原地改
    page = page_bitmap.convert("RGBA").copy()

    result = FillResult(image=page)
    skipped: dict[str, int] = {}
    warnings: list[str] = []

    total = len(cells)
    done = 0

    for cell in cells:
        text = matrix.at(cell.row, cell.col)
        if not text:
            done += 1
            if on_progress:
                on_progress(done, total)
            continue

        if len(text) > MAX_CHARS_PER_CELL:
            text = text[:MAX_CHARS_PER_CELL]
            warnings.append(
                f"第 {cell.row + 1} 行第 {cell.col + 1} 列的字数过多，已截断到 {MAX_CHARS_PER_CELL} 个。"
            )

        layer, (lx, ly), plan = render_cell(cell, text, lib, spec, seed)

        # 跳过统计
        for ch, n in plan.skipped.items():
            skipped[ch] = skipped.get(ch, 0) + n

        if layer is not None and plan.items:
            page.alpha_composite(layer, (int(round(lx)), int(round(ly))))
            result.filled_cells += 1

        done += 1
        if on_progress:
            on_progress(done, total)

    result.skipped_chars = skipped
    result.total_chars = sum(len(matrix.at(c.row, c.col)) for c in cells)
    result.warnings = warnings
    return result


def count_skipped(matrix: ContentMatrix, cells: list[CellRect], lib: FontLibrary) -> dict[str, int]:
    """只统计「哪些字符没有字形」，不渲染。界面预检用。"""
    out: dict[str, int] = {}
    for cell in cells:
        text = matrix.at(cell.row, cell.col)
        for ch in text:
            if not lib.has(ch):
                out[ch] = out.get(ch, 0) + 1
    return out


def plan_page(
    cells: list[CellRect],
    matrix: ContentMatrix,
    lib: FontLibrary,
    spec: JitterSpec,
    seed: int,
) -> list[CellPlan]:
    """只算排版不画图（调试 / 单测用）。"""
    plans: list[CellPlan] = []
    for cell in cells:
        text = matrix.at(cell.row, cell.col)
        if not text:
            continue
        rng = rng_for_cell(seed, cell.row, cell.col)
        plans.append(_layout_cell(cell, text, lib, spec, rng))
    return plans
