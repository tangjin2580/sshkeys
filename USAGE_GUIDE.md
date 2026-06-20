# SSH Key Manager - 使用指南

## 启动方式

### 方式一：双击启动（推荐）
双击 `start.vbs` 文件即可启动，**无终端窗口**，直接在系统托盘显示图标。

### 方式二：命令行启动（调试用）
```bash
venv\Scripts\python.exe main.py --dev
```

## 系统托盘菜单

启动后，在 Windows 系统托盘（右下角）会显示一个绿色钥匙图标。

**右键菜单：**
- **打开 Web 界面** — 在浏览器中打开 SSH Key Manager（默认 http://127.0.0.1:5201）
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
    └── pythonw.exe main.py
            ├── 后台线程: Waitress 服务器 (port 5201)
            ├── tkinter GUI 主窗口
            └── 系统托盘图标 (pystray)
                    ├── 打开 Web 界面 → webbrowser.open()
                    ├── 开机自启 → winreg (HKCU\...\Run)
                    └── 关闭程序 → icon.stop()
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `main.py` | 应用入口（GUI + 托盘 + 服务器） |
| `start.vbs` | 无终端窗口启动脚本 |
| `modules/server.py` | Flask 应用工厂 |
| `modules/routes/` | API 路由蓝图 |
| `modules/ssh_config.py` | SSH config 解析/写入 |
| `modules/key_generator.py` | 密钥生成 |
| `modules/key_uploader.py` | 公钥上传（GitHub/GitLab/服务器） |
| `modules/connections_store.py` | 连接管理持久化 |
| `modules/webssh.py` | WebSSH 终端 + SFTP |
| `static/js/` | 前端模块化 JS |
| `templates/index.html` | 单页应用模板 |
