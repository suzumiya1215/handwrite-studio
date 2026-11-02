"""可复用控件（画布、参数面板零件、字符键盘）。

★ 这些控件只依赖 PySide6 和 `..core`（纯数据/算法层），
  不 import 任何窗口模块 —— 窗口之间不互相依赖（见 ui/__init__.py 的说明）。
"""

from __future__ import annotations

from .canvas import CanvasLayers, PreviewCanvas, pil_to_pixmap, pil_to_qimage
from .char_keyboard import CHAR_LABEL, CaptureProgress, CharKeyboard
from .param_panel import (
    CollapsibleGroup,
    ColumnChecks,
    FieldRow,
    PresetButtons,
    SegmentedRadios,
    SliderRow,
    TextRow,
    make_button,
    make_hint,
)

__all__ = [
    "PreviewCanvas", "CanvasLayers", "pil_to_pixmap", "pil_to_qimage",
    "CharKeyboard", "CaptureProgress", "CHAR_LABEL",
    "CollapsibleGroup", "FieldRow", "SliderRow", "PresetButtons",
    "ColumnChecks", "SegmentedRadios", "TextRow",
    "make_hint", "make_button",
]
