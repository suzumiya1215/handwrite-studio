"""M1 验收脚本：真启动一次应用，截三张图，检查文案。

用法：
    .venv\\Scripts\\python.exe tools\\m1_accept.py

产出：
    build\\screenshots\\m1-01-home.png
    build\\screenshots\\m1-02-wizard.png
    build\\screenshots\\m1-03-studio.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
# ★ 清掉外部注入，避免安全钩子干扰
os.environ.pop("PYTHONPATH", None)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "screenshots"


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))

    from PySide6.QtWidgets import QApplication

    from handwrite.core import config as cfg

    cfg.ensure_dirs()

    from handwrite.main import MainController
    from handwrite.ui.app import apply_app_font, CrashGuard, load_stylesheet

    app = QApplication.instance() or QApplication([])
    font_name = apply_app_font(app)
    load_stylesheet(app)
    guard = CrashGuard(app)
    guard.install()

    print(f"中文字体：{font_name or '（未找到，可能显示方框）'}")

    conf = cfg.AppConfig.load()
    ctl = MainController(app, conf)
    ctl.window.resize(1280, 800)
    ctl.window.show()
    app.processEvents()

    OUT.mkdir(parents=True, exist_ok=True)

    shots = [
        ("m1-01-home.png", lambda: ctl.show_home(), "首页"),
        ("m1-02-wizard.png", ctl.open_wizard, "采集向导"),
        ("m1-03-studio.png", ctl.open_studio, "主工作台"),
    ]

    for filename, action, label in shots:
        action()
        for _ in range(6):                 # 让布局/样式落到画面上
            app.processEvents()
        path = OUT / filename
        ok = ctl.window.grab().save(str(path))
        print(f"  {label:6s} → {path.name}  {'OK' if ok else '保存失败'}")

    guard = None
    print(f"\n截图目录：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
