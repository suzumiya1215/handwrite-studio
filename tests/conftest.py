"""pytest 公共夹具。

设计依据：接口契约与测试用例.md §0.4
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 让 `import handwrite` 可用（不依赖 pip install -e）
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from handwrite.core.models.grid import GridSpec                # noqa: E402

# ★ PIL / fake_font 延迟导入：让 test_layering.py 这类纯静态扫描测试
#   在第三方依赖还没装好时也能跑（M1 阶段很有用）。


def _pil():
    from PIL import Image
    return Image


def _fake_font():
    from tests.fixtures.fake_font import make_fake_font
    return make_fake_font

# ─────────────────────────────────────────────────────────────
# 路径
# ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def project_root() -> Path:
    return _ROOT


# ─────────────────────────────────────────────────────────────
# 字形库
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def font():
    """12 字符齐全的假字形库（每个字符 2 样本）。"""
    return _fake_font()(samples_per_char=2)


@pytest.fixture
def font_1sample():
    """每字符只有 1 个样本的库（测「只有一种写法」的边界）。"""
    return _fake_font()(samples_per_char=1)


# ─────────────────────────────────────────────────────────────
# 图像
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def blank_a4():
    """A4 竖版白底图 @150dpi（约 1240×1754）。"""
    Image = _pil()
    return Image.new("RGB", (1240, 1754), (255, 255, 255))


@pytest.fixture
def blank_a4_300():
    """A4 竖版白底图 @300dpi（约 2480×3508）。"""
    Image = _pil()
    return Image.new("RGB", (2480, 3508), (255, 255, 255))


@pytest.fixture
def blank_landscape():
    """A4 横版白底图 @150dpi（约 1754×1240）。"""
    Image = _pil()
    return Image.new("RGB", (1754, 1240), (255, 255, 255))


# ─────────────────────────────────────────────────────────────
# 网格
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def basic_grid():
    """3 行 4 列的小网格，坐标好算，便于断言。"""
    return GridSpec(
        rows=3, cols=4,
        col_width_mm=15.0, row_height_mm=12.0,
        start_x_mm=20.0, start_y_mm=30.0,
        cell_padding_mm=1.0,
    )


@pytest.fixture
def full_grid():
    """24×10 的完整表格网格（需求文档里的典型场景）。"""
    return GridSpec(
        rows=24, cols=10,
        col_width_mm=15.0, row_height_mm=10.0,
        start_x_mm=20.0, start_y_mm=50.0,
        cell_padding_mm=1.0,
    )


# ─────────────────────────────────────────────────────────────
# 隔离的用户数据目录
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """把用户数据目录重定向到临时目录，避免测试污染真实 %LOCALAPPDATA%。"""
    from handwrite.core import config
    fake = tmp_path / "appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(fake))
    return fake / config.APP_NAME_EN
