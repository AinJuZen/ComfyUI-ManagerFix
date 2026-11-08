@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
title ComfyUI-Manager 镜像检查
python "managerfix.py" --check %*
echo.
pause
