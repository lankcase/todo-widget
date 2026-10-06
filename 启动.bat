@echo off
chcp 65001 >nul
rem 用 pythonw 启动，不留控制台窗口
start "" pythonw "%~dp0main.py"
