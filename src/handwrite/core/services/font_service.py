"""字形库服务 —— .hwfont 的读写、校验、管理（L3 服务层）。

设计依据：
- 数据格式规范 §1（.hwfont 格式）
- 模块设计说明书 §4.3
- 接口契约与测试用例 §4

★ 不 import Qt（架构禁令 N1）。

容器结构（.hwfont 就是个 zip）：:

    manifest.json         数据格式规范 §1.2
    glyphs/
        0/001.png
        0/002.png
        ...
        comma/001.png
        dot/001.png

目录名是 ASCII 的（``comma`` / ``dot``），字符本身存在 manifest 里 ——
这样在任何语言、任何文件系统下都不会有编码问题。
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from .. import charset as cs
from .. import config as cfg
from .. import imageops as iops
from ..errors import FontCorruptedError, FontNotFoundError
from ..models.glyph import FontLibrary, FontSummary, GlyphSample, GlyphSet

#: manifest 里的格式标记
FORMAT_NAME = "hwfont"
FORMAT_VERSION = 1

#: 一个字形库里允许的最多样本总数（防有人塞进来一万张图）
MAX_SAMPLES = 5000


# ─────────────────────────────────────────────────────────────
# 路径工具
# ─────────────────────────────────────────────────────────────


def _glyph_path(dirname: str, filename: str) -> str:
    """容器内的字形图路径（恒用正斜杠，zip 规范）。"""
    return f"glyphs/{dirname}/{filename}"


def _safe_id(name: str) -> str:
    """把任意名字变成可用作文件名的 id。"""
    keep = []
    for ch in name.strip():
        if ch.isalnum() or ch in "-_":
            keep.append(ch)
        elif ch.isspace():
            keep.append("-")
    s = "".join(keep).strip("-_").lower()
    return s or "font"


def new_library_id(name: str = "") -> str:
    """生成一个不重复的库 id。"""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    base = _safe_id(name) if name else "font"
    return f"{base}-{stamp}"


# ─────────────────────────────────────────────────────────────
# 保存
# ─────────────────────────────────────────────────────────────


def save(
    lib: FontLibrary,
    path: str | Path,
    *,
    app_version: str = "",
    compress: bool = True,
) -> Path:
    """把字形库写成 .hwfont（zip）。

    ★ 图像从 GlyphSample.image 取；没有缓存图像时会去 lib.root 下读盘。
      （「打开→改→另存」这条路必须能走通，所以两种来源都要支持。）

    Returns:
        实际写入的路径。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lib.touch()
    manifest = lib.to_manifest(app_version=app_version)

    # 列出所有要写的成员
    members: list[tuple[str, bytes]] = []
    total = 0

    for ch, gs in lib.characters.items():
        dirname = gs.dirname()
        for sample in gs.samples:
            img = sample.image
            if img is None:
                img = _read_cached_sample(lib, dirname, sample.file)
            if img is None:
                # 图丢了：跳过，但记在 manifest 的 note 里
                continue

            buf = io.BytesIO()
            img.convert("RGBA").save(buf, format="PNG", optimize=True)
            members.append((_glyph_path(dirname, sample.file), buf.getvalue()))
            total += 1
            if total > MAX_SAMPLES:
                raise FontCorruptedError(
                    f"字形库里的图太多了（超过 {MAX_SAMPLES} 张），可能有异常。"
                )

    manifest["total_samples"] = total

    try:
        with zipfile.ZipFile(
            path, "w",
            compression=zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED,
        ) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for name, data in members:
                zf.writestr(name, data)
    except OSError as e:
        from ..errors import DiskFullError, FileLockedError
        raise _wrap_os_error(e, path) from e

    lib.root = str(path)
    return path


def _read_cached_sample(lib: FontLibrary, dirname: str, filename: str):
    """从 lib.root 读一张样本图（另存场景）。读不到返回 None。"""
    if not lib.root:
        return None
    root = Path(lib.root)
    if not root.exists():
        return None
    try:
        if root.is_dir():
            p = root / "glyphs" / dirname / filename
            if p.exists():
                return iops.load_image(str(p)).convert("RGBA")
        elif zipfile.is_zipfile(root):
            with zipfile.ZipFile(root) as zf:
                with zf.open(_glyph_path(dirname, filename)) as f:
                    from PIL import Image
                    return Image.open(io.BytesIO(f.read())).convert("RGBA")
    except Exception:
        return None
    return None


def _wrap_os_error(e: OSError, path: Path):
    """把系统错误翻成中文友好的自家异常。"""
    from ..errors import DiskFullError, FileLockedError
    errno = getattr(e, "errno", None)
    if errno == 28:
        return DiskFullError(f"磁盘空间不足，写不了文件：{path}")
    if errno in (13, 32):
        return FileLockedError(
            f"文件被占用了：{path}",
            hint="它可能正在被另一个程序打开，关掉再试。",
        )
    return FontCorruptedError(f"保存字形库失败：{e}")


