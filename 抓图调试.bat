@echo off
cd /d "%~dp0"

rem 排查「译文重复」专用：把每次的截图和翻译结果都记到 captures 目录。

set GCT_SAVE_CAPTURE=1
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo  抓图调试模式
echo.
echo  每次按热键都会在 captures 目录记下两样东西：
echo    *.jpg          模型看到的画面
echo    results.jsonl  模型返回的译文 + 悬浮窗实际显示的文本
echo.
echo  复现问题后关掉程序，把这两样拿出来对照
echo ============================================================
echo.

"%PY%" "main.py"

echo.
echo --- 程序已退出，记录都在 captures 目录里 ---
pause
