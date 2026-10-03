@echo off
cd /d "%~dp0"

rem 带控制台启动，出错信息会显示在这个窗口里。
rem 平时用「启动.bat」，那个没有黑窗口。

set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"

echo 以调试模式启动，错误信息会留在本窗口。
echo.
"%PY%" "main.py"

echo.
echo --- 程序已退出 ---
pause
