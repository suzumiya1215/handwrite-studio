"""日志初始化。

设计依据：模块设计说明书 §4.1（logging_setup）

策略：
- 日志写到 ``%LOCALAPPDATA%\\HandWriteStudio\\logs\\app_YYYYMMDD.log``
- 同时输出到控制台（开发时方便）
- 单文件超过 5MB 自动轮转，保留最近 5 个
- 日志里写 UTF-8，中文不乱码
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config as cfg

_LOGGER_NAME = "handwrite"
_initialized = False

#: 日志格式：时间 | 级别 | 模块:行号 | 消息
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def init(level: int = logging.INFO, *, console: bool = True) -> logging.Logger:
    """初始化日志。重复调用是安全的（只生效一次）。"""
    global _initialized
    logger = logging.getLogger(_LOGGER_NAME)
    if _initialized:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # ── 文件处理器 ────────────────────────────────────────
    try:
        log_dir = cfg.logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        fname = log_dir / f"app_{datetime.now():%Y%m%d}.log"
        fh = RotatingFileHandler(
            fname, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        # 日志写不了不能拦着程序启动
        pass

    # ── 控制台处理器 ──────────────────────────────────────
    if console:
        try:
            ch = logging.StreamHandler(sys.stderr)
            ch.setLevel(level)
            ch.setFormatter(fmt)
            logger.addHandler(ch)
        except Exception:
            pass

    _initialized = True
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """取子 logger。名字会自动加上 ``handwrite.`` 前缀。"""
    if not _initialized:
        init()
    if not name:
        return logging.getLogger(_LOGGER_NAME)
    short = name.replace("handwrite.", "").replace("__main__", "main")
    return logging.getLogger(f"{_LOGGER_NAME}.{short}")


def log_startup_banner() -> None:
    """启动时打印环境信息（排查问题时第一眼要看的东西）。"""
    log = get_logger("startup")
    info = cfg.startup_report()
    log.info("=" * 62)
    log.info("%s v%s 启动", info["app"], info["version"])
    log.info("打包运行：%s | Python %s | %s",
             info["frozen"], info["python"], info["platform"])
    log.info("数据目录：%s（存在=%s）", info["data_dir"], info["data_dir_exists"])
    log.info("资源目录：%s（存在=%s）", info["resource_root"], info["resource_exists"])
    log.info("=" * 62)


def tail(lines: int = 80) -> str:
    """读最近的日志（「关于」对话框里可以查看）。"""
    try:
        log_dir = cfg.logs_dir()
        fname = log_dir / f"app_{datetime.now():%Y%m%d}.log"
        if not fname.exists():
            return "（今天还没有日志）"
        content = fname.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except OSError as e:
        return f"（日志读取失败：{e}）"


def log_path_str() -> str:
    try:
        return str(cfg.logs_dir() / f"app_{datetime.now():%Y%m%d}.log")
    except Exception:
        return ""
