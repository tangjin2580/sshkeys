# SSH Key Manager - 使用指南

## 启动方式

### 方式一：双击启动（推荐）
双击 `start.vbs` 文件即可启动，**无终端窗口**，直接在系统托盘显示图标。

### 方式二：命令行启动
```bash
venv\Scripts\pythonw.exe tray.py
```

## 系统托盘菜单

启动后，在 Windows 系统托盘（右下角）会显示一个绿色钥匙图标。

**右键菜单：**
- **打开 Web 界面** — 在浏览器中打开 SSH Key Manager（默认 http://127.0.0.1:5000）
- **开机自启** — 勾选后开机自动启动（写入注册表，无需管理员权限）
- **关闭程序** — 停止服务器并退出托盘应用

**双击图标** = 打开 Web 界面

## 开机自启

1. 右键托盘图标 → 勾选"开机自启"
2. 下次开机时，系统会自动运行 `start.vbs`，无窗口后台启动

取消开机自启：右键托盘图标 → 取消勾选"开机自启"

## 技术架构

```
start.vbs (无窗口启动)
    └── pythonw.exe tray.py (系统托盘应用)
            ├── 后台线程: Flask 服务器 (port 5000)
            └── 系统托盘图标 (pystray)
                    ├── 打开 Web 界面 → webbrowser.open()
                    ├── 开机自启 → winreg (HKCU\...\Run)
                    └── 关闭程序 → icon.stop()
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `tray.py` | 系统托盘应用主程序 |
| `start.vbs` | 无终端窗口启动脚本 |
| `run.py` | 纯命令行启动（有终端窗口，调试用） |
| `modules/server.py` | Flask 服务器主程序 |
| `modules/webssh.py` | WebSSH 功能模块 |
