"""界面层（L4）。

★ 架构铁律 R5 / 禁令 N1：
   只有本包下的文件可以 `import PySide6`。
   core/ 里任何文件都不许碰 Qt —— 由 tests/test_layering.py 自动看守。

本包内的窗口之间**不互相 import**：谁要跳转就发信号，
由 app 层的 MainController 统一调度。
"""

from __future__ import annotations

__all__ = ["app", "home", "wizard", "studio", "dialogs"]
