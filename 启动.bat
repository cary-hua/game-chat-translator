@echo off
cd /d "%~dp0"

rem 优先用本机实际安装的 Python。
rem 不能直接写 "pythonw"：PATH 里的 pythonw 很可能是 Windows 商店的占位符，
rem 双击后会弹出商店页面而不是启动程序。
set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"

if not exist "%PYW%" (
    for /f "delims=" %%i in ('where pythonw 2^>nul ^| findstr /vi "WindowsApps"') do (
        if not defined PYW set "PYW=%%i"
    )
)

if not exist "%PYW%" (
    echo.
    echo   [错误] 找不到 pythonw.exe
    echo   请先安装 Python 3.10 以上，然后执行: pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

start "" "%PYW%" "main.py"
