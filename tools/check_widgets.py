"""零件自检：不弹窗、不需要人来点，纯构造 + 信号验证。

用法： .venv/Scripts/python.exe tools/check_widgets.py
"""
from __future__ import annotations
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication          # noqa: E402

app = QApplication.instance() or QApplication([])

from handwrite.core.charset import CHARSET_V1        # noqa: E402
from handwrite.ui import widgets as W                # noqa: E402

ok = 0
fail = []


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(f"{name} {extra}")


# ── 画布 ─────────────────────────────────────────────
from PIL import Image                                # noqa: E402

c = W.PreviewCanvas()
check("画布构造", c.zoom_percent() == 100, c.zoom_percent())

img = Image.new("RGB", (400, 600), (255, 255, 255))
c.set_layers(W.CanvasLayers(
    base=img, fill=None,
    grid=[((10, 10), (390, 10), True), ((10, 10), (10, 590), True)],
    cells=[(0, 0, type("R", (), {"left": 10, "top": 10, "right": 100, "bottom": 50})())],
    active_cols=[0, 1],
))
check("set_layers 不炸", True)
check("内容尺寸=底图尺寸", c._content_size() == (400, 600), c._content_size())

c.resize(800, 600)
c.set_zoom(2.0)
check("set_zoom 生效", c.zoom_percent() == 200, c.zoom_percent())
c.set_zoom(999.0)
check("zoom 上限夹住", c.zoom_percent() == c.ZOOM_MAX, c.zoom_percent())
c.set_zoom(0.0001)
check("zoom 下限夹住", c.zoom_percent() == c.ZOOM_MIN, c.zoom_percent())

# 坐标往返
c.set_zoom(1.5)
p = c.content_to_widget(__import__("PySide6.QtCore", fromlist=["QPointF"]).QPointF(100, 200))
back = c.widget_to_content(p)
check("坐标往返一致", abs(back.x() - 100) < 0.01 and abs(back.y() - 200) < 0.01,
      (back.x(), back.y()))

c.set_show_grid(False)
check("显示网格开关", c.show_grid() is False)
c.fit_to_window()
check("fit 后是合理缩放", 0.1 <= c.zoom() <= 4.0, c.zoom())

# PIL → QPixmap 各模式
for mode in ("RGB", "RGBA", "L"):
    im = Image.new(mode, (20, 20))
    pm = W.pil_to_pixmap(im)
    check(f"PIL({mode})→QPixmap", not pm.isNull() and pm.width() == 20, pm.width())

# ── 参数面板零件 ──────────────────────────────────────
g = W.CollapsibleGroup("测试组", expanded=True)
check("分组默认展开", g.is_expanded() is True)
g.set_expanded(False)
check("折叠生效", g.is_expanded() is False and g.arrow.text() == "▶")
g.set_expanded(True)
check("再展开", g.is_expanded() is True and g.arrow.text() == "▼")

f = W.FieldRow("起点 X", 12.5, unit="mm", decimals=1)
got = []
f.changed.connect(got.append)
f.spin.setValue(20.0)
check("FieldRow 发信号", got and abs(got[-1] - 20.0) < 1e-6, got)
f.set_value(5.0)
check("FieldRow set_value 不回环", len(got) == 1, got)
check("FieldRow value 读回", abs(f.value() - 5.0) < 1e-6)

fi = W.FieldRow("行数", 24, integer=True, minimum=1, maximum=200)
check("整数 FieldRow", abs(fi.value() - 24) < 1e-6 and fi.spin.value() == 24)

s = W.SliderRow("左右偏移", minimum=0.0, maximum=0.5, value=0.08, scale=100)
sv = []
s.changed.connect(sv.append)
s.slider.setValue(30)
check("SliderRow 发信号", sv and abs(sv[-1] - 0.30) < 1e-6, sv)
check("SliderRow 读数", abs(s.value() - 0.30) < 1e-6, s.value())
s.set_value(0.12)
check("SliderRow set_value 不回环", len(sv) == 1, sv)
check("SliderRow 百分比标签", s.val_lb.text() == "12%", s.val_lb.text())

