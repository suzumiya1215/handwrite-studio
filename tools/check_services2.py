#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3 服务层第二批自检：template / export / project。

跑法：
    PYTHONPATH= NODE_OPTIONS= .venv/Scripts/python.exe tools/check_services2.py
"""
import io
import json
import os
import sys
import threading
import zipfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
TMP = os.path.join(ROOT, "build", "_svc2")
for p in (SRC, ROOT, os.path.join(ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from PIL import Image

from handwrite.core import errors as E
from handwrite.core import geometry as geo
from handwrite.core.charset import CHARSET_V1
from handwrite.core.models import (
    ContentMatrix,
    ContentSpec,
    FontLibrary,
    GlyphSample,
    GlyphSet,
    GridSpec,
    JitterSpec,
    PageGeometry,
    Project,
    RandomSpec,
)
from handwrite.core.services import content_service as cts
from handwrite.core.services import export_service as exs
from handwrite.core.services import fill_service as fls
from handwrite.core.services import project_service as prs
from handwrite.core.services import template_service as tps

OK = "  [OK]"
BAD = "  [!!]"
_fails = []


def check(label, cond, extra=""):
    print((OK if cond else BAD) + " " + label + (("  -> " + str(extra)) if extra != "" else ""))
    if not cond:
        _fails.append(label)


def clean_tmp():
    import shutil

    if os.path.isdir(TMP):
        shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)


clean_tmp()
D = TMP


def make_pdf(path, pages=2, w=595, h=842):
    """造一份多页 PDF 当模板。"""
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page(width=w, height=h)
        p.insert_text((72, 100), f"Template page {i + 1}", fontsize=14)
    doc.save(str(path))
    doc.close()
    return str(path)


def make_lib():
    from fixtures import fake_font

    lib = FontLibrary(id="test", name="测试库", charset=list(CHARSET_V1))
    gdir = os.path.join(D, "_glyphs")
    os.makedirs(gdir, exist_ok=True)
    for ch in CHARSET_V1:
        img = fake_font.render_glyph(ch, height=128)
        fn = ("c%02x" % ord(ch)) + ".png"
        img.save(os.path.join(gdir, fn), "PNG")
        s = GlyphSample(file=fn, width=img.size[0], height=img.size[1])
        s.image = img
        lib.characters[ch] = GlyphSet(char=ch, samples=[s])
    return lib


print("=" * 70)
print("四、template_service：模板载入")
print("=" * 70)

pdf1 = make_pdf(os.path.join(D, "tpl1.pdf"), pages=1)
pdf3 = make_pdf(os.path.join(D, "tpl3.pdf"), pages=3)

doc = tps.open_document(pdf1)
check("打开 PDF 成功", doc is not None)
check("格式标记为 pdf", doc.source_format == "pdf", doc.source_format)
check("页数 = 1", tps.page_count(doc) == 1, tps.page_count(doc))
w, h = tps.page_size(doc, 0)
check("A4 尺寸识别正确（595x842pt → 210x297mm）",
      abs(w - 210.0) < 1.0 and abs(h - 297.0) < 1.0, (round(w, 2), round(h, 2)))
check("describe 中文可读", "PDF模板" in tps.describe(doc), tps.describe(doc))
check("无警告", doc.warnings == [], doc.warnings)
tps.close_document(doc)

doc3 = tps.open_document(pdf3)
check("多页 PDF 页数 = 3", tps.page_count(doc3) == 3, tps.page_count(doc3))
img = tps.render_page(doc3, 1, 150)
check("渲染第 2 页出图", img is not None and img.size[0] > 0, img.size)
exp_w = geo.mm_to_px(210.0, 150)
check("150dpi A4 宽约 1240px", abs(img.size[0] - exp_w) <= 2, (img.size[0], round(exp_w)))
check("渲染结果是 RGBA", img.mode == "RGBA", img.mode)
img300 = tps.render_page(doc3, 0, 300)
check("300dpi 尺寸约翻倍", abs(img300.size[0] - geo.mm_to_px(210.0, 300)) <= 2, img300.size)
tps.close_document(doc3)

png = os.path.join(D, "pic.png")
Image.new("RGB", (1200, 800), "white").save(png)
imgdoc = tps.open_document(png)
check("打开图片成功", imgdoc.source_format == "image", imgdoc.source_format)
check("图片只有 1 页", tps.page_count(imgdoc) == 1)
check("图片按 DPI 反推尺寸（1200px@96dpi≈317mm）",
      abs(tps.page_size(imgdoc, 0)[0] - 317.5) < 2.0, round(tps.page_size(imgdoc, 0)[0], 1))
check("无 DPI 时有中文警告", len(imgdoc.warnings) >= 1, imgdoc.warnings)

print("\n-- 错误路径")
try:
    tps.open_document(os.path.join(D, "不存在.pdf"))
    check("不存在的文件报错", False)
except E.AppError as exc:
    check("不存在的文件报中文错", "不存在" in exc.user_message, exc.user_message)
try:
    bad = os.path.join(D, "a.xyz")
    Path(bad).write_bytes(b"xx")
    tps.open_document(bad)
    check("不支持的格式报错", False)
except E.AppError as exc:
    check("不支持的格式中文提示", "不支持" in exc.user_message, exc.user_message)
check("is_supported 判断正确", tps.is_supported(pdf1) and not tps.is_supported("a.xyz"))
check("suggest_page_size 对无模板给 A4", tps.suggest_page_size(None) == (210.0, 297.0))
check("is_a4 判定正确", tps.is_a4(210, 297) and tps.is_a4(297, 210) and not tps.is_a4(100, 100))

print()
print("=" * 70)
print("五、export_service：导出")
print("=" * 70)
svc = exs.ExportService()

# PDF
pages = [(i, Image.new("RGB", (600, 800), "white")) for i in range(3)]
out_pdf = str(Path(D) / "o1.pdf")
r = svc.export_pdf(pages, None, out_pdf)
check("PDF 文件已生成", Path(r).exists() and Path(r).stat().st_size > 0,
      "%d 字节" % Path(r).stat().st_size)
check("verify_pdf 页数 = 3", svc.verify_pdf(r, 3))
import pymupdf

with pymupdf.open(r) as d:
    check("页数与输入一致", d.page_count == 3, d.page_count)
    check("页面尺寸有效", d[0].rect.width > 0 and d[0].rect.height > 0,
          (round(d[0].rect.width, 1), round(d[0].rect.height, 1)))

# 用模板尺寸
doc_t = tps.open_document(pdf3)
pages_t = [(i, Image.new("RGB", (400, 560), "white")) for i in range(3)]
out_t = str(Path(D) / "o_tpl.pdf")
svc.export_pdf(pages_t, doc_t, out_t, dpi=150, project=None)
with pymupdf.open(out_t) as d:
    wpt = geo.mm_to_pt(210.0)
    check("PDF 页面用模板毫米尺寸", abs(d[0].rect.width - wpt) < 1.5,
          (round(d[0].rect.width, 1), round(wpt, 1)))
tps.close_document(doc_t)

# PNG
png_dir = str(Path(D) / "pngout")
out_pngs = svc.export_png([(0, Image.new("RGB", (100, 100), "white")),
                           (1, Image.new("RGB", (100, 100), "white"))], png_dir)
check("PNG 生成 2 个文件", len(out_pngs) == 2, len(out_pngs))
check("PNG 尺寸正确", Image.open(out_pngs[0]).size == (100, 100),
      Image.open(out_pngs[0]).size)
out_named = svc.export_png([(2, Image.new("RGB", (50, 50), "white"))],
                           str(Path(D) / "png2"),
                           name_pattern="第{page}页.png")
check("命名模板生效（0-based 页码 +1 → 第 3 页）",
      os.path.basename(out_named[0]).startswith("第3页"), os.path.basename(out_named[0]))

# RGBA → RGB 白底
rgba = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
out_rgba = svc.export_png([(0, rgba)], str(Path(D) / "rgbad"), name_pattern="t.png")
check("透明图导出后为 RGB", Image.open(out_rgba[0]).mode == "RGB",
      Image.open(out_rgba[0]).mode)
check("透明处变白不是黑", Image.open(out_rgba[0]).getpixel((5, 5))[0] > 240,
      Image.open(out_rgba[0]).getpixel((5, 5)))

print("\n-- 命名建议")
p = Project(id="x", name="晨午检表", font_id="f")
p.template_path = str(Path(D) / "晨午检表.pdf")
s1 = svc.suggest_output_path(p, "pdf")
check("建议路径含「手写版」", "手写版" in s1, os.path.basename(s1))
check("建议路径后缀 .pdf", s1.endswith(".pdf"), s1)
check("建议路径与模板同目录", Path(s1).parent == Path(D), str(Path(s1).parent))
Path(s1).write_bytes(b"x")
s2 = svc.suggest_output_path(p, "pdf")
check("重名自动加 (1)", "(1)" in os.path.basename(s2), os.path.basename(s2))
check("目录建议含 _导出", "_导出" in os.path.basename(svc.suggest_output_dir(p)),
      os.path.basename(svc.suggest_output_dir(p)))

print("\n-- 覆盖保护 & 取消")
try:
    svc.export_pdf([(0, Image.new("RGB", (50, 50), "white"))], None, out_pdf)
    check("已存在文件会拒绝写", False)
except E.AppError as exc:
    check("已存在文件给中文提示", "已经存在" in exc.user_message, exc.user_message)

ev = threading.Event()
ev.set()
try:
    svc.export_pdf([(0, Image.new("RGB", (50, 50), "white"))], None,
                   str(Path(D) / "cancel.pdf"), cancel_flag=ev)
    check("取消标志生效", False)
except E.ExportCancelledError:
    check("取消抛 ExportCancelledError", True)
check("取消后不留半个文件", not (Path(D) / "cancel.pdf").exists())
try:
    svc.export_png([(0, Image.new("RGB", (50, 50), "white"))],
                   str(Path(D) / "canceld"), cancel_flag=ev)
    check("PNG 也支持取消", False)
except E.ExportCancelledError:
    check("PNG 取消生效", True)

print()
print("=" * 70)
print("六、project_service：项目存取")
print("=" * 70)
psvc = prs.ProjectService(data_dir=os.path.join(D, "data"))


def make_project() -> Project:
    p = Project(id="p1", name="测试项目", font_id="f1", font_name="我的字迹")
    p.created_at = prs.now_iso()
    p.pages = []
    from handwrite.core.models import PageSettings

    p.pages = [PageSettings(index=0, grid=GridSpec(rows=3, cols=4))]
    p.page_count = 1
    p.seed = 12345
    return p


proj = make_project()
proj_path = str(Path(D) / "t.hwpj")
saved = psvc.save(proj, proj_path)
check("保存成功", Path(saved).exists() and Path(saved).stat().st_size > 0,
      "%d 字节" % Path(saved).stat().st_size)
check("是合法 zip", zipfile.is_zipfile(saved))
with zipfile.ZipFile(saved) as z:
    names = z.namelist()
    check("含 project.json", "project.json" in names, names)
    meta = json.loads(z.read("project.json").decode("utf-8"))
check("format = hwpj", meta.get("format") == "hwpj", meta.get("format"))
check("version = 1", meta.get("version") == 1, meta.get("version"))
check("中文不转义（name 原样）", meta.get("name") == "测试项目", meta.get("name"))

back = psvc.load(saved)
check("往返 id 一致", back.id == "p1", back.id)
check("往返 name 一致", back.name == "测试项目", back.name)
check("往返 seed 一致", back.seed == 12345, back.seed)
check("往返行数一致", back.grid_for(0).rows == 3, back.grid_for(0).rows)
check("往返列数一致", back.grid_for(0).cols == 4, back.grid_for(0).cols)
check("load 后 dirty = False", back.dirty is False, back.dirty)

proj2 = make_project()
proj2.grid_for(0).col_width_mm = 17.5
proj2.grid_for(0).active_cols = [1, 3]
p2 = str(Path(D) / "t2.hwpj")
psvc.save(proj2, p2)
b2 = psvc.load(p2)
check("格宽往返保真", abs(b2.grid_for(0).col_width_mm - 17.5) < 1e-9,
      b2.grid_for(0).col_width_mm)
check("活动列往返保真", b2.grid_for(0).active_cols == [1, 3], b2.grid_for(0).active_cols)

from handwrite.core.models import JitterSpec

proj3 = make_project()
proj3.jitter = JitterSpec.from_preset("strong")
p3 = str(Path(D) / "t3.hwpj")
psvc.save(proj3, p3)
b3 = psvc.load(p3)
check("抖动预设往返保真",
      abs(b3.jitter.cell.rotate_deg - proj3.jitter.cell.rotate_deg) < 1e-9,
      (b3.jitter.cell.rotate_deg, proj3.jitter.cell.rotate_deg))

print("\n-- 模板内嵌")
proj_t = make_project()
proj_t.template_path = pdf3
p_t = str(Path(D) / "t_tpl.hwpj")
psvc.save(proj_t, p_t)
with zipfile.ZipFile(p_t) as z:
    check("容器内有 template/", any(n.startswith("template/") for n in z.namelist()),
          z.namelist())
    check("模板字节可取出", len(z.read("template/template.pdf")) > 0)
    check("模板 meta 存在", "template/meta.json" in z.namelist())
tb = psvc.load_template_bytes(p_t)
check("load_template_bytes 有内容", tb is not None and len(tb) > 0)

print("\n-- 容错")
fake = Project(id="nf", name="找不到库的项目")
fake.pages = []
from handwrite.core.models import PageSettings

fake.pages = [PageSettings(index=0, grid=GridSpec(rows=3, cols=4))]
fake.page_count = 1
p_nf = str(Path(D) / "t_nf.hwpj")
psvc.save(fake, p_nf)
_orig = psvc._font_exists
psvc._font_exists = lambda fid: False
bnf = psvc.load(p_nf)
check("找不到字形库不致命", bnf is not None)
check("标记 unresolved_font", bnf.unresolved_font is True, bnf.unresolved_font)
check("网格参数仍保住", bnf.grid_for(0).rows == 3, bnf.grid_for(0).rows)
check("有中文警告说明", any("字形库" in w for w in bnf.warnings), bnf.warnings)
psvc._font_exists = _orig

badv = Path(D) / "badver.hwpj"
with zipfile.ZipFile(badv, "w") as z:
    z.writestr("project.json", json.dumps(
        {"format": "hwpj", "version": 999, "id": "x", "name": "y"}))
try:
    psvc.load(badv)
    check("版本过高被拒", False)
except E.AppError as exc:
    check("版本过高提示升级", ("升级" in exc.user_message or "更新版本" in exc.user_message),
          exc.user_message)

bzip = Path(D) / "corrupt.hwpj"
bzip.write_bytes(b"not a zip at all")
try:
    psvc.load(bzip)
    check("损坏文件被拒", False)
except E.AppError as exc:
    check("损坏文件中文提示", len(exc.user_message) > 0, exc.user_message)

noent = Path(D) / "noentry.hwpj"
with zipfile.ZipFile(noent, "w") as z:
    z.writestr("other.txt", "x")
try:
    psvc.load(noent)
    check("缺 project.json 被拒", False)
except E.AppError as exc:
    check("缺入口文件中文提示", "key" in exc.user_message or "project.json" in exc.user_message,
          exc.user_message)

print("\n-- 最近项目")
recents = psvc.list_recent()
check("保存后进最近列表", any(e.path == saved for e in recents),
      [e.name for e in recents])
check("最近条目有名字", any(e.name == "测试项目" for e in recents),
      [(e.name, e.path) for e in recents])
check("存在的文件 exists=True",
      all(e.exists for e in recents if e.path.startswith(D)), )

for i in range(15):
    p = make_project()
    p.id = f"p{i}"
    p.name = f"项目{i}"
    psvc.save(p, str(Path(D) / f"r{i}.hwpj"))
check("最近列表截断到 10 条", len(psvc.list_recent(limit=10)) == 10,
      len(psvc.list_recent(limit=10)))
check("默认上限也是 10", len(psvc.list_recent()) <= prs.MAX_RECENT,
      len(psvc.list_recent()))
psvc.remove_recent(saved)
check("移除最近条目生效", not any(e.path == saved for e in psvc.list_recent()))

print("\n-- 自动保存")
proj_a = make_project()
a1 = psvc.autosave(proj_a)
check("自动保存文件已生成", Path(a1).exists(), a1)
proj_a.name = "改过名字"
a2 = psvc.autosave(proj_a)
check("同一项目自动保存覆盖同一路径", a1 == a2, (a1, a2))
check("自动保存不写最近列表", not any(e.path == a1 for e in psvc.list_recent()))
la = psvc.load_autosave(proj_a.id)
check("能读回自动保存", la is not None and la.name == "改过名字",
      la.name if la else None)
check("has_autosave 为真", psvc.has_autosave(proj_a))
psvc.clear_autosave(proj_a)
check("clear_autosave 后文件消失", not psvc.has_autosave(proj_a))

print("\n-- 确定性打包")
d1 = Path(D) / "det1.hwpj"
d2 = Path(D) / "det2.hwpj"
det = make_project()
psvc.save(det, d1)
psvc.save(det, d2)
b1 = d1.read_bytes()
b2 = d2.read_bytes()
check("同内容两次保存字节一致（确定性打包）", b1 == b2, (len(b1), len(b2)))

print()
print("=" * 70)
print("七、全链路：模板 + 网格 + 内容 + 填充 + 导出")
print("=" * 70)
lib = make_lib()
doc_e = tps.open_document(pdf1)
w_mm, h_mm = tps.page_size(doc_e, 0)
page = PageGeometry(index=0, width_mm=w_mm, height_mm=h_mm)
grid = GridSpec(start_x_mm=20, start_y_mm=40, col_width_mm=20, row_height_mm=14,
                cell_padding_mm=1.5, rows=8, cols=6)
from handwrite.core.services import grid_service as grs

cells = grs.build_cells(grid, page, 200)
check("网格建成 48 格", len(cells) == 48, len(cells))
check("网格未越界", grs.out_of_bounds(grid, page) == [], grs.out_of_bounds(grid, page))

csp = ContentSpec(mode="random",
                  random=RandomSpec(type="decimal", min=0, max=99.9, decimals=1,
                                    length_min=2, length_max=5))
mat = cts.generate_matrix(csp, grid, seed=2026)
check("内容生成 8x6", (mat.rows, mat.cols) == (8, 6), (mat.rows, mat.cols))

# 背景用模板渲染的真实底图
bg = tps.render_page(doc_e, 0, 200)
res = fls.render_page(bg, cells, mat, lib, JitterSpec(), seed=2026)
check("填充 48 格", res.filled_cells == 48, res.filled_cells)
check("无字符被跳过", res.skipped_chars == {}, res.skipped_chars)
print("     共填充 %d 个字符" % res.total_chars)

out_pngs2 = svc.export_png([(0, res.image)], str(Path(D) / "final_png"),
                           project=Project(id="e", name="全链路", font_id="f"))
check("全链路导出 PNG 成功", len(out_pngs2) == 1 and Path(out_pngs2[0]).stat().st_size > 0,
      os.path.basename(out_pngs2[0]))
check("导出图非全白（确实画了字）",
      len(set(res.image.convert("RGB").getdata())) > 3,
      len(set(res.image.convert("RGB").getdata())))

out_pdf2 = str(Path(D) / "final.pdf")
svc.export_pdf([(0, res.image)], doc_e, out_pdf2, dpi=200)
check("全链路导出 PDF 成功", svc.verify_pdf(out_pdf2, 1), out_pdf2)
with pymupdf.open(out_pdf2) as d:
    check("PDF 用模板尺寸", abs(d[0].rect.width - geo.mm_to_pt(w_mm)) < 1.5,
          (round(d[0].rect.width, 1), round(geo.mm_to_pt(w_mm), 1)))
tps.close_document(doc_e)

print("     产物大小：PNG %.1fKB / PDF %.1fKB"
      % (Path(out_pngs2[0]).stat().st_size / 1024, Path(out_pdf2).stat().st_size / 1024))

print()
print("=" * 70)
if _fails:
    print("失败 %d 项：" % len(_fails))
    for f in _fails:
        print("   - " + f)
    sys.exit(1)
print("全部通过。")
