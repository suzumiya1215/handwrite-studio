"""图像基础操作（L1 基础层）。

设计依据：
- 模块设计说明书 §4.4（imageops）
- 数据格式规范 §1.3（字形图规格）
- 接口契约与测试用例 §3

★ 契约要点：
- C12：字形图输出规格 —— RGBA、前景纯黑 (0,0,0,255)、背景 (0,0,0,0)、四边透明
- C13：本模块**不得修改入参图像**（原地改会污染调用方的预览缓存）
- 所有函数只依赖 Pillow + numpy，**不依赖 Qt**（架构禁令 N1）

主要能力：
- 读图（含 EXIF 方向纠正、超大图降采样防爆内存）
- 背景去除（按容差，把接近背景色的像素变透明）
- 裁边（去掉四周空白）
- 连通域切分（把一张图里的多个字切开）
- 规范化（统一高度、保留原始宽高比）
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from . import geometry as geo
from .errors import FontCorruptedError

# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────

#: 读取时允许的最大像素数（约 8000×8000）。超过就降采样，
#: 避免一张 1 亿像素的手机原图把内存打爆。
MAX_PIXELS = 64_000_000

#: 去背景后保留为前景的最小 alpha 值
ALPHA_KEEP = 24

#: 连通域的最小面积占比（相对整图面积）。低于此视为噪点。
#: ★ 注意标点（. ,）面积很小，这个默认值只用于「自动估算」，
#:   真正的过滤阈值由调用方按预期字符数决定。
DEFAULT_MIN_AREA_RATIO = 0.00008

#: 相邻连通域合并的间隙（像素）。手写时笔画断开、或点与横分离时用得上。
MERGE_GAP_PX = 6

#: ★ 标点在标准字形高度里最多占的比例。
#: 句号 / 逗号在真实手写里大约只有字符高度的 1/4~1/3。
#: 归一化时若把它们也拉到满高，贴到表格里就会变成巨大的黑点。
PUNCT_MAX_HEIGHT_RATIO = 0.34

#: 属于「小标点」的字符 —— 归一化时要保持小巧，不能拉满高。
SMALL_PUNCT_CHARS = frozenset({".", ",", ";", ":", "'", "`"})


# ─────────────────────────────────────────────────────────────
# 读图 / 存图
# ─────────────────────────────────────────────────────────────


def load_image(path: str, *, max_pixels: int = MAX_PIXELS) -> Image.Image:
    """读一张图并转成 RGB。

    - 自动按 EXIF 旋转（手机竖拍的照片不会躺倒）
    - 超过 max_pixels 的图会等比降采样
    - 读不了抛 FontCorruptedError（带中文提示）
    """
    try:
        img = Image.open(path)
        img.load()
    except FileNotFoundError as e:
        raise FontCorruptedError(f"找不到图片文件：{path}", hint="文件可能被移动或删除了。") from e
    except OSError as e:
        raise FontCorruptedError(
            f"这张图片打不开：{path}",
            hint="可能不是有效的图片，或者文件损坏了。",
        ) from e

    # EXIF 方向纠正 —— 手机拍的照片几乎都带这个标记
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass                  # 没有 EXIF 或信息异常时保持原样，不算错误

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img).convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    if w * h > max_pixels:
        scale = (max_pixels / (w * h)) ** 0.5
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size, Image.LANCZOS)

    return img


def to_gray_array(img: Image.Image) -> np.ndarray:
    """转成 uint8 灰度 numpy 数组（不修改入参）。"""
    if img.mode != "L":
        img = img.convert("L")
    return np.asarray(img, dtype=np.uint8).copy()


def blank_rgba(size: tuple[int, int]) -> Image.Image:
    """建一张全透明的 RGBA 画布。"""
    return Image.new("RGBA", size, (0, 0, 0, 0))


# ─────────────────────────────────────────────────────────────
# 去背景（抠图第一步）
# ─────────────────────────────────────────────────────────────


def estimate_background(img: Image.Image) -> tuple[int, int, int]:
    """估计背景色 —— 取四角各一小块的中位色。

    比"取全图众数"稳：手写纸的照片里字占的面积不小，
    全图众数偶尔会把一大片字迹当成背景。
    """
    w, h = img.size
    pad_x = max(1, min(w // 10, 40))
    pad_y = max(1, min(h // 10, 40))

    small = img.convert("RGB")
    corners = [
        small.crop((0, 0, pad_x, pad_y)),
        small.crop((w - pad_x, 0, w, pad_y)),
        small.crop((0, h - pad_y, pad_x, h)),
        small.crop((w - pad_x, h - pad_y, w, h)),
    ]
    pixels = np.concatenate([
        np.asarray(c, dtype=np.uint8).reshape(-1, 3) for c in corners
    ])
    med = np.median(pixels, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def remove_background(
    img: Image.Image,
    *,
    tolerance: int = 29,
    background: tuple[int, int, int] | None = None,
) -> Image.Image:
    """把接近背景色的像素变透明，其余变纯黑前景。

    ★ 返回**新图**，不改入参（契约 C13）。

    Args:
        tolerance: 0~255。越大去掉的越多。原型默认 29。
                   判据用「与背景色的最大通道差」而不是欧氏距离 ——
                   对浅灰纸、偏黄纸都更稳，且滑块手感线性。

    Returns:
        RGBA 图：前景 (0,0,0,255)，背景 (0,0,0,0)。
    """
    src = img.convert("RGB") if img.mode != "RGB" else img
    arr = np.asarray(src, dtype=np.int16)

    if background is None:
        background = estimate_background(src)

    bg = np.array(background, dtype=np.int16)
    # 每像素与背景的最大通道差
    diff = np.abs(arr - bg).max(axis=2)

    # tolerance 越大 → 越多像素被判为背景
    is_bg = diff <= int(tolerance)

    h, w = is_bg.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., 0] = 0
    out[..., 1] = 0
    out[..., 2] = 0
    out[~is_bg, 3] = 255

    return Image.fromarray(out, mode="RGBA")


def refine_mask(img: Image.Image, *, smooth: bool = True) -> Image.Image:
    """把粗糙的二值 mask 修一下：去孤立噪点 + 轻度平滑边缘。

    放在 remove_background 之后、切分之前。
    """
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")

    if smooth:
        # 中值滤波去椒盐噪点，再高斯轻模糊让边缘不那么锯齿
        alpha = alpha.filter(ImageFilter.MedianFilter(size=3))
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.6))

    out = rgba.copy()
    out.putalpha(alpha)
    return out


# ─────────────────────────────────────────────────────────────
# 裁边 / 规范化
# ─────────────────────────────────────────────────────────────


def content_bbox(img: Image.Image, *, alpha_threshold: int = ALPHA_KEEP) -> tuple[int, int, int, int] | None:
    """求图中「有内容」的紧致包围盒。全透明返回 None。"""
    rgba = img.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    ys, xs = np.where(alpha > alpha_threshold)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def trim(img: Image.Image, *, padding: int = 2, alpha_threshold: int = ALPHA_KEEP) -> Image.Image:
    """裁掉四周空白，留一点 padding。返回新图；全空则原样返回。"""
    box = content_bbox(img, alpha_threshold=alpha_threshold)
    if box is None:
        return img.convert("RGBA").copy()

    l, t, r, b = box
    w, h = img.size
    l = max(0, l - padding)
    t = max(0, t - padding)
    r = min(w, r + padding)
    b = min(h, b + padding)
    return img.convert("RGBA").crop((l, t, r, b))


def normalize_glyph(
    img: Image.Image,
    *,
    target_height: int = 128,
    trim_first: bool = True,
    preserve_scale: bool = False,
) -> Image.Image:
    """把切出来的字形规范成统一规格。

    规格（契约 C12）：
    - RGBA
    - 高 = target_height（等比缩放，**不拉伸宽**，保留字迹胖瘦）
    - 前景纯黑、背景全透明
    - 四边留 2px 透明边（防止贴图时被裁掉笔画）

    ★ 只按高度缩放，宽度按原始宽高比跟随 —— 这是保真的关键：
      把每个字都拉伸成同样宽会把「1」拉胖、「8」压扁。

    Args:
        preserve_scale: ★ 标点专用。True 时**不把标点拉到满高**，
            而是保持它相对整张原图的高度比例。

            为什么需要：句号在原稿里只有字符高度的 1/4 左右。
            如果也按「拉到 128 高」处理，一个句号会被放大成跟数字一样高，
            贴到表格里就变成一个巨大的黑点 —— 完全不像手写。

            做法：先看它在原图里占多高，按「原图里字符的平均高度」
            推算缩放比，使标点尺寸与原稿一致。
    """
    work = trim(img) if trim_first else img.convert("RGBA")
    w, h = work.size
    if w <= 0 or h <= 0:
        raise FontCorruptedError("这个字是空白的，没有办法做成字形。")

    inner_h = max(1, target_height - 4)          # 预留上下各 2px

    if preserve_scale:
        # 标点：原尺寸已经很小了，只做轻微放大，**不拉满到 inner_h**。
        # 目标是让它保持「小」这个特征 —— 高度取 inner_h 的 1/3 封顶。
        cap = max(6, int(inner_h * PUNCT_MAX_HEIGHT_RATIO))
        scale = min(cap / h, 1.0) if h > cap else cap / h
        new_h = max(4, int(round(h * scale)))
        new_w = max(3, int(round(w * scale)))
    else:
        scale = inner_h / h
        new_h = inner_h
        new_w = max(1, int(round(w * scale)))

    scaled = work.resize((new_w, new_h), Image.LANCZOS)

    # 标点贴底对齐（句点写在基线附近），普通字符居中
    canvas_h = target_height
    canvas_w = new_w + 4
    canvas = blank_rgba((canvas_w, canvas_h))
    if preserve_scale:
        # 底部留 15% 的空白，模拟「点落在基线下方」的观感
        paste_y = max(0, canvas_h - new_h - int(canvas_h * 0.15))
    else:
        paste_y = 2
    canvas.paste(scaled, (2, paste_y), scaled)

    # 强制前景纯黑（缩放插值可能产生灰边）
    arr = np.asarray(canvas, dtype=np.uint8).copy()
    solid = arr[..., 3] > ALPHA_KEEP
    arr[..., 0][solid] = 0
    arr[..., 1][solid] = 0
    arr[..., 2][solid] = 0
    arr[..., 3][~solid] = 0
    return Image.fromarray(arr, mode="RGBA")


def normalize_to_height(img: Image.Image, target_height: int) -> Image.Image:
    """只缩放到指定高度（不 trim、不补边）。标点排版时会用到。"""
    w, h = img.size
    if h <= 0:
        raise FontCorruptedError("图片高度为 0，无法缩放。")
    scale = target_height / h
    return img.resize((max(1, int(round(w * scale))), target_height), Image.LANCZOS)


def is_small_punct(ch: str) -> bool:
    """这个字符是不是「小标点」（归一化时不该拉满高度）。"""
    return ch in SMALL_PUNCT_CHARS


def normalize_for_char(
    img: Image.Image,
    ch: str,
    *,
    target_height: int = 128,
    trim_first: bool = True,
) -> Image.Image:
    """按字符类型自动选择归一化方式。

    ★ 这是给采集流程用的统一入口：
      数字 → 拉满高；小标点 → 保持小巧贴底。
      调用方不必自己判断字符类别。
    """
    return normalize_glyph(
        img,
        target_height=target_height,
        trim_first=trim_first,
        preserve_scale=is_small_punct(ch),
    )


# ─────────────────────────────────────────────────────────────
# 连通域切分（抠图 / 框字的核心）
# ─────────────────────────────────────────────────────────────


def _label_components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """两遍扫描法给二值图打连通标记（8 邻域）。

    为什么不用 scipy.ndimage.label：
    少一个依赖（本项目已因 PySide6 很重了），
    而且这里只需要标号，不需要质心/惯量等高级统计。

    Returns:
        (labels, count) —— labels 是 int32 矩阵，0 表示背景。
    """
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    parent: list[int] = [0]        # 并查集，下标即标号

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    next_label = 1
    for y in range(h):
        row = mask[y]
        for x in range(w):
            if not row[x]:
                continue
            # 看左边、上边、左上、右上 4 个已处理的邻居（8 邻域的前半）
            neigh: list[int] = []
            if x > 0 and labels[y, x - 1]:
                neigh.append(int(labels[y, x - 1]))
            if y > 0:
                if labels[y - 1, x]:
                    neigh.append(int(labels[y - 1, x]))
                if x > 0 and labels[y - 1, x - 1]:
                    neigh.append(int(labels[y - 1, x - 1]))
                if x + 1 < w and labels[y - 1, x + 1]:
                    neigh.append(int(labels[y - 1, x + 1]))

            if not neigh:
                labels[y, x] = next_label
                parent.append(next_label)
                next_label += 1
            else:
                m = min(neigh)
                labels[y, x] = m
                for n in neigh:
                    union(m, n)

    # 第二遍：把等价类收敛到根号
    if next_label > 1:
        roots = np.array([find(i) for i in range(next_label)], dtype=np.int32)
        # 压缩编号，让标号连续
        uniq = np.unique(roots[1:])
        remap = np.zeros(next_label, dtype=np.int32)
        for new_id, old_root in enumerate(uniq, start=1):
            remap[old_root] = new_id
        labels = remap[labels]

    return labels, int(labels.max()) if labels.size else 0


def _merge_nearby(boxes: list[tuple[int, int, int, int]], gap: int) -> list[tuple[int, int, int, int]]:
    """把水平/垂直间隙小于 gap 的框合并。

    手写的「5」上下两笔可能断开、「i」的点与竖分离 ——
    不合并就会被切成两个「字」。
    """
    if len(boxes) < 2:
        return boxes

    merged = True
    while merged:
        merged = False
        out: list[tuple[int, int, int, int]] = []
        used = [False] * len(boxes)
        for i in range(len(boxes)):
            if used[i]:
                continue
            li, ti, ri, bi = boxes[i]
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                lj, tj, rj, bj = boxes[j]
                # 水平/垂直投影都重叠或接近才算同一个字
                h_close = not (ri + gap < lj or rj + gap < li)
                v_close = not (bi + gap < tj or bj + gap < ti)
                if h_close and v_close:
                    li, ti = min(li, lj), min(ti, tj)
                    ri, bi = max(ri, rj), max(bi, bj)
                    used[i] = used[j] = True
                    merged = True
            out.append((li, ti, ri, bi))
        boxes = out
    return boxes


def split_components(
    img: Image.Image,
    *,
    alpha_threshold: int = ALPHA_KEEP,
    min_area_ratio: float = DEFAULT_MIN_AREA_RATIO,
    merge_gap: int = MERGE_GAP_PX,
    keep_max: int | None = None,
) -> list[tuple[int, int, int, int]]:
    """把图里的字一个个切出来，返回包围盒列表（按阅读顺序：先上后下、先左后右）。

    ★ 标点保护：``.`` 和 ``,`` 的面积很小，容易被面积阈值误杀。
      所以 ``min_area_ratio`` 默认取得很低，而且——
      如果设了 keep_max 且过滤后剩下的数量不足，
      会**降级为「按面积取前 keep_max 大」**而不是交出一堆空框。
      对应契约用例 test_small_punct_not_killed。

    Returns:
        [(left, top, right, bottom), ...]，坐标为像素。
    """
    rgba = img.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    mask = alpha > alpha_threshold

    if not mask.any():
        return []

    labels, count = _label_components(mask)
    if count == 0:
        return []

    h, w = mask.shape
    total = h * w
    boxes: list[tuple[int, int, int, int]] = []

    for lab in range(1, count + 1):
        ys, xs = np.where(labels == lab)
        if len(xs) == 0:
            continue
        area = len(xs)
        if area / total < min_area_ratio:
            continue
        boxes.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))

    if not boxes:
        return []

    boxes = _merge_nearby(boxes, merge_gap)

    # 按阅读顺序排：先按行（y 中心聚类），行内再按 x
    boxes = sort_reading_order(boxes)

    # ★ 降级保护：数量不够时放宽面积限制重来一次
    if keep_max is not None and len(boxes) < keep_max:
        relaxed = max(0.0, min_area_ratio / 8.0)
        more = split_components(
            img,
            alpha_threshold=alpha_threshold,
            min_area_ratio=relaxed,
            merge_gap=merge_gap,
            keep_max=None,
        )
        if len(more) > len(boxes):
            boxes = more

    return boxes


def sort_reading_order(
    boxes: list[tuple[int, int, int, int]],
    *,
    row_tolerance_ratio: float = 0.5,
) -> list[tuple[int, int, int, int]]:
    """按人类阅读顺序排序：先上后下、先左后右。

    单纯按 y 排序不够 —— 一页里上一行的字可能比下一行的字还低一点。
    做法是先把 y 中心相近的归成一行。
    """
    if not boxes:
        return []

    heights = [b[3] - b[1] for b in boxes]
    median_h = float(np.median(heights)) if heights else 1.0
    row_tol = max(1.0, median_h * row_tolerance_ratio)

    with_center = sorted(
        ((b, (b[1] + b[3]) / 2.0) for b in boxes),
        key=lambda t: t[1],
    )

    rows: list[list[tuple[tuple[int, int, int, int], float]]] = []
    for box, cy in with_center:
        if rows and abs(cy - rows[-1][0][1]) <= row_tol:
            rows[-1].append((box, cy))
        else:
            rows.append([(box, cy)])

    out: list[tuple[int, int, int, int]] = []
    for row in rows:
        out.extend(b for b, _ in sorted(row, key=lambda t: t[0][0]))
    return out


def crop_boxes(img: Image.Image, boxes: list[tuple[int, int, int, int]]) -> list[Image.Image]:
    """按包围盒裁出一组子图（各自带一点透明余量）。"""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    out: list[Image.Image] = []
    for l, t, r, b in boxes:
        l2, t2 = max(0, l - 1), max(0, t - 1)
        r2, b2 = min(w, r + 1), min(h, b + 1)
        if r2 <= l2 or b2 <= t2:
            continue
        out.append(rgba.crop((l2, t2, r2, b2)))
    return out


def estimate_cell_count(img: Image.Image) -> int:
    """粗估图里大概有几个字（给界面做「自动框选」的初值）。

    用中位字宽去推：总内容宽度 / 中位字宽。
    """
    boxes = split_components(img)
    if not boxes:
        return 0
    widths = [b[2] - b[0] for b in boxes]
    widths = [w for w in widths if w > 0]
    if not widths:
        return len(boxes)
    return len(boxes)


# ─────────────────────────────────────────────────────────────
# 拼合（把多个字形合成一行 —— 填充引擎用）
# ─────────────────────────────────────────────────────────────


def paste_rgba(base: Image.Image, piece: Image.Image, x: int, y: int) -> Image.Image:
    """把 piece 贴到 base 的 (x, y)，返回新图。"""
    out = base.copy()
    out.paste(piece, (int(x), int(y)), piece)
    return out


def alpha_composite_at(base: Image.Image, piece: Image.Image, x: float, y: float) -> Image.Image:
    """alpha 合成到浮点坐标（内部取整），返回新图。"""
    out = base.copy()
    out.alpha_composite(piece, (int(round(x)), int(round(y))))
    return out


def solid_bbox(img: Image.Image, *, alpha_threshold: int = ALPHA_KEEP) -> tuple[float, float, float, float]:
    """返回内容包围盒（浮点），全空返回 (0,0,0,0)。"""
    box = content_bbox(img, alpha_threshold=alpha_threshold)
    if box is None:
        return (0.0, 0.0, 0.0, 0.0)
    return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))


# ─────────────────────────────────────────────────────────────
# 导出辅助
# ─────────────────────────────────────────────────────────────


def flatten_on_white(img: Image.Image) -> Image.Image:
    """把带透明的图压到白底上，转 RGB。导出 PNG/JPG 时用。"""
    rgba = img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, rgba).convert("RGB")


def px_to_mm_rect(rect_px: tuple[float, float, float, float], dpi: int) -> geo.Rect:
    """像素矩形 → mm 矩形（界面拖拽后回写参数用）。"""
    l, t, r, b = rect_px
    return geo.Rect(
        geo.px_to_mm(l, dpi),
        geo.px_to_mm(t, dpi),
        geo.px_to_mm(r, dpi),
        geo.px_to_mm(b, dpi),
    )