# raw 模式：值原样格式化（角度类参数）
s2 = W.SliderRow("整体旋转", minimum=0.0, maximum=15.0, value=3.0,
                 scale=10, display="raw", fmt="{:.0f}", prefix="±", suffix="°")
check("SliderRow raw 标签", s2.val_lb.text() == "±3°", s2.val_lb.text())
check("SliderRow raw 读数", abs(s2.value() - 3.0) < 1e-6, s2.value())

pb = W.PresetButtons([("light", "轻微"), ("natural", "自然"), ("bold", "明显")],
                     current="natural")
picked = []
pb.picked.connect(picked.append)
check("预设当前值", pb.current() == "natural")
pb.set_current("bold", emit=True)
check("预设切换发信号", picked == ["bold"], picked)

cc = W.ColumnChecks()
check("列勾选默认 10 列", cc._cols == 10 and len(cc.active()) == 10, cc._cols)
cc.set_active([0, 2])
check("列勾选 set_active", cc.active() == [0, 2], cc.active())
cc.set_cols(6)
check("set_cols 重建", cc._cols == 6 and len(cc.active()) == 6, (cc._cols, cc.active()))
cc.set_all(False)
check("set_all(False)", cc.active() == [], cc.active())

sr = W.SegmentedRadios([("random", "随机"), ("manual", "手动")], current="random")
check("单选当前值", sr.current() == "random")
sr.set_current("manual")
check("单选设值", sr.current() == "manual")

tr = W.TextRow("随机种子", placeholder="留空 = 每次不同")
tt = []
tr.changed.connect(tt.append)
tr.edit.setText("42")
check("文本行发信号", tt == ["42"], tt)
tr.set_text("7")
check("文本行 set_text 不回环", len(tt) == 1, tt)

check("make_hint", W.make_hint("x").objectName() == "Hint")
check("make_button", W.make_button("x", "Primary").objectName() == "Primary")

# ── 字符键盘 ──────────────────────────────────────────
kb = W.CharKeyboard()
check("键盘 12 键", len(kb._buttons) == 12, len(kb._buttons))
check("键盘覆盖字符集", sorted(kb._buttons) == sorted(CHARSET_V1),
      (sorted(kb._buttons), sorted(CHARSET_V1)))
sel = []
kb.selection_changed.emit.__class__          # 只确认信号存在
c2 = []
kb.char_clicked.connect(c2.append)
kb._on_key("3")
check("点键选题", kb.selected() == "3", kb.selected())
kb._on_key("3")
check("再点同键取消", kb.selected() == "", kb.selected())

kb.set_counts({ch: 3 for ch in CHARSET_V1 if ch not in ",."})
miss = kb.missing()
check("缺字识别", sorted(miss) == [",", "."], miss)
check("缺字按钮亮 miss", kb._buttons[","].property("miss") is True)
check("有字按钮不亮", kb._buttons["3"].property("miss") is False)

cp = W.CaptureProgress()
cp.set_counts({ch: 1 for ch in CHARSET_V1})
check("进度全齐 12/12", cp.text_lb.text() == "已采集 12 / 12", cp.text_lb.text())
check("进度条满", cp.bar.value() == 12, cp.bar.value())
check("全齐显示 HintOk", cp.miss_lb.objectName() == "HintOk", cp.miss_lb.objectName())

cp.set_counts({ch: 1 for ch in CHARSET_V1 if ch not in ",."})
check("缺字回到 HintWarn", cp.miss_lb.objectName() == "HintWarn", cp.miss_lb.objectName())
check("缺字文案含标点名", "逗号" in cp.miss_lb.text(), cp.miss_lb.text())

print(f"\n通过 {ok} 项")
if fail:
    print(f"失败 {len(fail)} 项：")
    for x in fail:
        print("  ✗", x)
    raise SystemExit(1)
print("零件自检全部通过")