# ─────────────────────────────────────────────────────────────
# 载入
# ─────────────────────────────────────────────────────────────


def load(path: str | Path, *, deep: bool = False) -> FontLibrary:
    """读一个 .hwfont。

    Args:
        deep: True 时把每张字形图也读进内存（渲染时用）；
              False 只读 manifest，够列表页和覆盖率显示用（快得多）。

    Raises:
        FontNotFoundError: 文件不存在
        FontCorruptedError: 不是合法的 .hwfont
    """
    path = Path(path)
    if not path.exists():
        raise FontNotFoundError(
            f"找不到字形库文件：{path.name}",
            hint="它可能被移动、改名或删除了。",
        )
    if not zipfile.is_zipfile(path):
        raise FontCorruptedError(
            f"「{path.name}」不是有效的字形库文件。",
            hint="字形库应该是 .hwfont 文件。",
        )

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names:
                raise FontCorruptedError(
                    f"「{path.name}」缺少必要的信息文件，可能已损坏。",
                )
            raw = zf.read("manifest.json")
            manifest = json.loads(raw.decode("utf-8"))
    except FontCorruptedError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise FontCorruptedError(
            f"「{path.name}」的信息文件读不出来，可能已损坏。",
        ) from e
    except OSError as e:
        raise FontCorruptedError(f"打开字形库失败：{e}") from e

    if manifest.get("format") != FORMAT_NAME:
        raise FontCorruptedError(
            f"「{path.name}」不是本程序的字形库。",
            hint=f"文件类型是「{manifest.get('format', '未知')}」。",
        )

    lib = FontLibrary.from_manifest(manifest, root=str(path))
    lib.warnings = []

    # manifest 与实际内容的对账（不报错，只记警告 —— 降级而非中断）
    if not deep:
        _verify_light(lib, path, names)
    else:
        _load_images(lib, path)

    return lib


def _verify_light(lib: FontLibrary, path: Path, names: set[str]) -> None:
    """轻校验：manifest 里列的文件是否真的在包里。"""
    missing = 0
    for ch, gs in lib.characters.items():
        dirname = gs.dirname()
        for s in gs.samples:
            if _glyph_path(dirname, s.file) not in names:
                missing += 1
    if missing:
        msg = f"字形库里有 {missing} 张图找不到了，用到的位置会留空。"
        lib.warnings.append(msg)


def _load_images(lib: FontLibrary, path: Path) -> None:
    """把每张字形图读进 GlyphSample.image。"""
    from PIL import Image

    try:
        with zipfile.ZipFile(path) as zf:
            for ch, gs in lib.characters.items():
                dirname = gs.dirname()
                keep: list[GlyphSample] = []
                for s in gs.samples:
                    member = _glyph_path(dirname, s.file)
                    try:
                        with zf.open(member) as f:
                            img = Image.open(io.BytesIO(f.read())).convert("RGBA")
                        s.image = img
                        keep.append(s)
                    except (KeyError, OSError):
                        continue
                gs.samples = keep
    except OSError as e:
        raise FontCorruptedError(f"读取字形图失败：{e}") from e


# ─────────────────────────────────────────────────────────────
# 目录形式的字形库（开发期 / 未打包形态）
# ─────────────────────────────────────────────────────────────


def save_to_dir(lib: FontLibrary, root: str | Path) -> Path:
    """把字形库存成一个**目录**（glyphs/ + manifest.json）。

    为什么需要：采集过程中频繁保存，每次压一个 zip 太慢；
    而且调试时能直接看文件夹里的图。
    """
    root = Path(root)
    (root / "glyphs").mkdir(parents=True, exist_ok=True)

    lib.touch()
    manifest = lib.to_manifest()
    total = 0
    for ch, gs in lib.characters.items():
        dirname = gs.dirname()
        d = root / "glyphs" / dirname
        d.mkdir(parents=True, exist_ok=True)
        for s in gs.samples:
            if s.image is None:
                continue
            s.image.convert("RGBA").save(d / s.file, format="PNG", optimize=True)
            total += 1
    manifest["total_samples"] = total
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lib.root = str(root)
    return root


def load_from_dir(root: str | Path, *, deep: bool = False) -> FontLibrary:
    """从目录形式载入。"""
    root = Path(root)
    mf = root / "manifest.json"
    if not mf.exists():
        raise FontNotFoundError(f"这个文件夹里没有字形库：{root}")

    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise FontCorruptedError(f"字形库信息文件损坏：{mf.name}") from e

    lib = FontLibrary.from_manifest(manifest, root=str(root))
    lib.warnings = []
    if deep:
        from PIL import Image
        for ch, gs in lib.characters.items():
            dirname = gs.dirname()
            keep = []
            for s in gs.samples:
                p = root / "glyphs" / dirname / s.file
                if p.exists():
                    try:
                        s.image = Image.open(p).convert("RGBA")
                        keep.append(s)
                    except OSError:
                        continue
            gs.samples = keep
    return lib


