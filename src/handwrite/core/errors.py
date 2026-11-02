"""
统一异常类型。

设计依据：
- 模块设计说明书 §7 错误传播链路
- 模块设计说明书 §9 禁令 N9（所有自定义异常都带 user_message，且是中文）
- 需求文档 §8 非功能需求（界面不出现英文报错）

铁律：
- 所有异常继承 AppError
- 所有 AppError 必须带 user_message（中文，直接给用户看）
- 服务层不弹窗、不打印，只抛异常；展示是界面的责任
- 可恢复问题走 warnings 列表，不抛异常（见 §7 降级表）
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有自定义异常的基类。

    Attributes:
        user_message: 给用户看的中文说明（会直接显示在错误框里）。
        detail: 给开发看的细节（写进日志，不显示给用户）。
        hint: 可选的下一步建议（如「请清理磁盘后重试」）。
    """

    #: 子类可覆盖的默认用户文案
    default_message: str = "程序遇到一个问题，请重试。如果反复出现，请把日志文件发给开发者。"

    def __init__(
        self,
        user_message: str | None = None,
        *,
        detail: str = "",
        hint: str = "",
        cause: BaseException | None = None,
    ) -> None:
        self.user_message = user_message or self.default_message
        self.detail = detail
        self.hint = hint
        if cause is not None:
            self.__cause__ = cause
        super().__init__(self.user_message)

    def log_repr(self) -> str:
        """给日志用的完整描述。"""
        parts = [f"{type(self).__name__}: {self.user_message}"]
        if self.detail:
            parts.append(f"  detail: {self.detail}")
        if self.hint:
            parts.append(f"  hint: {self.hint}")
        return "\n".join(parts)

    def __str__(self) -> str:  # pragma: no cover - 简单转发
        return self.user_message


# ─────────────────────────────────────────────────────────────
# 字形库相关
# ─────────────────────────────────────────────────────────────


class FontNotFoundError(AppError):
    default_message = "找不到字形库，请重新选择。"


class FontCorruptedError(AppError):
    default_message = "字形库文件已损坏，无法打开。"


class UnknownCharacterError(AppError):
    default_message = "这个字符不在本程序支持的范围内。"

    def __init__(
        self,
        char: str = "",
        *,
        allowed: str = "",
        detail: str = "",
        cause: BaseException | None = None,
    ) -> None:
        msg = f"不支持字符「{char}」。"
        if allowed:
            msg += f" v1 只支持：{allowed}"
        super().__init__(msg, detail=detail or f"char={char!r}", cause=cause)
        self.char = char


# ─────────────────────────────────────────────────────────────
# 模板相关
# ─────────────────────────────────────────────────────────────


class TemplateUnsupportedError(AppError):
    default_message = "不支持这个文件格式，请使用 PDF 或图片（PNG/JPG）。"


class TemplatePasswordError(AppError):
    default_message = "这个 PDF 有密码保护，本程序无法打开。"


class TemplateUnreadableError(AppError):
    default_message = "这个文件读不出来，可能已损坏或路径不正确。"


# ─────────────────────────────────────────────────────────────
# 网格 / 内容
# ─────────────────────────────────────────────────────────────


class GridOutOfBoundsError(AppError):
    default_message = "网格参数超出了页面范围。"


class ContentEmptyError(AppError):
    default_message = "没有任何内容可以填充。"


# ─────────────────────────────────────────────────────────────
# 导出 / 存储
# ─────────────────────────────────────────────────────────────


class ExportFailedError(AppError):
    default_message = "导出失败。"


class ExportCancelledError(AppError):
    """用户主动取消导出。

    ★ 单独成一个类，而不是复用 ExportFailedError：
      界面需要「取消」静默处理（不弹错框），「失败」才弹框。
    """

    default_message = "导出已取消。"


class DiskFullError(AppError):
    default_message = "磁盘空间不足。"
    hint = "请清理磁盘后重试。"


class FileLockedError(AppError):
    default_message = "目标文件正被其他程序占用。"

    hint = "请先关闭打开它的程序（如 WPS、Adobe Reader），再试一次。"


# ─────────────────────────────────────────────────────────────
# 配置 / 环境
# ─────────────────────────────────────────────────────────────


class ConfigError(AppError):
    default_message = "配置文件有问题，将使用默认设置。"


class DataDirError(AppError):
    default_message = "无法创建程序的数据目录。"

    hint = "请检查当前用户是否有写入权限，或联系管理员。"


# ─────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────


#: 所有自定义异常的元组，供 `except AppError` 之外的粗粒度捕获使用
ALL_ERRORS = (
    AppError,
    FontNotFoundError,
    FontCorruptedError,
    UnknownCharacterError,
    TemplateUnsupportedError,
    TemplatePasswordError,
    TemplateUnreadableError,
    GridOutOfBoundsError,
    ContentEmptyError,
    ExportFailedError,
    ExportCancelledError,
    DiskFullError,
    FileLockedError,
    ConfigError,
    DataDirError,
)


def wrap(exc: BaseException, user_message: str, *, hint: str = "") -> AppError:
    """把第三方异常（OSError / fitz 异常等）包成 AppError，保留原始堆栈。

    用于服务层捕获底层库异常后，转成带中文文案的 AppError 再上抛。
    """
    if isinstance(exc, AppError):
        return exc
    return AppError(
        user_message,
        detail=f"{type(exc).__name__}: {exc}",
        hint=hint,
        cause=exc,
    )


def has_chinese(text: str) -> bool:
    """判断字符串里是否含中日韩统一表意文字 —— 用于自检文案。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
