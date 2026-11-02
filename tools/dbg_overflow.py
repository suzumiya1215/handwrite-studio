#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断：每个格子的绘制内容是否真的超出自己的格子边界。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "src"), os.path.join(ROOT, "tests")):
    sys.path.insert(0, p)

from fixtures import fake_font
from handwrite.core.charset import CHARSET_V1
from handwrite.core.models import (
    ContentSpec,
    FontLibrary,
    GlyphSample,
    GlyphSet,
    GridSpec,
    JitterSpec,
    PageGeometry,
    RandomSpec,
)
from handwrite.core.services import content_service as cts
from handwrite.core.services import fill_service as fls
from handwrite.core.services import grid_service as grs

D = os.path.join(ROOT, "build", "_svc2")
lib = FontLibrary(id="t", name="库", charset=list(CHARSET_V1))
for ch in CHARSET_V1:
    im = fake_font.render_glyph(ch, height=128)
    fn = ("c%02x" % ord(ch)) + ".png"
    im.save(os.path.join(D, "_glyphs", fn), "PNG")
    s = GlyphSample(file=fn, width=im.size[0], height=im.size[1])
    s.image = im
    lib.characters[ch] = GlyphSet(char=ch, samples=[s])

page = PageGeometry(index=0, width_mm=210.0, height_mm=297.0)
grid = GridSpec(start_x_mm=20, start_y_mm=50, col_width_mm=24, row_height_mm=16,
                cell_padding_mm=2.0, rows=6, cols=5)
cells = grs.build_cells(grid, page, 200)
csp = ContentSpec(mode="random",
                  random=RandomSpec(type="decimal", min=0, max=99.9, decimals=1,
                                    length_min=3, length_max=5))
mat = cts.generate_matrix(csp, grid, seed=88)

print("格宽 px = %d, 内容区宽 px = %d" %
      (cells[0].rect_px[2] - cells[0].rect_px[0],
       cells[0].content_px[2] - cells[0].content_px[0]))
print()

over = 0
for c in cells[:10]:
    plan = fls.plan_page([c], mat, lib, JitterSpec(), seed=88)[0]
    cl, ct, cr, cb = c.content_px
    xs0 = [it[2][0] for it in plan.items]
    xs1 = [it[2][2] for it in plan.items]
    ys0 = [it[2][1] for it in plan.items]
    ys1 = [it[2][3] for it in plan.items]
    if not xs0:
        continue
    wsum = sum(it[2][2] - it[2][0] for it in plan.items)
    span = max(xs1) - min(xs0)
    bad = min(xs0) < cl - 0.5 or max(xs1) > cr + 0.5 or min(ys0) < ct - 0.5 or max(ys1) > cb + 0.5
    if bad:
        over += 1
    print("格(%d,%d) %-6s 字号总宽=%6.1f 跨度=%6.1f 内容区=%d  超界=%s"
          % (c.row, c.col, plan.text, wsum, span, int(cr - cl), "★是" if bad else "否"))
print()
print("超界格数 = %d / %d" % (over, len(cells)))

# 标点尺寸统计
print()
for c in cells[:6]:
    plan = fls.plan_page([c], mat, lib, JitterSpec(), seed=88)[0]
    dots = [(it[0], round(it[2][2] - it[2][0], 1), round(it[2][3] - it[2][1], 1))
            for it in plan.items if it[0] in ".,"]
    digits = [(round(it[2][2] - it[2][0], 1), round(it[2][3] - it[2][1], 1))
              for it in plan.items if it[0] not in ".,"]
    if dots:
        print("格(%d,%d) 标点(宽,高)=%s  数字尺寸=%s" % (c.row, c.col, dots, digits[:2]))
