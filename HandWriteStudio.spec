# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 构建脚本（onedir，无控制台，带资源）。"""

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

block_cipher = None

# 把 src 加入导入搜索路径，确保能解析 handwrite 包
pathex = ["src"]

# 资源文件：打包后放到 sys._MEIPASS/resources/ 下，与 config.resource_root() 约定一致
datas = [
    ("src/handwrite/resources", "resources"),
]

a = Analysis(
    ["tools/entry.py"],
    pathex=pathex,
    binaries=[],
    datas=datas,
    hiddenimports=[
        # PySide6 子模块由 PyInstaller hooks 自动收集，这里显式兜底
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # cv2 / fitz 有时会被漏掉
        "cv2",
        "fitz",
        "pymupdf",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HandWriteStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HandWriteStudio",
)
