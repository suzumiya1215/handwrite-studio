"""服务层（L3）。

★ 架构铁律 R5 / 禁令 N1：
   本包下的任何文件都**不得 import PySide6**。
   服务层要能在一台没装 Qt 的机器上跑完全部测试。

分层方向（模块设计说明书 §2 依赖规则）：
    services → models → base   （只能向下，不许向上、不许横向乱调）

装配层 app.py / main.py 负责把服务实例化并交给界面，
服务之间**不做互相查找**（FillService 是纯函数，不持有别的服务）。
"""

from __future__ import annotations

__all__ = [
    "font_service",
    "template_service",
    "grid_service",
    "content_service",
    "fill_service",
    "project_service",
    "export_service",
]
