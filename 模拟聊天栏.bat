@echo off
cd /d "%~dp0"

rem 打开一个模拟的游戏聊天栏，用来实验「只翻译新增的行」。
rem 用 pythonw 启动：用 python 会多一个黑窗口挡在聊天栏上面。

set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"

start "" "%PYW%" "模拟聊天栏.py"
