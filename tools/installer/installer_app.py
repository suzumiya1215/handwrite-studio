# -*- coding: utf-8 -*-
"""
手写字迹填充器 —— 自包含安装程序。

不依赖外部 Inno Setup / NSIS：本程序把已打好的 onedir 应用（payload）
随安装包一起分发，运行时把 payload 复制到用户选择的目标目录，
创建桌面 / 开始菜单快捷方式（用纯二进制 .lnk 生成器，绕开被禁用的 COM），
并在“应用和功能”中登记卸载项。

运行形态：
  - 正常双击：启动图形安装向导（PySide6 QWizard）。
  - 命令行：  HandWriteStudioSetup.exe --install-to <目录> [--silent]
             直接把应用安装到指定目录（用于自动化 / 测试），不弹窗。
"""
import os
import sys
import shutil
import subprocess
import threading
import time

# ---------------------------------------------------------------- 资源定位
def payload_dir():
    """随安装包装载的应用本体目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "_payload")
    # 开发态回退：直接用仓库里已打好的 onedir
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "dist", "HandWriteStudio")
    )


def asset_path(name):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "_assets", name)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", name))


APP_NAME = "手写字迹填充器"
APP_VERSION = "1.0.0"
APP_EXE = "HandWriteStudio.exe"
UNINST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\HandWriteStudio"


# ---------------------------------------------------------------- Windows 已知文件夹
def get_known_folder(csidl):
    """用 SHGetFolderPathW 取真实（含重定向）的已知文件夹路径。"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
        return buf.value
    except Exception:
        return None


CSIDL_DESKTOP = 0x10      # 桌面（已重定向时也正确）
CSIDL_PROGRAMS = 0x02     # 开始菜单\程序


def default_install_dir():
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    cand = os.path.join(pf, "HandWriteStudio")
    if _can_write(cand):
        return cand
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "Programs", "HandWriteStudio")


def _can_write(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".writetest")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        # 若目录为空则清理，避免留空壳
        try:
            if not os.listdir(path):
                os.rmdir(path)
        except Exception:
            pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 复制 + 进度
def _try_copy(src_file, dst_root, src_root):
    """复制单个文件；遇杀毒软件（360 / Defender）对新建 .dll 的临时锁返回
    False 由上层统一重试，不在单个文件上长阻塞。"""
    tgt = os.path.join(dst_root, os.path.relpath(src_file, src_root))
    try:
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        if os.path.exists(tgt):
            try:
                os.chmod(tgt, 0o666)
            except OSError:
                pass
        shutil.copy2(src_file, tgt)
        return True
    except (PermissionError, OSError):
        return False


def copy_payload(src, dst, on_progress=None, on_log=None):
    files = []
    for root, _dirs, fnames in os.walk(src):
        for fn in fnames:
            files.append(os.path.join(root, fn))
    total = len(files) or 1

    # 第一轮：尽可能快拷；被杀软临时锁住的文件先放下，不在此长阻塞
    done = 0
    failed = []
    for f in files:
        if _try_copy(f, dst, src):
            done += 1
        else:
            failed.append(f)
        if on_progress:
            on_progress(done + len(failed), total)
    if on_log and failed:
        on_log("首轮跳过 %d 个被安全软件临时锁定的文件，进入重试…" % len(failed))

    # 后续轮：对失败文件做短退避重试（杀软扫描锁通常 1 秒内释放）
    for rnd in range(30):
        if not failed:
            break
        time.sleep(0.5)
        still = []
        for f in failed:
            if _try_copy(f, dst, src):
                done += 1
            else:
                still.append(f)
        failed = still
        if on_progress:
            on_progress(done, total)
        if on_log and (rnd % 5 == 4) and failed:
            on_log("仍在复制被安全软件临时锁定的文件（剩 %d 个）…" % len(failed))

    if failed:
        raise RuntimeError(
            "以下文件复制失败（可能被安全软件持续占用）：%s"
            % ", ".join(os.path.basename(x) for x in failed[:8])
        )
    if on_log:
        on_log("已复制 %d 个文件到 %s" % (done, dst))


# ---------------------------------------------------------------- 快捷方式
def make_shortcuts(install_dir, on_log=None):
    from lnkbuild import make_lnk

    exe = os.path.join(install_dir, APP_EXE)
    desktop = get_known_folder(CSIDL_DESKTOP)
    programs = get_known_folder(CSIDL_PROGRAMS)

    paths = {}
    if desktop:
        d_lnk = os.path.join(desktop, APP_NAME + ".lnk")
        make_lnk(d_lnk, exe, working_dir=install_dir, icon=exe,
                 description=APP_NAME)
        paths["desktop"] = d_lnk
        if on_log:
            on_log("已创建桌面快捷方式")

    if programs:
        sm_dir = os.path.join(programs, APP_NAME)
        os.makedirs(sm_dir, exist_ok=True)
        s_lnk = os.path.join(sm_dir, APP_NAME + ".lnk")
        make_lnk(s_lnk, exe, working_dir=install_dir, icon=exe,
                 description=APP_NAME)
        paths["startmenu"] = s_lnk
        if on_log:
            on_log("已创建开始菜单快捷方式")
    return paths


