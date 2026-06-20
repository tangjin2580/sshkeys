@echo off
REM SSH Key Manager PyInstaller 打包脚本
REM 必须使用 venv313 中的 PyInstaller（该环境已安装 PIL/Pillow）

set VENV=G:\code\sshkeys\venv313

echo [BUILD] 检查 venv313 PyInstaller...
"%VENV%\Scripts\pyinstaller.exe" --version >nul 2>&1
if errorlevel 1 (
    echo [BUILD] 正在安装 PyInstaller 到 venv313...
    "%VENV%\Scripts\pip.exe" install pyinstaller
)

echo [BUILD] 清理旧构建...
if exist build rmdir /s /q build
if exist dist\SSHKeyManager rmdir /s /q "dist\SSHKeyManager"

echo [BUILD] 开始打包（使用 venv313）...
"%VENV%\Scripts\pyinstaller.exe" main.spec

if errorlevel 1 (
    echo [BUILD] ❌ 打包失败
    pause
    exit /b 1
)

echo [BUILD] ✅ 打包完成：dist\SSHKeyManager\SSHKeyManager.exe
pause
