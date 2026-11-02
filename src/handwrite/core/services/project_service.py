"""项目服务 —— `.hwpj` 存取、自动保存、最近项目（L3 服务层）。

设计依据：
- 模块设计说明书 §4.7
- 数据格式规范 §2（.hwpj 完整规范）
- 接口契约与测试用例 §8

四条设计要点：
1. **模板复制进容器**：用户从微信收到的 PDF 常在临时目录，只存路径迟早打不开。
   代价是文件变大（2 页 A4 约 200KB），换来「项目永远打得开」。
2. **确定性打包**：每个 `ZipInfo` 显式设 `date_time`，同内容同字节 —— 便于 diff 和测试。
3. **找不到字形库不算致命**：标记 `unresolved_font = True`，
   **保住用户辛苦标定的网格参数**，让用户重新指一个库就行。
4. **版本过高直接拒**：提示用户升级程序，不做「尽力解析」。
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import charset as cs
from ..errors import AppError, DiskFullError, FileLockedError, FontNotFoundError
from ..models.project import Project, RecentEntry

#: 格式标识与版本（写进 project.json）
FORMAT_NAME = "hwpj"
FORMAT_VERSION = 1
#: 程序能读的最高版本
MAX_READ_VERSION = 1
#: 最近项目上限
MAX_RECENT = 10
#: 自动保存保留份数（每个项目只留一份，避免堆积）
#: 容器内固定成员名
ENTRY_JSON = "project.json"
ENTRY_TEMPLATE = "template/template.pdf"
ENTRY_TEMPLATE_META = "template/meta.json"
ENTRY_THUMB = "thumbnail.png"

#: 时区（本机固定东八区；不依赖系统时区设置，保证写出稳定）
_TZ = timezone(timedelta(hours=8))
#: zip 里用的固定时间戳（1980-01-01，保证确定性打包）
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def now_iso() -> str:
    """当前时间，ISO 8601 带时区偏移。"""
    return datetime.now(_TZ).replace(microsecond=0).isoformat()


class ProjectService:
    """项目文件读写。持有 data_dir（用于 autosave / recent）。"""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from .. import config as cfg

            data_dir = cfg.data_dir()
        self.data_dir = Path(data_dir)
        self.autosave_dir = self.data_dir / "autosave"
        self.recent_file = self.data_dir / "recent.json"

    # ── 保存 ─────────────────────────────────────────────

    def save(
        self,
        project: Project,
        path: str | Path,
        *,
        template_bytes: bytes | None = None,
        thumbnail: bytes | None = None,
    ) -> str:
        """把项目写进 `.hwpj`（ZIP 容器）。

        Args:
            project: 项目对象（会被 touch() 更新时间戳）
            path: 目标文件
            template_bytes: 模板文件内容；给了就内嵌。空则只记路径
            thumbnail: 首页缩略图 PNG 字节（可选）

        Returns:
            实际写出的路径
        """
        target = Path(path)
        if target.suffix.lower() != ".hwpj":
            target = target.with_suffix(".hwpj")

        project.touch()
        if not project.id:
            project.id = _new_id()
        if not project.created_at:
            project.created_at = project.updated_at or now_iso()

        # 模板内嵌：优先用调用方给的字节，其次从 template_path 读
        data = template_bytes
        if data is None and project.template_path:
            src = Path(project.template_path)
            if src.exists() and src.is_file():
                try:
                    data = src.read_bytes()
                except OSError:
                    data = None

        payload = project.to_dict()
        if data is not None:
            payload["template"]["file"] = ENTRY_TEMPLATE
            payload["template"]["embedded_bytes"] = len(data)
        else:
            payload["template"]["file"] = ""

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_zip(target, payload, data, thumbnail)
        except OSError as exc:
            raise _wrap_os(exc, target) from exc

        project.file_path = str(target)
        project.mark_clean()

        # 写成功才入最近列表
        try:
            self.add_recent(target, project)
        except Exception:
            pass          # 最近列表失败不该影响保存成功

        return str(target)

    @staticmethod
    def _write_zip(
        target: Path,
        payload: dict,
        template_bytes: bytes | None,
        thumbnail: bytes | None,
    ) -> None:
        # 先写临时文件再改名：避免写到一半崩了留下半个文件
        tmp = target.with_name(target.name + ".tmp")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                _writestr(z, ENTRY_JSON, _dumps(payload))
                if template_bytes is not None:
                    _writestr(z, ENTRY_TEMPLATE, template_bytes)
                    _writestr(
                        z,
                        ENTRY_TEMPLATE_META,
                        _dumps(
                            {
                                "page_count": payload.get("template", {}).get("page_count", 1),
                                "apply_to_all_pages": payload.get("template", {}).get(
                                    "apply_to_all_pages", True
                                ),
                            }
                        ),
                    )
                if thumbnail:
                    _writestr(z, ENTRY_THUMB, thumbnail)
            os.replace(str(tmp), str(target))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ── 读取 ─────────────────────────────────────────────

    def load(self, path: str | Path) -> Project:
        """读 `.hwpj`。

        容错策略：
        - 不是 zip / 缺 project.json / JSON 坏了 → 抛 AppError（致命）
        - version 高于本程序 → 抛 AppError（提示升级）
        - 字形库找不到 → 不抛，标记 unresolved_font（保住参数）
        - 模板文件缺失 → 不抛，记警告
        """
        p = Path(path)
        if not p.exists():
            raise AppError(f"找不到这个项目文件：{p.name}")
        if not zipfile.is_zipfile(str(p)):
            raise AppError(
                f"这个文件不是有效的项目文件：{p.name}",
                hint="请确认文件没有被改过扩展名或损坏。",
            )

        try:
            with zipfile.ZipFile(p) as z:
                names = set(z.namelist())
                if ENTRY_JSON not in names:
                    raise AppError(
                        f"这个项目文件缺了关键内容（project.json）：{p.name}",
                        hint="文件可能不完整，请重新保存一份。",
                    )
                raw = z.read(ENTRY_JSON)
                project = self._parse(raw, p)
                names_full = names
        except AppError:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise AppError(f"打不开这个项目文件：{p.name}", detail=str(exc)) from exc
        except Exception as exc:
            raise AppError(f"读项目文件出错：{p.name}", detail=str(exc)) from exc

        # 模板：容器里有就解出来给上层用；没有就标记
        if ENTRY_TEMPLATE in names_full:
            project.template_path = str(p) + "#template"
        elif project.template_path:
            project.warnings.append("项目里的模板副本丢了，可重新选一次模板。")

        # 字形库存在性
        if not self._font_exists(project.font_id):
            project.unresolved_font = True
            shown = project.font_name or "某个字形库"
            project.warnings.append(
                f"没找到本项目用的字形库「{shown}」，"
                f"网格和内容都还在，重新选一个字形库就能继续。"
            )
        else:
            project.unresolved_font = False

        project.file_path = str(p)
        project.mark_clean()

        try:
            self.add_recent(p, project)
        except Exception:
            pass

        return project

    def _parse(self, raw: bytes, p: Path) -> Project:
        try:
            d = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(
                f"这个项目文件的内容读不出来：{p.name}",
                hint="文件可能已损坏，请用备份或重新保存一份。",
            ) from exc

        if not isinstance(d, dict):
            raise AppError(f"这个项目文件格式不对：{p.name}")

        fmt = d.get("format")
        if fmt and fmt != FORMAT_NAME:
            raise AppError(f"这不是本程序的项目文件：{p.name}")

        ver = int(d.get("version", 1) or 1)
        if ver > MAX_READ_VERSION:
            raise AppError(
                f"这个项目是更新版本的程序创建的（版本 {ver}），当前程序打不开。",
                hint="请升级到最新版本后再打开。",
            )

        project = Project.from_dict(d)
        project.dirty = False
        return project

    def load_template_bytes(self, path: str | Path) -> bytes | None:
        """取出项目里内嵌的模板字节（没有则 None）。"""
        p = Path(path)
        if not zipfile.is_zipfile(str(p)):
            return None
        try:
            with zipfile.ZipFile(p) as z:
                if ENTRY_TEMPLATE in z.namelist():
                    return z.read(ENTRY_TEMPLATE)
        except Exception:
            return None
        return None

    # ── 字形库存在性 ─────────────────────────────────────

    def _font_exists(self, font_id: str) -> bool:
        """字形库是否还在（查 data_dir/fonts 下的 .hwfont）。

        ★ 抽成方法便于测试注入（契约测试里 monkeypatch 它）。
        """
        if not font_id:
            return False
        d = self.data_dir / "fonts"
        if not d.exists():
            return False
        for f in d.glob("*.hwfont"):
            try:
                with zipfile.ZipFile(f) as z:
                    if "manifest.json" not in z.namelist():
                        continue
                    m = json.loads(z.read("manifest.json").decode("utf-8"))
                    if str(m.get("id", "")) == font_id:
                        return True
            except Exception:
                continue
        return False

    # ── 自动保存 ─────────────────────────────────────────

    def autosave(self, project: Project) -> str:
        """把项目存到自动保存目录。同一项目恒返回同一路径（覆盖写）。

        ★ 不写最近列表 —— 自动保存的碎文件不该污染用户看到的列表。
        """
        try:
            self.autosave_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DiskFullError("自动保存失败：建不了自动保存目录。", detail=str(exc)) from exc

        pid = project.id or "unnamed"
        target = self.autosave_dir / f"{_safe_id(pid)}.hwpj"
        try:
            self._write_zip(target, self._autosave_payload(project), None, None)
        except OSError as exc:
            raise _wrap_os(exc, target) from exc
        return str(target)

    def _autosave_payload(self, project: Project) -> dict:
        project.touch()
        if not project.id:
            project.id = _new_id()
        d = project.to_dict()
        # 自动保存不内嵌模板（省空间，模板在正式文件里有）
        d["template"]["file"] = project.template_path or ""
        d["_autosave"] = True
        return d

    def autosave_path(self, project: Project) -> str:
        pid = project.id or "unnamed"
        return str(self.autosave_dir / f"{_safe_id(pid)}.hwpj")

    def has_autosave(self, project: Project) -> bool:
        return Path(self.autosave_path(project)).exists()

    def clear_autosave(self, project: Project) -> None:
        p = Path(self.autosave_path(project))
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass

    def load_autosave(self, project_id: str) -> Project | None:
        p = self.autosave_dir / f"{_safe_id(project_id)}.hwpj"
        if not p.exists():
            return None
        try:
            return self.load(p)
        except AppError:
            return None

    def cleanup_autosaves(self, keep_days: int = 30) -> int:
        """清掉太旧的自动保存。返回删了几个。"""
        if not self.autosave_dir.exists():
            return 0
        cutoff = datetime.now(_TZ).timestamp() - keep_days * 86400
        n = 0
        for f in self.autosave_dir.glob("*.hwpj"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    n += 1
            except OSError:
                continue
        return n

    # ── 最近项目 ─────────────────────────────────────────

    def list_recent(self, limit: int = MAX_RECENT) -> list[RecentEntry]:
        """读最近项目（新→旧），顺带修正 exists 标记。"""
        raw = self._read_recent_raw()
        out: list[RecentEntry] = []
        for item in raw[: max(1, limit)]:
            path = str(item.get("path", ""))
            if not path:
                continue
            e = RecentEntry(
                path=path,
                name=str(item.get("name", "")),
                font_name=str(item.get("font_name", "")),
                page_count=int(item.get("page_count", 1) or 1),
                updated_at=str(item.get("updated_at", "")),
            )
            e.exists = Path(path).exists()
            out.append(e)
        return out

    def add_recent(self, path: str | Path, project: Project) -> None:
        """把项目插到最近列表最前（去重 + 截断到上限）。"""
        p = str(path)
        raw = [x for x in self._read_recent_raw() if str(x.get("path", "")) != p]
        raw.insert(
            0,
            {
                "path": p,
                "name": project.name or Path(p).stem,
                "font_name": project.font_name,
                "page_count": project.page_count,
                "updated_at": now_iso(),
                "opened_at": now_iso(),
            },
        )
        self._write_recent_raw(raw[:MAX_RECENT])

    def remove_recent(self, path: str | Path) -> None:
        p = str(path)
        raw = [x for x in self._read_recent_raw() if str(x.get("path", "")) != p]
        self._write_recent_raw(raw)

    def clear_recent(self) -> None:
        self._write_recent_raw([])

    def _read_recent_raw(self) -> list[dict]:
        if not self.recent_file.exists():
            return []
        try:
            d = json.loads(self.recent_file.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = d.get("recent_projects") if isinstance(d, dict) else d
        if not isinstance(items, list):
            return []
        return [x for x in items if isinstance(x, dict)]

    def _write_recent_raw(self, items: list[dict]) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.recent_file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"recent_projects": items}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(self.recent_file))
        except OSError:
            pass          # 最近列表写不进去不该让用户看到报错

    # ── 其他 ─────────────────────────────────────────────

    @staticmethod
    def default_name(project: Project) -> str:
        """项目默认名：优先项目名，其次模板文件名，最后「未命名」。"""
        if (project.name or "").strip():
            return project.name.strip()
        if project.template_path:
            return Path(project.template_path).stem
        return "未命名"

    @staticmethod
    def new_project(name: str = "", font_id: str = "", font_name: str = "") -> Project:
        """建一个空白项目（时间戳都填好）。"""
        p = Project(
            id=_new_id(),
            name=name or "未命名",
            font_id=font_id,
            font_name=font_name,
        )
        p.created_at = now_iso()
        p.updated_at = p.created_at
        from .. import config as cfg

        p.app_version = getattr(cfg, "APP_VERSION", "")
        p.ensure_pages()
        return p


# ─────────────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────────────


def _dumps(obj: object) -> bytes:
    """JSON 序列化：UTF-8 无 BOM，中文不转义。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")


def _writestr(z: zipfile.ZipFile, name: str, data: bytes) -> None:
    """写成员，时间戳固定 → 同内容同字节（确定性打包）。"""
    info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    z.writestr(info, data)


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _safe_id(s: str) -> str:
    """把 id 变成安全文件名（虽然 uuid 本来就安全，防手改）。"""
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(s))
    return out[:64] or "unnamed"


def _wrap_os(exc: OSError, target: Path) -> AppError:
    """把 OSError 翻成带中文的 AppError。"""
    eno = getattr(exc, "errno", None)
    if eno == 28:                                     # ENOSPC
        return DiskFullError("磁盘空间不足，存不了项目。", hint="请清理磁盘后重试。")
    if eno in (13, 5, 32):                            # EACCES / 被占用
        return FileLockedError(
            f"写不了这个文件：{target.name}",
            hint="文件可能正被别的程序打开，关掉后再试。",
        )
    return AppError(f"保存项目失败：{target.name}", detail=str(exc))
