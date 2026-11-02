"""端到端集成测试：模板 → 网格 → 字形库 → 填充 → 导出 → 项目存档。

不依赖 Qt（直接驱动 DocumentSession + 服务层），CI/远程也能跑。
同时把渲染结果落盘到 tmp，便于人工眼检「是不是真手写字」。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw

from handwrite.core.services.session_service import DocumentSession
from tests.fixtures.fake_font import make_fake_font, write_font_to_dir


def _make_blank_form(path: str, w_mm=210, h_mm=297, dpi=200) -> None:
    """画一张「白底 + 浅灰表格线」的 A4 图，模拟待填的台账表。"""
    w = int(w_mm / 25.4 * dpi)
    h = int(h_mm / 25.4 * dpi)
    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    # 标题
    d.rectangle([int(w * 0.07), int(h * 0.05), int(w * 0.93), int(h * 0.10)],
                outline=(150, 150, 150))
    # 外框
    d.rectangle([int(w * 0.05), int(h * 0.04), int(w * 0.95), int(h * 0.96)],
                outline=(120, 120, 120), width=3)
    # 列分割线（10 列）
    for c in range(1, 10):
        x = int(w * 0.05 + (w * 0.90) * c / 10)
        d.line([(x, int(h * 0.13)), (x, int(h * 0.95))], fill=(200, 200, 200), width=2)
    # 行分割线（25 行）
    for r in range(1, 25):
        y = int(h * 0.13 + (h * 0.82) * r / 25)
        d.line([(int(w * 0.05), y), (int(w * 0.95), y)], fill=(200, 200, 200), width=1)
    img.convert("RGB").save(path, "PNG")


def _dark_pixel_count(img: Image.Image) -> int:
    """统计近乎黑色的像素数（手写字笔画）。"""
    if img.mode != "L":
        img = img.convert("L")
    px = img.load()
    n = 0
    for y in range(0, img.height, 4):
        for x in range(0, img.width, 4):
            if px[x, y] < 60:
                n += 1
    return n


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """隔离数据目录，并把测试字形库存到 fonts/ 下（项目重载时能找到）。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    from handwrite.core import config as cfg
    from handwrite.core.services import font_service as fts

    cfg.ensure_dirs()
    form = tmp_path / "台账.png"
    _make_blank_form(str(form))

    lib = make_fake_font(samples_per_char=3, height=128, name="测试字迹",
                         lib_id="test-font")
    hwfont = cfg.fonts_dir() / "test-font.hwfont"
    fts.save(lib, str(hwfont), app_version=cfg.APP_VERSION)
    return {"form": str(form), "font": str(hwfont), "root": tmp_path}


def test_full_pipeline_renders_handwriting(workspace):
    s = DocumentSession()
    s.open_template(workspace["form"])
    s.load_font(workspace["font"])

    # 20 行 × 8 列，随机填数字
    s.apply_grid(rows=20, cols=8,
                 start_x_mm=10.5, start_y_mm=38.0,
                 col_width_mm=21.0, row_height_mm=9.6,
                 cell_padding_mm=1.5)
    s.apply_content(mode="random", min=0, max=999999,
                    length_min=3, length_max=8, decimals=0)
    s.apply_jitter({"preset": "natural"})
    s.apply_seed(20261009)

    base, fill, result = s.fill_page(0)
    assert fill is not None, "填充层不该为空"
    assert result is not None
    assert getattr(result, "filled_cells", 0) > 0, "应该有被填的格子"

    ink = _dark_pixel_count(fill)
    assert ink > 200, f"填充结果里几乎没黑像素（{ink}），不像写了字"

    # 落盘供人工眼检
    out_png = str(workspace["root"] / "preview.png")
    fill.convert("RGB").save(out_png, "PNG")
    print(f"\n[E2E] 预览已落盘：{out_png}（黑像素约 {ink}）")


def test_export_pdf_and_png(workspace):
    s = DocumentSession()
    s.open_template(workspace["form"])
    s.load_font(workspace["font"])
    s.apply_grid(rows=15, cols=6)
    s.apply_content(mode="random", min=100, max=9999, length_min=4, length_max=4)
    s.apply_seed(7)

    out_dir = workspace["root"] / "exports"
    out_dir.mkdir()
    pdfs = s.export(kind="pdf", out=str(out_dir / "台账_手写版.pdf"), dpi=300)
    assert pdfs and os.path.exists(pdfs[0]), "PDF 应该生成"
    assert os.path.getsize(pdfs[0]) > 4096, "PDF 太小，可能没内容"

    pngs = s.export(kind="png", out=str(out_dir), dpi=200, pages=1)
    assert pngs and os.path.exists(pngs[0]), "PNG 应该生成"
    print(f"\n[E2E] PDF: {pdfs[0]}\n[E2E] PNG: {pngs[0]}")


def test_project_save_load_roundtrip(workspace):
    s = DocumentSession()
    s.open_template(workspace["form"])
    s.load_font(workspace["font"])
    s.apply_grid(rows=12, cols=5)
    s.apply_content(mode="random", min=0, max=99, length_min=1, length_max=2)

    proj_path = workspace["root"] / "demo.hwpj"
    saved = s.save_project(str(proj_path))
    assert os.path.exists(saved)

    s2 = DocumentSession()
    s2.load_project(saved)
    assert s2.has_template, "重新载入后模板应还在"
    assert s2.lib is not None, "字形库应自动重新载入"
    assert s2.project is not None
    # 网格参数应能还原
    g = s2.grid_spec(0)
    assert g.rows == 12 and g.cols == 5
    # 能继续渲染
    base, fill, _ = s2.fill_page(0)
    assert fill is not None
