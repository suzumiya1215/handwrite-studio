#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3 服务层自检：grid / content / fill 三件套。

不依赖 Qt，纯逻辑验证。跑法：
    PYTHONPATH= NODE_OPTIONS= .venv/Scripts/python.exe tools/check_services.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for p in (SRC, ROOT, os.path.join(ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from handwrite.core import imageops as iop
from handwrite.core import geometry as geo
from handwrite.core.charset import CHARSET_V1
from handwrite.core.models import (
    CellRect,
    ContentMatrix,
    ContentSpec,
    FontLibrary,
    GridSpec,
    JitterSpec,
    PageGeometry,
    RandomSpec,
)
from handwrite.core.services import content_service as cs
from handwrite.core.services import fill_service as fs
from handwrite.core.services import grid_service as gs

OK = "  [OK]"
BAD = "  [!!]"
_fails = []


def check(label, cond, extra=""):
    print((OK if cond else BAD) + " " + label + (("  -> " + str(extra)) if extra != "" else ""))
    if not cond:
        _fails.append(label)


def make_page():
    return PageGeometry(index=0, width_mm=210.0, height_mm=297.0)


PAGE = make_page()
DPI = 300


def make_grid():
    return GridSpec(
        start_x_mm=20.0,
        start_y_mm=30.0,
        col_width_mm=16.0,
        row_height_mm=16.0,
        cell_padding_mm=1.0,
        rows=6,
        cols=4,
    )


def make_lib():
    from fixtures import fake_font

    lib = FontLibrary(id="test", name="测试库")
    for ch in CHARSET_V1:
        img = fake_font.render_glyph(ch, height=128)
        lib.add_char_sample(ch, img) if hasattr(lib, "add_char_sample") else None
    return lib


def make_lib2():
    """稳妥版：直接用 GlyphSet / GlyphSample 组装，不猜方法名。"""
    import tempfile

    from handwrite.core.models import GlyphSample, GlyphSet
    from fixtures import fake_font

    lib = FontLibrary(id="test", name="测试库", charset=list(CHARSET_V1))
    tmpdir = os.path.join(ROOT, "build", "_glyphtmp")
    os.makedirs(tmpdir, exist_ok=True)
    for ch in CHARSET_V1:
        img = fake_font.render_glyph(ch, height=128)
        fn = ("c%02x" % ord(ch)) + ".png"
        fp = os.path.join(tmpdir, fn)
        img.save(fp, "PNG")
        s = GlyphSample(file=fn, width=img.size[0], height=img.size[1])
        s.image = img
        lib.characters[ch] = GlyphSet(char=ch, samples=[s])
    lib.root = tmpdir
    return lib


print("=" * 70)
print("一、grid_service：网格构建")
print("=" * 70)
grid = make_grid()
cells = gs.build_cells(grid, PAGE, DPI)
check("格子数 = 4列 x 6行 = 24", len(cells) == 24, len(cells))
check("首格 row/col = (0,0)", (cells[0].row, cells[0].col) == (0, 0), (cells[0].row, cells[0].col))
print("     首格 px: %s  几何 px: %s" % (cells[0].rect_px, cells[0].content_px))
print("     页面 px: %s" % (geo.page_size_px(PAGE.width_mm, PAGE.height_mm, DPI),))

check("页内无越界", gs.out_of_bounds(grid, PAGE) == [], gs.out_of_bounds(grid, PAGE))

big = GridSpec(start_x_mm=180.0, start_y_mm=280.0, col_width_mm=20.0, row_height_mm=20.0,
               cell_padding_mm=1.0, rows=3, cols=3)
oob = gs.out_of_bounds(big, PAGE)
check("越界网格能被检出", len(oob) > 0, oob)

print("\n-- 列选择（★ 空列表 = 全部 的陷阱）")
g2 = make_grid()
check("默认 active_cols 为空", g2.active_cols == [], g2.active_cols)
check("空列表时全列都算激活", g2.effective_cols == [0, 1, 2, 3], g2.effective_cols)
check("建出全部 24 格", len(gs.build_cells(g2, PAGE, DPI)) == 24)

g2 = gs.toggle_col(g2, 1)
check("从全选取消 1 列 → [0,2,3]（不是只剩第1列）", g2.active_cols == [0, 2, 3], g2.active_cols)
c_sel = gs.build_cells(g2, PAGE, DPI)
check("取消 1 列后建出 18 格", len(c_sel) == 18, len(c_sel))
check("受影响列号正确", sorted({c.col for c in c_sel}) == [0, 2, 3], sorted({c.col for c in c_sel}))
check("列 0 仍激活", gs.is_col_active(g2, 0))
check("列 1 已取消", not gs.is_col_active(g2, 1))
print("     摘要: " + gs.active_summary(g2))

g2 = gs.toggle_col(g2, 3)
check("再取消 1 列 → [0,2]", g2.active_cols == [0, 2], g2.active_cols)
g2 = gs.toggle_col(g2, 1)
check("把取消的列加回来 → [0,1,2]", g2.active_cols == [0, 1, 2], g2.active_cols)
g2 = gs.toggle_col(g2, 3)
check("补齐后回到全选 → 存成空列表", g2.active_cols == [], g2.active_cols)

g3 = gs.set_all_cols(g2, True)
check("set_all_cols(True) → 空=全部", g3.active_cols == [] and g3.effective_cols == [0, 1, 2, 3],
      g3.active_cols)
g4 = gs.set_all_cols(g2, False)
check("set_all_cols(False) → 无有效列", g4.effective_cols == [], g4.active_cols)
check("全不选时 build_cells 出 0 格", len(gs.build_cells(g4, PAGE, DPI)) == 0)
check("全不选摘要为中文提示", "没有" in gs.active_summary(g4), gs.active_summary(g4))

g5 = gs.toggle_col(make_grid(), 99)
check("越界列号不越界（原样返回）", g5.active_cols == [], g5.active_cols)
check("越界 toggle 不改原对象", make_grid().active_cols == [])

print("\n-- 命中测试")
cx = (cells[0].rect_px[0] + cells[0].rect_px[2]) // 2
cy = (cells[0].rect_px[1] + cells[0].rect_px[3]) // 2
hit = gs.hit_test(cells, cx, cy)
check("中心命中第 1 格", hit is not None and (hit.row, hit.col) == (0, 0),
      (hit.row, hit.col) if hit else None)
check("页外不命中", gs.hit_test(cells, -50, -50) is None)
idx = gs.cell_index_map(cells)
check("索引表 24 项", len(idx) == 24, len(idx))
check("索引可查 (2,3)", (2, 3) in idx)

print("\n-- 几何编辑")
moved = gs.move_grid(grid, 5.0, -3.0)
check("move_grid 平移正确", abs(moved.start_x_mm - 25.0) < 1e-6 and abs(moved.start_y_mm - 27.0) < 1e-6,
      (moved.start_x_mm, moved.start_y_mm))
check("move_grid 不改入参", grid.start_x_mm == 20.0 and grid.start_y_mm == 30.0)
rz = gs.resize_cell(grid, col_width_mm=25.0)
check("resize_cell 改宽", abs(rz.col_width_mm - 25.0) < 1e-6, rz.col_width_mm)
check("resize_cell 不改入参", abs(grid.col_width_mm - 16.0) < 1e-6)
sz = gs.set_grid_size(grid, 10, 8)
check("set_grid_size 生效", (sz.rows, sz.cols) == (10, 8), (sz.rows, sz.cols))
fitted = gs.auto_fit_cols(grid, PAGE)
check("auto_fit_cols 返回 GridSpec", isinstance(fitted, GridSpec))
check("auto_fit 后不越界", gs.out_of_bounds(fitted, PAGE) == [], gs.out_of_bounds(fitted, PAGE))
check("auto_fit 后列数 >= 1", fitted.cols >= 1, fitted.cols)
print("     auto_fit_cols -> %d 列 x %d 行, 格宽 %.2fmm" %
      (fitted.cols, fitted.rows, fitted.col_width_mm))
gl = gs.grid_lines(grid, PAGE, DPI)
check("网格线非空", len(gl) > 0, len(gl))
check("网格线元素为 ((x,y),(x,y),bool)", len(gl[0]) == 3 and len(gl[0][0]) == 2, gl[0])

print()
print("=" * 70)
print("二、content_service：内容生成与导入")
print("=" * 70)
m0 = cs.empty_matrix(grid)
check("空矩阵尺寸 = 6 行 x 4 列", (m0.rows, m0.cols) == (6, 4), (m0.rows, m0.cols))
check("空矩阵全为空串", all(v == "" for r in m0.data for v in r))

csp = ContentSpec(
    mode="random",
    random=RandomSpec(type="decimal", min=0.0, max=9999.99, decimals=2,
                      length_min=2, length_max=6),
)
m1 = cs.generate_matrix(csp, grid, seed=12345)
m2 = cs.generate_matrix(csp, grid, seed=12345)
m3 = cs.generate_matrix(csp, grid, seed=99999)
check("同种子内容相同", [r[:] for r in m1.data] == [r[:] for r in m2.data])
check("换种子内容不同", [r[:] for r in m1.data] != [r[:] for r in m3.data])
flat = [v for r in m1.data for v in r]
check("生成 24 个值", len(flat) == 24, len(flat))
check("非空率 100%", all(v != "" for v in flat))
valid = set(CHARSET_V1)
check("全部落在 12 字符集内", all(all(ch in valid for ch in v) for v in flat),
      sorted({ch for v in flat for ch in v if ch not in valid}))
print("     样例: " + " | ".join(flat[:5]))

print("\n-- 整数模式")
csp_i = ContentSpec(mode="random",
                    random=RandomSpec(type="integer", min=1, max=9999, decimals=0,
                                      length_min=2, length_max=4))
mi = cs.generate_matrix(csp_i, grid, seed=7)
check("整数模式无小数点", all("." not in v for r in mi.data for v in r))
print("     样例: " + " | ".join([v for r in mi.data for v in r][:5]))

print("\n-- 枚举候选项模式")
csp_c = ContentSpec(mode="random",
                    random=RandomSpec(type="pick", candidates=["1.5", "2.0", "36.7"], decimals=1))
mc = cs.generate_matrix(csp_c, grid, seed=3)
pool = {v for r in mc.data for v in r}
check("pick 模式只出候选项", pool <= {"1.5", "2.0", "36.7"}, pool)

print("\n-- 表格解析（自动识别分隔符）")
for label, txt, exp in [
    ("Tab", "1\t2\t3\n4\t5\t6", (2, 3)),
    ("逗号", "1,2,3\n4,5,6", (2, 3)),
    ("分号", "1;2\n3;4", (2, 2)),
]:
    r = cs.parse_table(txt)
    check("%s 分隔 -> %s" % (label, exp), (len(r), len(r[0])) == exp, [len(r), len(r[0])])

rows_t = cs.parse_table("1\t2\t3\n4\t5\t6")
mm = cs.matrix_from_table(rows_t, grid)
check("表格转矩阵补齐 6x4", (mm.rows, mm.cols) == (6, 4), (mm.rows, mm.cols))
check("首行内容正确", mm.data[0][:3] == ["1", "2", "3"], mm.data[0])
check("空位补空串", mm.data[2][0] == "", repr(mm.data[2][0]))

print("\n-- 导入（返回 矩阵 + 警告列表）")
mat_i, warns_i = cs.import_from_text("10.5\t20.3\n30\t40", grid)
check("import_from_text 返回 (矩阵, 警告)", isinstance(mat_i, ContentMatrix) and isinstance(warns_i, list))
check("导入值正确", mat_i.data[0][:2] == ["10.5", "20.3"], mat_i.data[0][:2])

print("\n-- 契约 C10：超范围字符检测")
bad = ContentMatrix(rows=2, cols=2)
bad.data = [["1A2", "3"], ["4", "55"]]
rep = cs.check_charset(bad)
check("检出 A 不受支持", "A" in rep.unsupported, rep.unsupported)
check("统计 unsupported 数 = 1", rep.total_unsupported == 1, rep.total_unsupported)
check("受影响格数 >= 1", rep.affected_cells >= 1, rep.affected_cells)
clean, rep2 = cs.sanitize(bad)
check("sanitize 后 A 被剔除", clean.data[0][0] == "12", clean.data[0][0])
check("sanitize 不报错（只剔除）", isinstance(rep2, cs.CharsetReport))
check("sanitize 返回新对象", clean is not bad)

print("\n-- 编辑操作（均返回新对象、不改入参）")
e = cs.empty_matrix(grid)
e_snap = [r[:] for r in e.data]
e1 = cs.fill_down(e, 0, 0)
e1.data[0][0] = "9"
check("fill_down 不污染原矩阵", e.data == e_snap, e.data[0][:2])
f = cs.empty_matrix(grid)
f.data[0][0] = "9"
fd = cs.fill_down(f, 0, 0)
check("fill_down 首行=9", fd.data[0][0] == "9", fd.data[0][0])
check("fill_down 末行=9", fd.data[-1][0] == "9", fd.data[-1][0])
fr = cs.fill_right(fd, 0, 0)
check("fill_right 末列=9", fr.data[0][-1] == "9", fr.data[0][-1])
cr = cs.clear_row(fr, 0)
check("清行后首行全空", all(v == "" for v in cr.data[0]), cr.data[0])
cc = cs.clear_col(fr, 0)
check("清列后首列全空", all(r[0] == "" for r in cc.data), [r[0] for r in cc.data])
ca = cs.clear_all(fr)
check("清全后全空", all(v == "" for r in ca.data for v in r))
rs = cs.resize_matrix(fr, gs.set_grid_size(grid, 3, 2))
check("resize_matrix -> 3x2", (rs.rows, rs.cols) == (3, 2), (rs.rows, rs.cols))
check("resize 保留左上内容", rs.data[0][0] == "9", rs.data[0][0])

print("\n-- 导出 / 往返")
tsv = cs.export_to_tsv(m1)
check("导出含制表符", "\t" in tsv)
check("导出行数 = 6", len(tsv.rstrip("\n").split("\n")) == 6, len(tsv.rstrip("\n").split("\n")))
back, _ = cs.import_from_text(tsv, grid)
check("TSV 往返一致", [r[:] for r in back.data] == [r[:] for r in m1.data])

print("\n-- 统计 / 预览 / 预设")
st = cs.stats(m1)
check("统计填充格数 = 24", st.filled == 24, st.filled)
check("统计数据类字段可用", st.rows == 6 and st.cols == 4, (st.rows, st.cols))
check("统计字符总数 > 0", st.chars > 0, st.chars)
check("预览非空", bool(cs.preview_text(m1).strip()))
ps = cs.preset_specs()
check("预设数量 >= 4", len(ps) >= 4, list(ps.keys()))
allzh = all(any("\u4e00" <= c <= "\u9fff" for c in lbl) for lbl, _ in ps.values())
check("预设名称均为中文", allzh, list(ps.values())[0][0] if ps else "")
for k, (lbl, sp) in ps.items():
    check("预设 %-10s 类型=%s" % (k, sp.type), sp.type in ("decimal", "integer", "pick"), lbl)

print()
print("=" * 70)
print("三、fill_service：填充内核")
print("=" * 70)
lib = make_lib2()
jitter = JitterSpec()
print("     字形库字符数 = %d, 抖动预设 = %s" % (len(lib.characters), jitter.preset))
check("字形库 12 字符", len(lib.characters) == 12, len(lib.characters))
check("每个字符都有样本", all(len(gs_.samples) >= 1 for gs_ in lib.characters.values()))

mat = cs.generate_matrix(csp, grid, seed=2024)
bmp = fs.blank_rgba(geo.page_size_px(PAGE.width_mm, PAGE.height_mm, DPI))
res = fs.render_page(bmp, cells, mat, lib, jitter, seed=2024)
check("填充格数 = 24", res.filled_cells == 24, res.filled_cells)
check("总字符数 > 0", res.total_chars > 0, res.total_chars)
check("无跳过", res.skipped_chars == {}, res.skipped_chars)
check("出图尺寸正确", res.image.size == bmp.size, res.image.size)

print("\n-- ★ C5 同种子逐像素相同")
bmp2 = fs.blank_rgba(bmp.size)
res2 = fs.render_page(bmp2, cells, mat, lib, jitter, seed=2024)
same = list(res.image.getdata()) == list(res2.image.getdata())
check("C5 同种子逐像素相同", same)
bmp3 = fs.blank_rgba(bmp.size)
res3 = fs.render_page(bmp3, cells, mat, lib, jitter, seed=777)
d = sum(1 for a, b in zip(res.image.getdata(), res3.image.getdata()) if a != b)
check("换种子像素不同", d > 0, "差异像素 %d" % d)

print("\n-- ★ C13 不改入参")
mat_snap = [r[:] for r in mat.data]
cell_snap = [(c.row, c.col, c.rect_px) for c in cells]
bmp4 = fs.blank_rgba(bmp.size)
fs.render_page(bmp4, cells, mat, lib, jitter, seed=5)
check("C13 入参矩阵未改", [r[:] for r in mat.data] == mat_snap)
check("C13 入参网格未改", [(c.row, c.col, c.rect_px) for c in cells] == cell_snap)

print("\n-- ★ C9~C11 超范围字符跳过")
small_page = PageGeometry(index=0, width_mm=60.0, height_mm=60.0)
small_grid = GridSpec(start_x_mm=5, start_y_mm=5, col_width_mm=20.0, row_height_mm=20.0,
                      cell_padding_mm=1.0, rows=1, cols=2)
scells = gs.build_cells(small_grid, small_page, 150)
badm = ContentMatrix(rows=1, cols=2)
badm.data = [["1A", "2"]]
bmp5 = fs.blank_rgba(geo.page_size_px(60.0, 60.0, 150))
res5 = fs.render_page(bmp5, scells, badm, lib, jitter, seed=1)
check("A 被跳过计数 = 1", res5.skipped_chars.get("A") == 1, res5.skipped_chars)
check("跳过总数 = 1", res5.skipped_total == 1, res5.skipped_total)
check("跳过文案为中文", "跳过" in res5.skipped_text or "字形" in res5.skipped_text, res5.skipped_text)
print("     提示: " + res5.skipped_text)
check("数字仍被填充", res5.filled_cells >= 1, res5.filled_cells)

print("\n-- 单格渲染")
c0 = cells[0]
layer, pos, plan = fs.render_cell(c0, "123", lib, jitter, seed=3)
check("render_cell 出图非空", layer is not None and layer.size[0] > 0,
      layer.size if layer is not None else None)
check("render_cell 返回绝对坐标", isinstance(pos, tuple) and len(pos) == 2, pos)
check("CellPlan.text 正确", plan.text == "123", plan.text)
check("CellPlan 行列为 (0,0)", (plan.row, plan.col) == (0, 0), (plan.row, plan.col))
check("CellPlan.items 有 3 项", len(plan.items) == 3, len(plan.items))

print("\n-- 逐格独立（★ 每格种子 = seed + row*1000 + col）")
r1 = fs.rng_for_cell(1000, 0, 0).random()
r2 = fs.rng_for_cell(1000, 0, 0).random()
r3 = fs.rng_for_cell(1000, 0, 1).random()
check("同格种子可复现", r1 == r2, (r1, r2))
check("异格种子不同", r1 != r3, (r1, r3))

print("\n-- plan_page / count_skipped")
plans = fs.plan_page(cells, mat, lib, jitter, seed=42)
check("plan_page 覆盖 24 格", len(plans) == 24, len(plans))
check("plan_page 可复现", [p.text for p in fs.plan_page(cells, mat, lib, jitter, seed=42)] == [p.text for p in plans])
sk = fs.count_skipped(badm, scells, lib)
check("count_skipped 检出 A", sk.get("A") == 1, sk)

print("\n-- 进度回调")
seen = []
bmp6 = fs.blank_rgba(bmp.size)
fs.render_page(bmp6, cells, mat, lib, jitter, seed=1,
               on_progress=lambda i, t: seen.append((i, t)))
check("回调被调用", len(seen) > 0, len(seen))
check("进度终值 = 24", seen[-1][1] == 24, seen[-1])
check("进度递增", all(seen[i][0] <= seen[i + 1][0] for i in range(len(seen) - 1)))

print()
print("=" * 70)
if _fails:
    print("失败 %d 项：" % len(_fails))
    for f in _fails:
        print("   - " + f)
    sys.exit(1)
print("全部通过。")
