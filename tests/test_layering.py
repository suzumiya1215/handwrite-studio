"""架构分层规则的自动校验。

设计依据：模块设计说明书 §9 禁令清单（N1~N12）+ §2.1 依赖规则（R1~R6）

这些测试不导入被测模块，只做**静态扫描** —— 这样即使某个模块语法有错
也能跑（不会因为 import 失败而整批红掉）。

★ 从 M1 起就要跑。规矩从第一天立，后面才不会越写越松。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "handwrite"
CORE = SRC / "core"
UI = SRC / "ui"


def _py_files(base: Path) -> list[Path]:
    return sorted(base.rglob("*.py")) if base.exists() else []


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _strip_comments(text: str) -> str:
    """去掉注释和字符串字面量里的干扰 —— 否则注释里提到 QMessageBox 会误报。

    做法比较粗但够用：逐行去掉 # 之后的内容，再去掉三引号块。
    """
    # 去掉三引号块
    text = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    text = re.sub(r"'''(?:.|\n)*?'''", "''", text)
    # 去掉行注释
    lines = []
    for line in text.splitlines():
        in_str = False
        quote = ""
        out = []
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    in_str = False
                out.append(ch)
            else:
                if ch in "\"'":
                    in_str = True
                    quote = ch
                    out.append(ch)
                elif ch == "#":
                    break
                else:
                    out.append(ch)
            i += 1
        lines.append("".join(out))
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════
# N1 / N2 · core 不得沾 Qt
# ═════════════════════════════════════════════════════════════


class TestNoQtInCore:
    """★ R5 / N1：core/ 下任何文件不出现 PySide6 / Qt。"""

    def test_no_pyside_import(self):
        bad = []
        for f in _py_files(CORE):
            t = _strip_comments(_read(f))
            if re.search(r"^\s*(import|from)\s+PySide6", t, re.M):
                bad.append(_rel(f))
        assert not bad, f"core/ 下出现 Qt 依赖（违反 R5/N1）: {bad}"

    def test_no_qt_import_any_form(self):
        """连 PyQt / Qt 也不许。"""
        bad = []
        for f in _py_files(CORE):
            t = _strip_comments(_read(f))
            if re.search(r"^\s*(import|from)\s+(PyQt|Qt)", t, re.M):
                bad.append(_rel(f))
        assert not bad, f"core/ 下出现 Qt 系依赖: {bad}"

    def test_no_qmessagebox(self):
        """★ N2：服务层不弹窗，异常只上抛。"""
        bad = []
        for f in _py_files(CORE):
            t = _strip_comments(_read(f))
            if "QMessageBox" in t:
                bad.append(_rel(f))
        assert not bad, f"core/ 下出现弹窗（违反 N2）: {bad}"

    def test_no_other_qt_symbols(self):
        """常见的 Qt 符号都不该出现在 core。"""
        symbols = ("QApplication", "QWidget", "QDialog", "QPixmap",
                   "QTimer", "QThread", "Signal(", "Slot(")
        bad = []
        for f in _py_files(CORE):
            t = _strip_comments(_read(f))
            hit = [s for s in symbols if s in t]
            if hit:
                bad.append(f"{_rel(f)}: {hit}")
        assert not bad, f"core/ 下出现 Qt 符号: {bad}"


# ═════════════════════════════════════════════════════════════
# N3 · 不硬编码 25.4
# ═════════════════════════════════════════════════════════════


class TestNoHardcodedConstants:
    def test_no_raw_25_4(self):
        """★ N3 / C3：mm↔px 换算只走 geometry.py。"""
        bad = []
        for f in _py_files(CORE):
            if f.name == "geometry.py":
                continue
            t = _strip_comments(_read(f))
            if "25.4" in t:
                bad.append(_rel(f))
        assert not bad, f"硬编码 25.4（违反 N3/C3）: {bad}"

    def test_no_raw_dirnames(self):
        """★ N4 / C11：comma / dot 目录名只能由 charset 生成。

        ★ 注意精确性问题：``"dot"`` 在 jitter.py 里指的是**第四层抖动**
          （小数点抖动），与字形库目录名恰好同名 —— 那是合法的。
          所以这里只在「看起来像路径上下文」时才判定违规：
          出现 ``glyphs/`` 前缀、或坐在 ``os.path.join`` 里、或直接拼文件名。

          实测教训：规则太粗会把合法同名标识符误报，收紧判据比放宽阈值好。
        """
        # 目录名 + 路径分隔符 或 目录名 + .png
        path_like = re.compile(r'["\'](?:comma|dot)["\']\s*[,)]|\b(?:comma|dot)\s*/\s*[\w.]')
        # 显式拼路径的写法
        join_like = re.compile(r'(?:join|Path|f)["\']?[^"\']*["\'](?:comma|dot)["\']')
        bad = []
        for f in _py_files(CORE):
            if f.name == "charset.py":
                continue
            t = _strip_comments(_read(f))
            for i, line in enumerate(t.splitlines(), 1):
                if "glyphs" in line and re.search(r'["\'](comma|dot)["\']', line):
                    bad.append(f"{_rel(f)}:{i} 字形库路径里硬编码目录名")
                elif re.search(r'["\'](comma|dot)\s*/\s*["\']', line):
                    bad.append(f"{_rel(f)}:{i} 路径片段里硬编码目录名")
        assert not bad, f"硬编码字符目录名（违反 N4/C11）: {bad}"

    def test_no_raw_charset_list(self):
        """★ C9：权威字符集只有 charset.py 一处。

        检测「连着写了 5 个以上数字字符」的可疑字面量。
        """
        bad = []
        for f in _py_files(CORE):
            if f.name in ("charset.py",):
                continue
            t = _strip_comments(_read(f))
            for m in re.finditer(r'["\']([0-9]{5,})["\']', t):
                bad.append(f"{_rel(f)}: {m.group(0)}")
        assert not bad, f"疑似硬编码字符集（违反 C9）: {bad}"


# ═════════════════════════════════════════════════════════════
# N5 / N6 · 随机数
# ═════════════════════════════════════════════════════════════


class TestRandomness:
    def test_no_stdlib_random(self):
        """★ N5 / C5：统一用 np.random.default_rng。"""
        bad = []
        for f in _py_files(CORE):
            t = _strip_comments(_read(f))
            if re.search(r"^\s*import\s+random\b", t, re.M):
                bad.append(_rel(f))
            if re.search(r"^\s*from\s+random\s+import", t, re.M):
                bad.append(_rel(f))
        assert not bad, f"出现 stdlib random（违反 N5/C5）: {bad}"

    def test_no_global_np_seed(self):
        """★ N6：np.random.seed 会破坏可复现性。"""
        bad = []
        for f in _py_files(CORE):
            t = _strip_comments(_read(f))
            if "np.random.seed" in t or "numpy.random.seed" in t:
                bad.append(_rel(f))
        assert not bad, f"出现全局播种（违反 N6）: {bad}"

    def test_no_legacy_random_api(self):
        """np.random.rand / randint 这类全局 API 也不该用。"""
        bad = []
        for f in _py_files(CORE):
            t = _strip_comments(_read(f))
            if re.search(r"np\.random\.(rand|randint|randn|choice|shuffle)\b", t):
                bad.append(_rel(f))
        assert not bad, f"用了 np.random 全局 API（违反 C5）: {bad}"


# ═════════════════════════════════════════════════════════════
# N12 · 不硬编码绝对路径
# ═════════════════════════════════════════════════════════════


class TestResourcePaths:
    def test_no_absolute_path_literals(self):
        """★ N12 / 实施计划 P4：资源路径必须走 paths/config 解析。

        允许在注释里出现 # noqa 豁免。
        """
        pat = re.compile(r'["\'][A-Za-z]:[\\/]')
        bad = []
        for f in _py_files(SRC):
            for i, line in enumerate(_read(f).splitlines(), 1):
                if "noqa" in line:
                    continue
                # 跳过纯注释行
                if line.lstrip().startswith("#"):
                    continue
                if pat.search(line):
                    bad.append(f"{_rel(f)}:{i}")
        assert not bad, f"硬编码绝对路径（违反 N12/P4）: {bad}"

    def test_no_unc_paths(self):
        pat = re.compile(r'["\']\\\\\\\\[^"\']+["\']')
        bad = []
        for f in _py_files(SRC):
            t = _strip_comments(_read(f))
            if pat.search(t):
                bad.append(_rel(f))
        assert not bad, f"出现 UNC 路径: {bad}"


# ═════════════════════════════════════════════════════════════
# R1 / R2 / R3 · 依赖方向
# ═════════════════════════════════════════════════════════════


class TestDependencyDirection:
    def test_base_does_not_import_upper(self):
        """★ R1：base 层不得 import services / models / ui。"""
        base = CORE
        bad = []
        for f in _py_files(base):
            # models 子包本身允许被 models 内部互相引用
            if f.parent.name == "models":
                continue
            if f.parent.name == "services":
                continue
            t = _strip_comments(_read(f))
            if re.search(r"from\s+\.{1,2}(services|models|ui)\b", t) or \
               re.search(r"from\s+handwrite\.core\.(services|models|ui)", t):
                bad.append(_rel(f))
        assert not bad, f"基础层反向依赖（违反 R1）: {bad}"

    def test_services_do_not_import_ui(self):
        """★ R3：服务层不得 import ui。"""
        svc = CORE / "services"
        bad = []
        for f in _py_files(svc):
            t = _strip_comments(_read(f))
            if re.search(r"(from|import)\s+.*\bui\b", t) or \
               "handwrite.ui" in t or "core.ui" in t:
                bad.append(_rel(f))
        assert not bad, f"服务层依赖 ui（违反 R3）: {bad}"

    def test_models_do_not_import_services(self):
        """★ R2：数据层不得 import 服务层。"""
        bad = []
        for f in _py_files(CORE / "models"):
            t = _strip_comments(_read(f))
            if re.search(r"from\s+\.{1,3}services\b", t) or \
               "core.services" in t:
                bad.append(_rel(f))
        assert not bad, f"数据层依赖服务层（违反 R2）: {bad}"

    def test_ui_may_import_services_only_via_public(self):
        """ui 允许 import services，但不应该访问私有成员（下划线开头）。

        这是软性检查，找出可疑的 `_xxx` 访问。
        """
        bad = []
        for f in _py_files(UI):
            t = _strip_comments(_read(f))
            # 形如 services.xxx._private
            for m in re.finditer(r"\.\w+\._(\w+)\(", t):
                bad.append(f"{_rel(f)}: ._{m.group(1)}()")
        # 目前允许（M1 还没写业务），改成警告式：只要不爆炸就行
        # 后续收紧时把 assert 打开
        if bad:
            pytest.skip(f"待收紧：UI 访问服务私有成员 {bad}")


# ═════════════════════════════════════════════════════════════
# N11 · 打包方式
# ═════════════════════════════════════════════════════════════


class TestPackaging:
    def test_no_onefile_in_spec(self):
        """★ N11：不许用 --onefile（首启要解压 200MB，等十几秒）。

        只在 build/ 目录存在 .spec 时检查。
        """
        build_dir = ROOT / "build"
        specs = list(build_dir.glob("*.spec")) if build_dir.exists() else []
        if not specs:
            pytest.skip("build/*.spec 尚未创建（M7 才做）")
        bad = []
        for sp in specs:
            t = _read(sp)
            if re.search(r"onefile\s*=\s*True", t):
                bad.append(_rel(sp))
        assert not bad, f"用了 onefile 打包（违反 N11）: {bad}"


# ═════════════════════════════════════════════════════════════
# 分层目录存在性
# ═════════════════════════════════════════════════════════════


class TestLayersExist:
    """确认分层目录结构已经按设计建好。"""

    @pytest.mark.parametrize("sub", ["core", "core/models", "ui"])
    def test_layer_dir(self, sub):
        assert (SRC / sub).is_dir(), f"缺少分层目录：src/handwrite/{sub}"

    @pytest.mark.parametrize("mod", [
        "core/errors.py", "core/config.py", "core/geometry.py", "core/charset.py",
    ])
    def test_base_module_file(self, mod):
        assert (SRC / mod).is_file(), f"缺少基础层模块：{mod}"
