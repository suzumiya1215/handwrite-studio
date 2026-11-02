#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""调试：填充后图像是否真的有黑字。"""
import collections
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
from handwrite.core.services import template_service as tps

D = os.path.join(ROOT, "build", "_svc2")

lib = FontLibrary(id="t", name="测试库", charset=list(CHARSET_V1))
gdir = os.path.join(D, "_glyphs")
os.makedirs(gdir, exist_ok=True)
for ch in CHARSET_V1:
    im = fake_font.render_glyph(ch, height=128)
    fn = ("c%02x" % ord(ch)) + ".png"
    im.save(os.path.join(gdir, fn), "PNG")
    s = GlyphSample(file=fn, width=im.size[0], height=im.size[1])
    s.image = im
    lib.characters[ch] = GlyphSet(char=ch, samples=[s])

doc = tps.open_document(os.path.join(D, "tpl1.pdf"))
w, h = tps.page_size(doc, 0)
page = PageGeometry(index=0, width_mm=w, height_mm=h)
grid = GridSpec(start_x_mm=20, start_y_mm=40, col_width_mm=20, row_height_mm=14,
                cell_padding_mm=1.5, rows=8, cols=6)
cells = grs.build_cells(grid, page, 200)
csp = ContentSpec(mode="random",
                  random=RandomSpec(type="decimal", min=0, max=99.9, decimals=1,
                                    length_min=2, length_max=5))
mat = cts.generate_matrix(csp, grid, seed=2026)

bg = tps.render_page(doc, 0, 200)
print("底图 size=%s mode=%s" % (bg.size, bg.mode))
cb = collections.Counter(bg.convert("RGB").getdata())
print("底图颜色种类 =%d  top3=%s" % (len(cb), cb.most_common(3)))

res = fls.render_page(bg, cells, mat, lib, JitterSpec(), seed=2026)
print("成图 size=%s mode=%s" % (res.image.size, res.image.mode))
res.image.save(os.path.join(D, "dbg_res.png"))
cr = collections.Counter(res.image.convert("RGB").getdata())
print("成图颜色种类 =%d  top5=%s" % (len(cr), cr.most_common(5)))
dark = sum(v for k, v in cr.items() if sum(k) < 400)
print("暗像素数 =%d" % dark)

c0 = cells[0]
print("首格 rect_px=%s" % (c0.rect_px,))
crop = res.image.crop(c0.rect_px).convert("RGB")
cc = collections.Counter(crop.getdata())
print("首格颜色种类 =%d  top3=%s" % (len(cc), cc.most_common(3)))
darkc = sum(v for k, v in cc.items() if sum(k) < 400)
print("首格暗像素 =%d" % darkc)

# 直接看 render_cell 的输出
layer, pos, plan = fls.render_cell(c0, mat.data[0][0], lib, JitterSpec(), seed=2026)
print("render_cell 文本=%r layer=%s pos=%s" %
      (plan.text, layer.size if layer is not None else None, pos))
if layer is not None:
    cl = collections.Counter(layer.convert("RGBA").getdata())
    print("单格层 top3=%s" % cl.most_common(3))
    layer.save(os.path.join(D, "dbg_cell.png"))
