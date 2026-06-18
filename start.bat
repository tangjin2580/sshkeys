@echo off
title SSH Key Manager
cd /d "%~dp0"

:: 优先使用 venv，否则回退到系统 Python
if exist "venv\Scripts\python.exe" (
    set PYTHON=venv\Scripts\python.exe
) else (
    set PYTHON=python
)

echo ============================================
echo   SSH Key Manager
echo   Python: %PYTHON%
echo   参数:   %*
echo ============================================

%PYTHON% main.py %*
pause
