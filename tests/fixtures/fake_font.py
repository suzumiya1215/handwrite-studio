"""★ 程序化生成假字形库 —— M3 填充引擎开发的命脉。

为什么必须有它：
    没有真实手写样本时，FillService 无法调试。用程序化生成的假字形，
    测试瞬间完成且完全确定 —— 不受"这次照片拍得不好"的影响。
    有了它，M3 可以不等 M2 的采集向导完工就开工（实施计划 §0.2 的并行前提）。

设计要点：
    - 不依赖任何系统字体：用 PIL 内置位图字体画，CI 上必然可跑
    - 每个字符可生成多个「样本」（variant 控制形状微差，模拟真实手写）
    - 输出规格严格符合契约 C12：RGBA、前景纯黑、背景 alpha=0
"""

from __future__ import annotations

import io
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from handwrite.core import charset as cs
from handwrite.core.models.glyph import FontLibrary, GlyphSample, GlyphSet

# ─────────────────────────────────────────────────────────────
# 保底字形：用 PIL 内置字体，找不到就用更兜底的默认字体
# ─────────────────────────────────────────────────────────────

_BUILTIN_FONT_CACHE: dict[int, ImageFont.ImageFont] = {}


def _builtin_font(size: int) -> ImageFont.ImageFont:
    """取一个可用的位图/矢量字体。

    ★ 优先用 PIL 自带的 load_default(size=)，新版本 Pillow 支持缩放；
      老版本退回不可缩放的内置字体，此时字形会小一点但不影响测试。
    """
    if size in _BUILTIN_FONT_CACHE:
        return _BUILTIN_FONT_CACHE[size]
    font: ImageFont.ImageFont
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:                       # 老版本 Pillow 没有 size 参数
        font = ImageFont.load_default()
    _BUILTIN_FONT_CACHE[size] = font
    return font


# ─────────────────────────────────────────────────────────────
# 单字形渲染
# ─────────────────────────────────────────────────────────────

#: 标点类字符的**高度**基准（相对字高的比例）
#:
#: ★ 用「高度」定义而不是「宽度」：
#:   真实手写的句号/逗号是一个小点，高度约为数字的 1/4~1/3，
#:   宽度≈高度（略扁或略圆），接近方形。
#:   早期版本按"宽度 = 字高 × 0.11"来压扁，导致句号的宽高比被
#:   任意规定成 1.8:1 这样的扁条，放大后就成了一根短横 —— bug 根源。
_PUNCT_HEIGHT_RATIO = 0.28

#: 句号的宽高比（1.0 略扁一点，像毛笔点的形状）
_PUNCT_ASPECT = 1.15

#: 逗号的宽高比（比句号更瘦长一点，带个小尾巴）
_COMMA_ASPECT = 0.85


