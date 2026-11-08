@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
title ComfyUI-Manager 镜像修复
echo 这会修改 ComfyUI-Manager 的配置和源码，每个改动的文件都会留 .bak_managerfix 备份。
echo.
choice /c YN /m "确定要继续吗"
if errorlevel 2 goto end
python "managerfix.py" --fix %*
:end
echo.
pause
