@echo off
setlocal

rem ============================================================
rem  手写字迹填充器 - 环境初始化（只需跑一次）
rem
rem  做什么：
rem    1) 用 Python 3.11 / 3.12 建 .venv
rem    2) 装依赖
rem    3) 以“可编辑模式”把本项目装进 .venv
rem
rem  为什么第 3 步重要：
rem    装成可编辑模式后，运行只需 python -m handwrite.main，
rem    不必再手工设置 PYTHONPATH。
rem
rem  注意：
rem    本机 WorkBuddy 会把 PYTHONPATH 指向它自己的工具目录，
rem    那里的 sitecustomize 会接管文件删除，导致 pip 报错。
rem    所以每条命令前都清空 PYTHONPATH / NODE_OPTIONS。
rem ============================================================

set "PYTHONPATH="
set "NODE_OPTIONS="

cd /d "%~dp0.."

echo.
echo ==================================================
echo   手写字迹填充器 - 环境初始化
echo ==================================================
echo.

rem -- 1. 找系统 Python（要 3.11 或 3.12）--
set "SYS_PY="

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    for %%V in (3.12 3.11) do (
        if not defined SYS_PY (
            py -%%V -c "import sys" >nul 2>nul
            if not errorlevel 1 set "SYS_PY=py -%%V"
        )
    )
)

if not defined SYS_PY (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 set "SYS_PY=python"
)

if not defined SYS_PY (
    echo   [错误] 没有找到 Python 3.11 或 3.12。
    echo   请先安装，或把 Python 加进 PATH。
    pause
    exit /b 1
)

echo   使用解释器：%SYS_PY%
%SYS_PY% -c "import sys; print('   版本：', sys.version.split()[0])"

rem -- 2. 建 venv --
if exist ".venv\Scripts\python.exe" (
    echo.
    echo   虚拟环境已存在，跳过创建。
) else (
    echo.
    echo   正在创建虚拟环境 .venv ...
    %SYS_PY% -m venv .venv
    if errorlevel 1 (
        echo   [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

set "PY=.venv\Scripts\python.exe"

rem -- 3. 装依赖 --
echo.
echo   正在安装依赖，首次约需几分钟，请勿关闭窗口 ...
echo.
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail

"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

rem -- 4. 可编辑安装 --
echo.
echo   正在把本项目装进虚拟环境 ...
"%PY%" -m pip install -e . --no-deps
if errorlevel 1 goto :fail

echo.
echo ==================================================
echo   初始化完成。
echo.
echo   启动方式：双击项目根目录的 run.bat
echo   跑测试：  .venv\Scripts\python.exe -m pytest
echo ==================================================
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo   [错误] 初始化中断，请看上面的报错。
echo.
pause
endlocal
exit /b 1