# ---------------------------------------------------------------- 卸载脚本 + 注册表
def write_uninstaller(install_dir, shortcut_paths, on_log=None):
    desktop_lnk = shortcut_paths.get("desktop", "")
    sm_dir = os.path.dirname(shortcut_paths.get("startmenu", "")) \
        if shortcut_paths.get("startmenu") else ""

    # 卸载脚本用 cp936 写入（中文 Windows 的 cmd 默认代码页）
    bat = (
        "@echo off\n"
        "echo 正在卸载 " + APP_NAME + "...\n"
    )
    if desktop_lnk:
        bat += 'del /q "' + desktop_lnk + '" 2>nul\n'
    if sm_dir:
        bat += 'rmdir /s /q "' + sm_dir + '" 2>nul\n'
    bat += 'reg delete "HKCU\\' + UNINST_KEY + '" /f 2>nul\n'
    bat += 'cd /d "%TEMP%"\n'
    bat += 'rmdir /s /q "' + install_dir + '"\n'
    bat += "echo 卸载完成。\n"
    bat += "pause\n"

    bat_path = os.path.join(install_dir, "uninstall.bat")
    with open(bat_path, "w", encoding="cp936") as f:
        f.write(bat)

    # 在“应用和功能”登记卸载项（HKCU，无需管理员）
    try:
        import winreg
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "HandWriteStudio")
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, install_dir)
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ,
                          '"' + bat_path + '"')
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ,
                          os.path.join(install_dir, APP_EXE))
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(k)
        if on_log:
            on_log("已在“应用和功能”登记卸载项")
    except Exception as e:
        if on_log:
            on_log("警告：卸载项登记失败（%s）" % e)


# ---------------------------------------------------------------- 安装主流程
def install_to(install_dir, on_progress=None, on_log=None):
    src = payload_dir()
    if not os.path.isdir(src):
        raise RuntimeError("找不到应用安装包数据（payload 缺失）")

    os.makedirs(install_dir, exist_ok=True)
    if on_log:
        on_log("开始安装 %s %s" % (APP_NAME, APP_VERSION))
        on_log("目标目录：%s" % install_dir)

    copy_payload(src, install_dir, on_progress=on_progress, on_log=on_log)
    shortcuts = make_shortcuts(install_dir, on_log=on_log)
    write_uninstaller(install_dir, shortcuts, on_log=on_log)

    if on_log:
        on_log("安装完成！")
    return os.path.join(install_dir, APP_EXE)


# ---------------------------------------------------------------- 命令行模式
def run_cli(install_dir, silent=False):
    import time
    t0 = time.time()
    install_to(
        install_dir,
        on_log=(lambda m: print("[install]", m)) if not silent else None,
    )
    if not silent:
        print("安装耗时 %.1fs" % (time.time() - t0))


