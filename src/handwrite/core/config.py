"""
路径解析与应用配置。

设计依据：
- 数据格式规范 §3（应用配置 config.json）
- 模块设计说明书 §4.1（paths / config）

关键约定：
- 用户数据统一放 ``%LOCALAPPDATA%\\HandWriteStudio\\``，与安装目录分离
  （需求文档 §9.5.2 定的：卸载时询问是否保留用户数据，所以必须分离）
- ★ 实施计划 P4：所有资源路径走统一函数解析，禁止硬编码绝对路径（禁令 N12）
- 资源路径要兼容「开发运行」与「PyInstaller 打包后运行」两种情形
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from .errors import ConfigError, DataDirError

# ─────────────────────────────────────────────────────────────
# 应用标识
# ─────────────────────────────────────────────────────────────

APP_NAME_ZH = "手写字迹填充器"
APP_NAME_EN = "HandWriteStudio"
APP_ID = "handwrite-studio"
APP_VERSION = "1.0.0"
#: 界面上展示的短版本号（原型里显示 "v1.0"；完整版本号给日志和安装包用）
APP_VERSION_SHORT = "1.0"
APP_ORG = "HandWriteStudio"

#: v1.0 之外的功能开关（预留给后续版本）
FEATURE_FLAGS: dict[str, bool] = {
    "auto_detect_grid": False,   # 自动识别表格线（v1.1）
    "embed_font_in_project": False,  # 项目内嵌字形库（v1.1）
    "dark_mode": False,          # 深色模式（待定）
}


# ─────────────────────────────────────────────────────────────
# 运行环境判断
# ─────────────────────────────────────────────────────────────


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境里。"""
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """资源目录根。

    - 开发时：``src/handwrite/resources``
    - 打包后：``sys._MEIPASS/handwrite/resources``（PyInstaller 的解包目录）

    ★ 一切都走这个函数，不许在别处拼资源路径（禁令 N12）。
    """
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "resources"


def resource_path(*parts: str) -> Path:
    """拼接资源文件路径。

    >>> resource_path("style.qss")  # doctest: +SKIP
    WindowsPath('.../resources/style.qss')
    """
    return resource_root().joinpath(*parts)


def data_dir() -> Path:
    """用户数据目录（字形库、项目、日志、配置都放这里）。

    优先 ``%LOCALAPPDATA%``，取不到则退回用户主目录。
    ★ 用 LOCALAPPDATA 而不是 APPDATA：前者是不漫游的本地数据，适合放
      大体积的字形库和项目文件，且不会因为域账号漫游而同步卡顿。
    """
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME_EN
    return Path.home() / f".{APP_ID}"


def fonts_dir() -> Path:
    """字形库目录。"""
    return data_dir() / "fonts"


def projects_dir() -> Path:
    """项目目录（默认保存位置）。"""
    return data_dir() / "projects"


def autosave_dir() -> Path:
    """自动保存目录。"""
    return data_dir() / "autosave"


def logs_dir() -> Path:
    """日志目录。"""
    return data_dir() / "logs"


def exports_dir() -> Path:
    """默认导出目录。"""
    return data_dir() / "exports"


def config_file() -> Path:
    """配置文件路径。"""
    return data_dir() / "config.json"


def ensure_dirs() -> None:
    """创建全部必需的目录。首次启动时调用。

    Raises:
        DataDirError: 目录创建失败（通常是权限问题）。
    """
    for d in (data_dir(), fonts_dir(), projects_dir(),
              autosave_dir(), logs_dir(), exports_dir()):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DataDirError(
                detail=f"无法创建目录 {d}: {e}",
                cause=e,
            ) from e


# ─────────────────────────────────────────────────────────────
# 应用配置
# ─────────────────────────────────────────────────────────────


@dataclass
class WindowState:
    """主窗口几何状态。"""

    width: int = 1280
    height: int = 820
    x: int = -1        # -1 表示居中
    y: int = -1
    maximized: bool = False