def _draw_char(ch: str, height: int, font: ImageFont.ImageFont) -> Image.Image:
    """把字符画成「黑字 + 透明底」的图，高度恰好 = height。"""
    # 先在大画布上画，再量出实际墨迹范围
    canvas = Image.new("L", (height * 3, height * 3), 0)
    d = ImageDraw.Draw(canvas)
    try:
        d.text((height, height), ch, fill=255, font=font)
    except Exception:
        # 内置字体画不出来的字符（如中文），用方框代替
        d.rectangle([height, height, height + height // 2, height * 2], fill=255)
    bbox = canvas.getbbox()
    if bbox is None:                        # 空字形，兜底画一个小方块
        canvas = Image.new("L", (height // 2, height // 2), 255)
    else:
        canvas = canvas.crop(bbox)
    return canvas


def render_glyph(ch: str, variant: int = 0, height: int = 128,
                 alpha_threshold: int = 24) -> Image.Image:
    """渲染一个字符为字形图（RGBA，前景黑、背景透明）。

    Args:
        ch: 字符
        variant: 样本编号。不同编号产生形状微差（模拟同一人的不同写法）
        height: 目标高度（= manifest.glyph_height）
        alpha_threshold: 灰度低于此值算背景（去掉字体渲染的淡边缘）

    Returns:
        RGBA 图。高度 = height，宽度按原始长宽比缩放，四边留 ≥2px 透明边。
    """
    raw = _draw_char(ch, height, _builtin_font(height))

    # 标点特殊处理：画成一个小点，尺寸和形状都由常量明确控制。
    #
    # ★ 这里直接"造"一个点，而不是把字体渲染出的句号压扁 ——
    #   字体渲染结果随字体/字号浮动，压扁后宽高比就不可控了。
    if ch in ",.":
        ph = max(6, int(round(height * _PUNCT_HEIGHT_RATIO)))
        aspect = _COMMA_ASPECT if ch == "," else _PUNCT_ASPECT
        pw = max(6, int(round(ph * aspect)))
        dot = Image.new("L", (pw, ph), 0)
        dd = ImageDraw.Draw(dot)
        if ch == ".":
            # 圆点
            dd.ellipse([0, 0, pw - 1, ph - 1], fill=255)
        else:
            # 逗号：上圆下尖（用椭圆 + 下方小三角拼）
            r = max(3, ph // 2)
            dd.ellipse([0, 0, pw - 1, ph - 1], fill=255)
            dd.polygon(
                [(pw // 2 - r // 2, ph - r // 2), (pw // 2 + r // 2, ph - r // 2),
                 (pw // 2, ph)],
                fill=255,
            )
        raw = dot

    # variant 让每个样本略有不同（旋转 + 缩放微差），模拟手写变异
    if variant:
        angle = (variant % 5 - 2) * 1.7          # ±3.4 度
        scale = 1.0 + ((variant % 3) - 1) * 0.04  # ±4%
        if abs(scale - 1.0) > 1e-6:
            raw = raw.resize(
                (max(1, int(raw.width * scale)), max(1, int(raw.height * scale))),
                Image.LANCZOS,
            )
        if abs(angle) > 1e-6:
            raw = raw.rotate(angle, resample=Image.BICUBIC, expand=True,
                             fillcolor=0)

    # 等比缩放到目标高度
    # ★ 标点例外：它本来就该比字矮得多，不能拉满到 height。
    #   早期版本在这里无差别拉高，把句号又变回了一个大方块 ——
    #   下游（normalize_for_char）再按比例缩就得到宽度离谱的"点"。
    is_punct = ch in ",."
    if not is_punct:
        if raw.height != height and raw.height > 0:
            k = height / raw.height
            raw = raw.resize((max(1, int(round(raw.width * k))), height), Image.LANCZOS)
        elif raw.height == 0:
            raw = Image.new("L", (height // 2, height), 0)
        if raw.height != height:
            raw = raw.resize((max(1, raw.width), height), Image.LANCZOS)
    elif raw.height == 0:
        ph = max(6, int(round(height * _PUNCT_HEIGHT_RATIO)))
        pw = max(6, int(round(ph * _PUNCT_ASPECT)))
        raw = Image.new("L", (pw, ph), 0)

    # 加透明边距（防旋转裁笔画）
    pad = 2
    padded = Image.new("L", (raw.width + pad * 2, raw.height + pad * 2), 0)
    padded.paste(raw, (pad, pad))

    # L → RGBA：墨迹处黑色不透明，背景全透明
    rgba = Image.new("RGBA", padded.size, (0, 0, 0, 0))
    px = padded.load()
    out = rgba.load()
    for y in range(padded.height):
        for x in range(padded.width):
            a = px[x, y]
            if a > alpha_threshold:
                out[x, y] = (0, 0, 0, 255)   # 纯黑，全不透明
    return rgba


# ─────────────────────────────────────────────────────────────
# 组装字形库
# ─────────────────────────────────────────────────────────────


def make_fake_font(
    samples_per_char: int = 2,
    chars: Iterable[str] | None = None,
    height: int = 128,
    name: str = "测试字迹",
    lib_id: str = "test-font",
) -> FontLibrary:
    """构造一个完整的、可直接喂给 FillService 的 FontLibrary。

    Args:
        samples_per_char: 每个字符生成几个样本
        chars: 要包含的字符（默认 v1 全部 12 个）
        height: 字形高度
        name / lib_id: 元信息
    """
    target = list(chars) if chars is not None else list(cs.CHARSET_V1)
    characters: dict[str, GlyphSet] = {}

    for ch in target:
        samples: list[GlyphSample] = []
        for i in range(samples_per_char):
            img = render_glyph(ch, variant=i, height=height)
            samples.append(GlyphSample(
                file=f"{i + 1:03d}.png",
                width=img.width,
                height=img.height,
                image=img,
            ))
        characters[ch] = GlyphSet(char=ch, samples=samples)

    lib = FontLibrary(
        id=lib_id,
        name=name,
        charset=list(cs.CHARSET_ENABLED_V1),
        glyph_height=height,
        characters=characters,
        created_at="2026-10-09T00:00:00+08:00",
        updated_at="2026-10-09T00:00:00+08:00",
        notes="程序化生成的测试字形库，非真实手写",
        app_version="1.0.0",
    )
    return lib


def make_blind_font(
    missing: set[str] | None = None,
    **kwargs,
) -> FontLibrary:
    """缺字版本的字形库 —— 用来测「跳过字符」的计数是否正确。

    Args:
        missing: 要移除的字符集合，默认移除 {"A"}（本来就不在 v1 里），
                 测「超范围」时应该传 v1 内的字符如 {"8"}。
    """
    lib = make_fake_font(**kwargs)
    drop = missing if missing is not None else set()
    for ch in drop:
        lib.characters.pop(ch, None)
    return lib


def make_single_char_font(ch: str, samples: int = 2, **kwargs) -> FontLibrary:
    """只含一个字符的库（测边界：某格只填一个字）。"""
    return make_fake_font(samples_per_char=samples, chars=[ch], **kwargs)


def make_font_with_big_variance(ch: str = "8", samples: int = 6,
                                height: int = 128) -> FontLibrary:
    """样本差异很大的库 —— 用来验证「随机取样本」确实取到了不同的。
    """
    imgs = [render_glyph(ch, variant=i, height=height) for i in range(samples)]
    samples_list = [
        GlyphSample(file=f"{i + 1:03d}.png", width=img.width,
                    height=img.height, image=img)
        for i, img in enumerate(imgs)
    ]
    lib = FontLibrary(
        id="variance-font",
        name="多变样本",
        charset=list(cs.CHARSET_ENABLED_V1),
        glyph_height=height,
        characters={ch: GlyphSet(char=ch, samples=samples_list)},
        created_at="2026-10-09T00:00:00+08:00",
        updated_at="2026-10-09T00:00:00+08:00",
    )
    return lib


# ─────────────────────────────────────────────────────────────
# 落到磁盘（给需要真实 .hwfont 的测试用）
# ─────────────────────────────────────────────────────────────


def write_font_to_dir(lib: FontLibrary, root: str) -> str:
    """把字形库按 .hwfont 的目录结构写到磁盘（解压态，不是 zip）。

    用于测试 FontService 的加载逻辑（它既能读 zip 也能读目录）。

    Returns:
        写入的根目录路径
    """
    import json
    import os

    os.makedirs(root, exist_ok=True)
    for ch, gs in lib.characters.items():
        d = os.path.join(root, "glyphs", gs.dirname())
        os.makedirs(d, exist_ok=True)
        for s in gs.samples:
            if s.image is not None:
                s.image.save(os.path.join(d, s.file), "PNG")
    manifest = lib.to_manifest()
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return root


def make_font_bytes(lib: FontLibrary) -> bytes:
    """把字形库打成 .hwfont 的 zip 字节流（不落盘）。"""
    import json
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",
                   json.dumps(lib.to_manifest(), ensure_ascii=False, indent=2))
        for ch, gs in lib.characters.items():
            for s in gs.samples:
                if s.image is None:
                    continue
                png = io.BytesIO()
                s.image.save(png, "PNG")
                z.writestr(f"glyphs/{gs.dirname()}/{s.file}", png.getvalue())
    return buf.getvalue()