def load_any(path: str | Path, *, deep: bool = False) -> FontLibrary:
    """目录或 .hwfont 都能载入。"""
    p = Path(path)
    if p.is_dir():
        return load_from_dir(p, deep=deep)
    return load(p, deep=deep)


# ─────────────────────────────────────────────────────────────
# 库管理（用户数据目录下的一套库）
# ─────────────────────────────────────────────────────────────


def list_libraries() -> list[FontSummary]:
    """列出用户已保存的全部字形库（只看 manifest，很快）。"""
    out: list[FontSummary] = []
    d = cfg.fonts_dir()
    if not d.exists():
        return out

    for p in sorted(d.iterdir()):
        if p.suffix.lower() != ".hwfont" or not p.is_file():
            continue
        try:
            lib = load(p, deep=False)
        except Exception:
            continue
        cov = lib.coverage()
        out.append(FontSummary(
            id=lib.id,
            name=lib.name or p.stem,
            path=str(p),
            char_count=len(cov.present),
            sample_count=lib.total_samples(),
            coverage=cov.display,
            created_at=lib.created_at,
            updated_at=lib.updated_at,
        ))

    out.sort(key=lambda s: s.updated_at or "", reverse=True)
    return out


def find_by_id(lib_id: str) -> Path | None:
    """按 id 找库文件。"""
    for p in cfg.fonts_dir().glob("*.hwfont") if cfg.fonts_dir().exists() else []:
        try:
            lib = load(p, deep=False)
            if lib.id == lib_id:
                return p
        except Exception:
            continue
    return None


def delete_library(path: str | Path) -> bool:
    """删除一个库文件。★ 走回收站而不是直接删（数据安全）。"""
    p = Path(path)
    if not p.exists():
        return False
    try:
        from send2trash import send2trash          # 可选依赖
        send2trash(str(p))
        return True
    except Exception:
        pass
    # 没有 send2trash 就改名藏起来，不真删
    try:
        trash = p.with_suffix(p.suffix + f".deleted-{datetime.now():%Y%m%d%H%M%S}")
        p.rename(trash)
        return True
    except OSError:
        return False


def rename_library(path: str | Path, new_name: str) -> FontLibrary:
    """改库的显示名（文件名不动，避免引用失效）。"""
    lib = load(path, deep=False)
    lib.name = new_name.strip() or lib.name
    lib.touch()
    save(lib, path)
    return load(path, deep=False)


# ─────────────────────────────────────────────────────────────
# 采集过程中的增量构建
# ─────────────────────────────────────────────────────────────


def make_sample(img_rgba, *, target_height: int = 128, ch: str = ""):
    """把一张切好的字形图规范化成一个样本。

    Args:
        ch: 字符。★ 传了它才能正确处理标点 ——
            句号/逗号不会被拉满高度（否则会变成一个大黑点）。
            不传就按普通字符处理。

    Returns:
        (GlyphSample, 规范化后的 RGBA 图)
    """
    if ch:
        norm = iops.normalize_for_char(img_rgba, ch, target_height=target_height)
    else:
        norm = iops.normalize_glyph(img_rgba, target_height=target_height)
    w, h = norm.size
    return GlyphSample(file="", width=w, height=h, image=norm), norm


def add_samples(lib: FontLibrary, ch: str, images: list) -> FontLibrary:
    """往库里加若干样本（返回**新**库，不可变更新）。

    ★ 文件名在这里统一分配，保证同一字符下不重名。
    """
    if not cs.is_supported(ch):
        from ..errors import UnknownCharacterError
        raise UnknownCharacterError(
            f"不支持字符「{ch}」。",
            hint="当前版本只能采集：" + cs.CHARSET_HUMAN,
        )

    new = lib.copy()
    gs = new.characters.get(ch)
    if gs is None:
        gs = GlyphSet(char=ch)
        new.characters[ch] = gs

    for img in images:
        sample, norm = make_sample(img, target_height=new.glyph_height, ch=ch)
        sample.file = gs.next_filename()
        sample.width, sample.height = norm.size
        sample.image = norm
        gs.add(sample)

    new.touch()
    return new


def coverage_text(lib: FontLibrary) -> str:
    """覆盖率的显示文案，如 ``12/12``。"""
    return lib.coverage().display


def is_ready(lib: FontLibrary | None) -> bool:
    """这个库能不能用来填表（至少要有全部 12 个字符）。"""
    if lib is None:
        return False
    return lib.coverage().is_complete


def missing_text(lib: FontLibrary) -> str:
    """缺了哪些字符的中文描述。"""
    cov = lib.coverage()
    if not cov.missing:
        return ""
    return "还缺：" + " ".join(cov.missing)


def purge_unsupported(lib: FontLibrary) -> FontLibrary:
    """清掉库里不属于 v1 字符集的字符（导入旧库时用）。"""
    new = lib.copy()
    new.characters = {
        ch: gs for ch, gs in new.characters.items() if cs.is_supported(ch)
    }
    return new