# ---------------------------------------------------------------- GUI 向导
def run_gui():
    from PySide6.QtWidgets import (
        QApplication, QWizard, QWizardPage, QLabel, QLineEdit, QPushButton,
        QFileDialog, QVBoxLayout, QHBoxLayout, QProgressBar, QPlainTextEdit,
        QCheckBox, QMessageBox,
    )
    from PySide6.QtCore import Qt, QThread, Signal, QUrl
    from PySide6.QtGui import QIcon, QDesktopServices

    class Worker(QThread):
        progress = Signal(int, int)
        log = Signal(str)
        finished = Signal(str)
        error = Signal(str)

        def __init__(self, target):
            super().__init__()
            self.target = target

        def run(self):
            try:
                exe = install_to(self.target,
                                 on_progress=self.progress.emit,
                                 on_log=self.log.emit)
                self.finished.emit(exe)
            except Exception as e:
                self.error.emit(str(e))

    class WelcomePage(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle("欢迎安装 %s" % APP_NAME)
            lay = QVBoxLayout(self)
            info = QLabel(
                "%s %s\n\n"
                "本向导将把程序安装到你的电脑。\n"
                "点击“下一步”继续，或“取消”退出安装。" % (APP_NAME, APP_VERSION)
            )
            info.setWordWrap(True)
            lay.addWidget(info)
            lay.addStretch(1)

    class LocationPage(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle("选择安装位置")
            lay = QVBoxLayout(self)
            lab = QLabel("安装目录：")
            lay.addWidget(lab)
            row = QHBoxLayout()
            self.edit = QLineEdit()
            self.edit.setText(default_install_dir())
            self.registerField("dir*", self.edit)
            btn = QPushButton("浏览...")
            btn.clicked.connect(self.browse)
            row.addWidget(self.edit)
            row.addWidget(btn)
            lay.addLayout(row)
            self.space = QLabel("")
            lay.addWidget(self.space)
            lay.addStretch(1)
            self.edit.textChanged.connect(self.update_space)

        def browse(self):
            d = QFileDialog.getExistingDirectory(self, "选择安装目录",
                                                 self.edit.text())
            if d:
                self.edit.setText(d)

        def update_space(self):
            try:
                free = shutil.disk_usage(self.edit.text() or "C:\\").free
                self.space.setText("该位置可用空间：%.1f GB" % (free / 1e9))
            except Exception:
                self.space.setText("")

        def initializePage(self):
            self.update_space()

        def validatePage(self):
            d = self.edit.text().strip()
            if not d:
                QMessageBox.warning(self, "提示", "请选择安装目录。")
                return False
            if not _can_write(d):
                QMessageBox.warning(self, "提示",
                                    "对该目录没有写入权限，请换一个位置。")
                return False
            return True

    class ProgressPage(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle("正在安装")
            self._done = False
            self._exe = None
            lay = QVBoxLayout(self)
            self.bar = QProgressBar()
            self.bar.setRange(0, 100)
            lay.addWidget(self.bar)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            lay.addWidget(self.log)

        def isComplete(self):
            return self._done

        def initializePage(self):
            self._done = False
            self.completeChanged.emit()
            self.bar.setValue(0)
            self.log.setPlainText("")
            target = self.field("dir")
            self.worker = Worker(target)
            self.worker.progress.connect(self.on_progress)
            self.worker.log.connect(lambda m: self.log.appendPlainText(m))
            self.worker.finished.connect(self.on_finished)
            self.worker.error.connect(self.on_error)
            self.worker.start()

        def on_progress(self, cur, total):
            self.bar.setValue(int(cur * 100 / total))

        def on_finished(self, exe):
            self._exe = exe
            self.bar.setValue(100)
            self.log.appendPlainText("安装成功！")
            self._done = True
            self.completeChanged.emit()
            # 自动切到“完成”按钮可点击
            wiz = self.wizard()
            wiz.button(QWizard.NextButton).setEnabled(True)

        def on_error(self, msg):
            self.log.appendPlainText("安装失败：" + msg)
            QMessageBox.critical(self, "安装失败", msg)
            self._done = True
            self.completeChanged.emit()

    class FinishPage(QWizardPage):
        def __init__(self):
            super().__init__()
            self.setTitle("安装完成")
            lay = QVBoxLayout(self)
            lab = QLabel("%s 已成功安装到你的电脑。" % APP_NAME)
            lay.addWidget(lab)
            self.launch = QCheckBox("立即启动 %s" % APP_NAME)
            self.launch.setChecked(True)
            lay.addWidget(self.launch)
            lay.addStretch(1)

        def initializePage(self):
            prog = self.wizard().page(2)
            self._exe = getattr(prog, "_exe", None)

        def validatePage(self):
            if self.launch.isChecked() and getattr(self, "_exe", None):
                try:
                    subprocess.Popen([self._exe],
                                     cwd=os.path.dirname(self._exe))
                except Exception as e:
                    QMessageBox.warning(self, "提示",
                                        "无法自动启动：%s" % e)
            return True

    app = QApplication.instance() or QApplication(sys.argv)
    icon = QIcon(asset_path("app.ico"))
    app.setWindowIcon(icon)

    wiz = QWizard()
    wiz.setWindowIcon(icon)
    wiz.setWindowTitle("%s 安装程序" % APP_NAME)
    wiz.setWizardStyle(QWizard.ModernStyle)
    wiz.resize(520, 380)
    wiz.setOptions(QWizard.NoBackButtonOnStartPage | QWizard.NoCancelButtonOnLastPage)

    wiz.addPage(WelcomePage())
    wiz.addPage(LocationPage())
    wiz.addPage(ProgressPage())
    wiz.addPage(FinishPage())

    # 把“下一步”文案本地化
    wiz.setButtonText(QWizard.NextButton, "下一步")
    wiz.setButtonText(QWizard.BackButton, "上一步")
    wiz.setButtonText(QWizard.CancelButton, "取消")
    wiz.setButtonText(QWizard.FinishButton, "完成")

    wiz.exec()


# ---------------------------------------------------------------- 入口
def main():
    args = sys.argv[1:]
    if "--install-to" in args:
        i = args.index("--install-to")
        target = args[i + 1] if i + 1 < len(args) else None
        if not target:
            print("用法: --install-to <目录> [--silent]")
            return 2
        silent = "--silent" in args
        run_cli(target, silent=silent)
        return 0
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
