@echo off
chcp 65001 >nul
REM ============================================================================
REM cifkit 工作台启动器（Windows）
REM
REM 用法：
REM   start.cmd                    127.0.0.1:8780，默认显式开 --allow-exec（可跑 op/管线、可写 runs-dir）
REM   start.cmd --port 8790        换端口；仍带 --allow-exec
REM   start.cmd --readonly         只读浏览：不跑计算、不写 runs-dir（看历史 run / 页面演示）
REM   start.cmd --selftest         起服务自检后退出（退出码 0 = 通过）
REM
REM 只读的另一种等价做法（不经本启动器，直接调 server.py 且不带 --allow-exec）：
REM   ..\.venv\Scripts\python.exe server.py --port 8790
REM
REM 安全默认：server.py 自身默认只读（写动作 403 E_EXEC_DISABLED）；
REM 本启动器显式传 --allow-exec 是为了"双击即可干活"。若 --runs-dir 指向不可写目录，
REM 后端会自动只读（Bootstrap.caps.runs_writable=false，写动作 403 E_READONLY_RUNS）。
REM ============================================================================
setlocal
cd /d "%~dp0"
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "EXEC=--allow-exec"
set "ARGS=%*"
echo(%ARGS%| findstr /I /C:"--readonly" >nul
if not errorlevel 1 set "EXEC="
set "ARGS=%ARGS:--readonly=%"
echo [workbench] python = %PY%
echo [workbench] runs-dir = %~dp0state\runs
if defined EXEC (echo [workbench] exec = ON ^(--allow-exec^)) else (echo [workbench] exec = OFF ^(readonly^))
"%PY%" "%~dp0server.py" --port 8780 %EXEC% %ARGS%
endlocal
