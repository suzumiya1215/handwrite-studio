"""手写字迹填充器 · 内核层。

★ 铁律：本包及子包**不得 import PySide6 或任何 Qt 模块**（模块设计说明书 R5 / N1）。
   这条保证内核可以在一台没装 Qt 的机器上跑全部测试。

分层：
    base 层     errors / charset / geometry / config / logging_setup
    models 层   纯 dataclass 数据对象
    services 层 各业务服务（无 Qt）
"""

__version__ = "1.0.0"
