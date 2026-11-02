@echo off
setlocal

rem ============================================================
rem  手写字迹填充器 - 一键启动（开发用）
rem
rem  双击本文件即可运行。
rem  前提：已执行过一次 .venv 初始化（跑 tools\init_env.bat）
rem
rem  打包后的正式版不走这里，走安装目录下的 exe。
rem ============================================================

rem 清空外部注入的环境变量。
rem 若 PYTHONPATH 指向别处，Python 启动时会自动加载那边的
rem sitecustomize，导致文件删改被接管、pip 报奇怪的错。
set "PYTHONPATH="
set "NODE_OPTIONS="

cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo   [错误] 没有找到虚拟环境 .venv
    echo.
    echo   请先双击运行：tools\init_env.bat
    echo.
    pause
    exit /b 1
)

rem 项目已用 pip install -e . 装进虚拟环境，无需再设 PYTHONPATH。
"%PY%" -m handwrite.main %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo   程序异常退出，代码 %RC% 。
    echo   日志目录：%LOCALAPPDATA%\HandWriteStudio\logs\
    echo.
    pause
)

endlocal
exit /b %RC%
