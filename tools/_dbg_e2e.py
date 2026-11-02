"""临时调试：把网格/内容/填充的中间产物落盘，人工眼检 + 打印诊断。"""
from __future__ import annotations
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image, ImageDraw
from handwrite.core.services.session_service import DocumentSession
from handwrite.core.services import fill_service as fs2
from tests.fixtures.fake_font import make_fake_font, write_font_to_dir

def dark(img):
    if img.mode!="L": img=img.convert("L")
    px=img.load(); n=0
    for y in range(0,img.height,4):
        for x in range(0,img.width,4):
            if px[x,y]<60: n+=1
    return n

def _make_blank_form(path, dpi=200):
    w_mm, h_mm = 210, 297
    w = int(w_mm/25.4*dpi); h = int(h_mm/25.4*dpi)
    img = Image.new("RGBA",(w,h),(255,255,255,255)); d=ImageDraw.Draw(img)
    d.rectangle([int(w*0.05),int(h*0.04),int(w*0.95),int(h*0.96)],outline=(120,120,120),width=3)
    for c in range(1,10):
        x=int(w*0.05+(w*0.90)*c/10); d.line([(x,int(h*0.13)),(x,int(h*0.95))],fill=(200,200,200),width=2)
    for r in range(1,25):
        y=int(h*0.13+(h*0.82)*r/25); d.line([(int(w*0.05),y),(int(w*0.95),y)],fill=(200,200,200),width=1)
    img.convert("RGB").save(path,"PNG")

form=r"D:\tmp_hw\台账.png"; os.makedirs(r"D:\tmp_hw",exist_ok=True)
_make_blank_form(form)
fdir=r"D:\tmp_hw\fonts\test-font"
lib=make_fake_font(samples_per_char=3,height=128,name="测试字迹")
write_font_to_dir(lib,fdir)

s=DocumentSession()
s.open_template(form)
print("page_size_mm=", s.page_size_mm(0))
print("page_bitmap size=", s.page_bitmap(0).size)
s.load_font(fdir)
s.apply_grid(rows=20,cols=8,start_x_mm=10.5,start_y_mm=38.0,col_width_mm=21.0,row_height_mm=9.6,cell_padding_mm=1.5)
s.apply_content(mode="random",min=0,max=999999,length_min=3,length_max=8,decimals=0)
s.apply_jitter({"preset":"natural"}); s.apply_seed(20261009)
print("session jitter=", s._jitter())
cells=s.cells(0); print("cells count=",len(cells))
if cells: print("first cell rect_px=", cells[0].rect_px)
m=s.matrix(0); print("matrix=",type(m))
base,fill,result=s.fill_page(0)
print("base size=",base.size,"fill size=",fill.size if fill else None,"result=",result)
print("fill is base?", fill is base, "fill mode=", fill.mode if fill else None)
print("fill dark immediate=", dark(fill) if fill else -1)
# explicit render_page with session jitter
print("s._seed()=", s._seed())
print("fs2.render_page is s.fill.render_page?", fs2.render_page is s.fill.render_page)
print("fs2.render_page code file=", fs2.render_page.__code__.co_filename)
print("s.fill.render_page code file=", s.fill.render_page.__code__.co_filename)
res_jit = fs2.render_page(base, cells, m, lib, s._jitter(), s._seed())
print("render_page session-jitter dark=", dark(res_jit.image))
print("res_jit.image is base?", res_jit.image is base)
# call via s.fill.render_page
res_via = s.fill.render_page(base, cells, m, s.lib, s._jitter(), s._seed())
print("render_page via s.fill dark=", dark(res_via.image))
# compare libs
gs_lib = lib.get("5"); gs_s = s.lib.get("5")
print("lib glyph 5 mode/size/dark=", gs_lib.samples[0].image.mode, gs_lib.samples[0].image.size, dark(gs_lib.samples[0].image))
print("s.lib glyph 5 mode/size/dark=", gs_s.samples[0].image.mode, gs_s.samples[0].image.size, dark(gs_s.samples[0].image))
print("lib is s.lib?", lib is s.lib)
if result: print("result attrs=", [a for a in dir(result) if not a.startswith('_')])
base.save(r"D:\tmp_hw\base.png")
if fill is not None: fill.save(r"D:\tmp_hw\fill.png")
# dark count both
def dark(img):
    if img.mode!="L": img=img.convert("L")
    px=img.load(); n=0
    for y in range(0,img.height,4):
        for x in range(0,img.width,4):
            if px[x,y]<60: n+=1
    return n
print("base dark=",dark(base),"fill dark=",dark(fill) if fill else -1)

# inspect a single glyph sample
print("lib chars=", list(lib.characters.keys())[:5])
gs=lib.get("5")
print("samples for '5'=", len(gs.samples) if gs else None)
if gs:
    s=gs.samples[0]
    print("sample 5 size=", s.image.size if s.image else None, "mode=", s.image.mode if s.image else None)
    s.image.save(r"D:\tmp_hw\glyph_5.png")
    print("glyph_5 dark=", dark(s.image))

# inspect matrix content
print("matrix text at first few cells:")
for c in cells[:3]:
    print("  ", c.row,c.col, m.at(c.row,c.col))

# inspect one cell render
from handwrite.core.services import fill_service as fs2
from handwrite.core.models.jitter import JitterSpec
cell=cells[0]
text=m.at(cell.row,cell.col)
layer,(lx,ly),plan=fs2.render_cell(cell,text,lib,JitterSpec(),20261009)
print("layer size=", layer.size if layer else None, "pos=",(lx,ly), "items=",len(plan.items))
if layer:
    layer.save(r"D:\tmp_hw\cell_layer.png")
    print("layer dark=", dark(layer))
    # manual composite onto base
    test = base.convert("RGBA").copy()
    print("test mode/size before=", test.mode, test.size)
    test.alpha_composite(layer, (int(round(lx)), int(round(ly))))
    test.save(r"D:\tmp_hw\manual_fill.png")
    print("manual fill dark=", dark(test))

# also test render_page directly with default jitter and session jitter
from handwrite.core.services.fill_service import render_page
res2 = render_page(base, cells[:1], m, lib, JitterSpec(), 20261009)
print("render_page 1-cell result=", res2, "fill dark=", dark(res2.image))
res2.image.save(r"D:\tmp_hw\one_cell_render_page.png")

for n in [2,5,10,20,40,80,120,160]:
    resn = render_page(base, cells[:n], m, lib, JitterSpec(), 20261009)
    print(f"render_page {n}-cell dark=", dark(resn.image), "filled=", resn.filled_cells)