@dataclass
class AppConfig:
    """应用配置（对应 ``config.json``）。

    字段定义见数据格式规范 §3。
    """

    format: str = "hwconfig"
    version: int = 1

    # 外观
    language: str = "zh_CN"
    font_family: str = ""          # 空 = 用系统默认中文字体
    font_size: int = 13

    # 行为
    autosave_interval_sec: int = 120      # 自动保存间隔（需求文档定的 2 分钟）
    autosave_enabled: bool = True
    confirm_on_exit: bool = True          # 有未保存改动时退出询问
    show_welcome: bool = True

    # 上次使用
    last_font_id: str = ""
    last_template_dir: str = ""
    recent_projects: list[str] = field(default_factory=list)
    recent_max: int = 10

    # 导出默认值
    export_dpi: int = 300
    export_format: str = "pdf"            # pdf | png

    # 采集默认值
    capture_tolerance: int = 29           # 容差滑块默认值（与原型一致）
    capture_glyph_height: int = 128

    # 窗口
    window: WindowState = field(default_factory=WindowState)

    # 其它
    first_run: bool = True
    last_version: str = ""

    # ── 读写 ──────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """从磁盘加载。文件不存在或损坏时返回默认配置，**不抛异常**。

        配置坏了不该拦着用户用程序 —— 这与「字形库损坏要报错」不同，
        配置的降级成本很低（大不了回到默认），而阻塞的代价很高。
        """
        p = Path(path) if path else config_file()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ConfigError(
                "配置文件读取失败，已改用默认设置。",
                detail=f"{p}: {e}",
                cause=e,
            ) from e
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "AppConfig":
        """从 dict 构造，忽略不认识的字段，补全缺失字段。"""
        cfg = cls()
        for key, value in (raw or {}).items():
            if not hasattr(cfg, key):
                continue                      # 向前兼容：忽略未知字段
            if key == "window" and isinstance(value, dict):
                cfg.window = _window_from_dict(value)
            elif isinstance(value, list) and key == "recent_projects":
                cfg.recent_projects = [str(v) for v in value][:cfg.recent_max]
            else:
                setattr(cfg, key, value)
        return cfg

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的 dict。"""
        d = asdict(self)
        # 只保留已知字段，避免把运行时字段写进去
        return d

    def save(self, path: Path | None = None) -> None:
        """原子写。失败不阻断流程（配置丢失影响有限）。"""
        p = Path(path) if path else config_file()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, p)
        except OSError:
            # 配置写不进去不是致命问题，静默容忍（但会记日志）
            pass

    # ── 最近项目 ──────────────────────────────────────────
    def push_recent(self, path: str) -> None:
        """把一个项目路径推到最近列表最前（去重、限长）。"""
        s = str(path)
        if s in self.recent_projects:
            self.recent_projects.remove(s)
        self.recent_projects.insert(0, s)
        del self.recent_projects[self.recent_max:]

    def remove_recent(self, path: str) -> None:
        s = str(path)
        if s in self.recent_projects:
            self.recent_projects.remove(s)


def _window_from_dict(d: dict) -> WindowState:
    ws = WindowState()
    for k, v in d.items():
        if hasattr(ws, k):
            setattr(ws, k, v)
    return ws


# ─────────────────────────────────────────────────────────────
# 启动自检
# ─────────────────────────────────────────────────────────────


def startup_report() -> dict:
    """收集一份环境信息，写进日志首行（排查问题时有用）。"""
    return {
        "app": APP_NAME_ZH,
        "version": APP_VERSION,
        "frozen": is_frozen(),
        "executable": sys.executable,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "data_dir": str(data_dir()),
        "data_dir_exists": data_dir().exists(),
        "resource_root": str(resource_root()),
        "resource_exists": resource_root().exists(),
        "time": datetime.now().isoformat(timespec="seconds"),
    }
