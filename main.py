"""
SSH Key Manager — 主入口（GUI 主面板 + 系统托盘）
启动后显示主面板窗口，后台运行 Waitress 服务器。
关闭窗口最小化到托盘，双击托盘恢复窗口。

用法:
    python main.py            # 有终端窗口（调试用）
    pythonw main.py           # 无终端窗口
    双击 start.vbs            # 无终端窗口（推荐）
    pyinstaller main.spec     # 打包为 exe
"""

import os
import sys
import socket
import time
import webbrowser
import threading
import logging
import argparse
from pathlib import Path

# 确保项目根目录在 sys.path 中（打包后也能找到 modules）
ROOT_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    # PyInstaller 打包后，资源在 _MEIPASS
    ROOT_DIR = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else ROOT_DIR
sys.path.insert(0, str(ROOT_DIR))

from modules.server import create_app, _sse_queues

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ==================== 配置 ====================
HOST = "127.0.0.1"
PORT = 5201
APP_URL = f"http://{HOST}:{PORT}"
APP_NAME = "SSH Key Manager"

# 开机自启注册表路径
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "SSHKeyManager"

# ==================== 全局状态 ====================
_shutting_down = False
_server_started = threading.Event()
_tray_icon = None        # pystray 图标实例
_main_window = None      # tkinter 主窗口实例


# ==================== 优雅关闭 ====================

def _cleanup_and_exit():
    """清理所有资源并退出"""
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    logger.info("正在关闭服务...")

    # 1. 关闭所有 SSE 队列
    for q in _sse_queues:
        try:
            q.put_nowait(None)
        except Exception:
            pass
    _sse_queues.clear()

    # 2. 关闭所有 WebSSH 会话
    try:
        from modules.webssh import _ssh_sessions, _ssh_lock, _close_ssh_session
        with _ssh_lock:
            sids = list(_ssh_sessions.keys())
        for sid in sids:
            try:
                _close_ssh_session(sid)
            except Exception:
                pass
        logger.info(f"已关闭 {len(sids)} 个 WebSSH 会话")
    except Exception:
        pass

    logger.info("服务已关闭")


# ==================== Waitress 生产服务器 ====================

_MAX_RESTARTS = 5
_MAX_BACKOFF = 30

def _serve_with_restart(app_obj):
    """使用 Waitress 提供服务，异常崩溃后自动重启。"""
    from waitress import serve
    restart_count = 0
    start_time = time.time()

    while not _shutting_down:
        try:
            logger.info("WSGI 服务器: Waitress（生产模式）")
            _server_started.set()
            serve(app_obj, host=HOST, port=PORT, threads=32,
                  channel_timeout=120, cleanup_interval=30)
            break
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            if _shutting_down:
                break
            restart_count += 1
            if time.time() - start_time > 300:
                restart_count = 1
            if restart_count > _MAX_RESTARTS:
                logger.error(f"连续重启 {_MAX_RESTARTS} 次仍失败，放弃重启")
                break
            delay = min(2 ** restart_count, _MAX_BACKOFF)
            logger.error(f"服务器异常退出: {e}")
            logger.info(f"{delay} 秒后重启（第 {restart_count}/{_MAX_RESTARTS} 次）...")
            time.sleep(delay)
            start_time = time.time()


# ==================== 端口就绪检测 ====================

def _wait_for_port(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((HOST, PORT), timeout=0.5)
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


# ==================== 图标生成 ====================

def _create_icon_image():
    """用 PIL 生成绿色钥匙图标，PIL 不可用时返回 None"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for i in range(size // 2, 0, -1):
        r = 34 + (size // 2 - i) * 2
        g = 197 + (size // 2 - i)
        b = 94
        draw.ellipse(
            [size // 2 - i, size // 2 - i, size // 2 + i, size // 2 + i],
            fill=(r, g, b, 255),
        )

    cx, cy = size // 2 - 8, size // 2
    r = 10
    draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                 outline=(255, 255, 255, 255), width=3)
    draw.ellipse([cx - r + 4, cy - r + 4, cx + r - 4, cy + r - 4],
                 fill=(34, 197, 94, 255))

    draw.rectangle([cx + r - 2, cy - 3, cx + r + 18, cy + 3],
                   fill=(255, 255, 255, 255))
    draw.rectangle([cx + r + 10, cy + 3, cx + r + 14, cy + 10],
                   fill=(255, 255, 255, 255))
    draw.rectangle([cx + r + 15, cy + 3, cx + r + 18, cy + 8],
                   fill=(255, 255, 255, 255))
    return img


def _icon_to_tk_photo(icon_img):
    """PIL Image → tkinter PhotoImage"""
    from PIL import ImageTk
    return ImageTk.PhotoImage(icon_img)


# ==================== 开机自启 ====================

def _is_autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _enable_autostart():
    try:
        import winreg
        if getattr(sys, "frozen", False):
            # 打包后直接用 exe 路径
            value = f'"{sys.executable}"'
        else:
            vbs_path = os.path.join(str(ROOT_DIR), "start.vbs")
            if os.path.exists(vbs_path):
                value = f'wscript.exe "{vbs_path}"'
            else:
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = sys.executable
                script_path = os.path.join(str(ROOT_DIR), "main.py")
                value = f'"{pythonw}" "{script_path}"'

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, value)
        return True
    except Exception as e:
        logger.error(f"启用开机自启失败: {e}")
        return False


def _disable_autostart():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
        return True
    except FileNotFoundError:
        return True
    except Exception as e:
        logger.error(f"禁用开机自启失败: {e}")
        return False


# ==================== GUI 主面板（tkinter） ====================

class MainPanel:
    """主面板窗口"""

    def __init__(self):
        import tkinter as tk
        from tkinter import ttk

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("380x240")
        self.root.resizable(False, False)
        self._center_window()

        # 关闭窗口（X）→ 最小化到托盘，不退出
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 图标
        try:
            self.icon_img = _create_icon_image()
            if self.icon_img is not None:
                self.tk_icon = _icon_to_tk_photo(self.icon_img)
                self.root.iconphoto(False, self.tk_icon)
        except Exception:
            pass

        # ---- 布局 ----
        # 顶部标题区
        header = tk.Frame(self.root, bg="#22c55e", height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header, text="SSH Key Manager", font=("Microsoft YaHei UI", 14, "bold"),
            bg="#22c55e", fg="white",
        ).pack(expand=True)

        # 中间状态区
        body = tk.Frame(self.root, padx=24, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, text="服务运行中", font=("Microsoft YaHei UI", 11),
            fg="#22c55e",
        ).pack(anchor="w", pady=(0, 4))

        tk.Label(
            body, text=f"地址：{APP_URL}", font=("Microsoft YaHei UI", 9),
            fg="#666666",
        ).pack(anchor="w")

        # 开机自启复选框
        self.autostart_var = tk.BooleanVar(value=_is_autostart_enabled())
        self.autostart_cb = tk.Checkbutton(
            body, text="开机自动启动", variable=self.autostart_var,
            font=("Microsoft YaHei UI", 9), fg="#444444",
            command=self._toggle_autostart,
        )
        self.autostart_cb.pack(anchor="w", pady=(8, 0))

        # 底部按钮区
        btn_frame = tk.Frame(self.root, padx=24, pady=16)
        btn_frame.pack(fill="x")

        self.btn_open = tk.Button(
            btn_frame, text="打开 Web 界面", font=("Microsoft YaHei UI", 10),
            bg="#22c55e", fg="white", relief="flat", cursor="hand2",
            padx=16, pady=6, command=self._open_web,
        )
        self.btn_open.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_quit = tk.Button(
            btn_frame, text="退出程序", font=("Microsoft YaHei UI", 10),
            bg="#ef4444", fg="white", relief="flat", cursor="hand2",
            padx=16, pady=6, command=self._quit,
        )
        self.btn_quit.pack(side="left", expand=True, fill="x", padx=(6, 0))

    def _center_window(self):
        """窗口居中"""
        self.root.update_idletasks()
        w = 380
        h = 240
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2 - 40
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _on_close(self):
        """关闭窗口 → 最小化到托盘"""
        self.root.withdraw()  # 隐藏窗口

    def show(self):
        """从托盘恢复窗口"""
        self.root.after(0, lambda: self.root.deiconify())

    def _open_web(self):
        """打开 Web 界面"""
        webbrowser.open(APP_URL)

    def _toggle_autostart(self):
        """切换开机自启"""
        if self.autostart_var.get():
            _enable_autostart()
        else:
            _disable_autostart()

    def _quit(self):
        """退出程序"""
        _cleanup_and_exit()
        # 停止托盘
        if _tray_icon:
            _tray_icon.stop()
        # 销毁窗口
        self.root.after(0, self.root.destroy)

    def run(self):
        """运行主循环"""
        self.root.mainloop()


# ==================== 系统托盘 ====================

def _start_tray(icon_img):
    """启动系统托盘（后台线程）"""
    global _tray_icon
    try:
        from pystray import Icon, Menu, MenuItem
    except ImportError as e:
        logger.warning(f"[主程序] pystray 不可用，系统托盘功能禁用: {e}")
        return

    def _tray_open(icon, item):
        """双击托盘 → 显示主面板"""
        if _main_window:
            _main_window.show()

    def _tray_quit(icon, item):
        """托盘退出 → 退出程序"""
        _cleanup_and_exit()
        icon.stop()
        if _main_window:
            _main_window.root.after(0, _main_window.root.destroy)

    _tray_icon = Icon(
        APP_NAME,
        icon=icon_img,
        title=APP_NAME,
        menu=Menu(
            MenuItem("打开主面板", _tray_open, default=True),
            MenuItem("打开 Web 界面", lambda i, it: webbrowser.open(APP_URL)),
            Menu.SEPARATOR,
            MenuItem(
                "开机自启",
                lambda i, it: _toggle_autostart_tray(i),
                checked=lambda item: _is_autostart_enabled(),
            ),
            Menu.SEPARATOR,
            MenuItem("退出程序", _tray_quit),
        ),
    )
    _tray_icon.run()


def _toggle_autostart_tray(icon):
    """托盘菜单切换开机自启"""
    if _is_autostart_enabled():
        _disable_autostart()
    else:
        _enable_autostart()
    icon.update_menu()
    # 同步主面板复选框
    if _main_window:
        _main_window.root.after(0, lambda: _main_window.autostart_var.set(_is_autostart_enabled()))


# ==================== 主入口 ====================

def main():
    global _main_window

    parser = argparse.ArgumentParser(description="SSH Key Manager")
    parser.add_argument("--dev", action="store_true",
                        help="开发模式：启用代码热重载（不启动 GUI）")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("  SSH Key Manager")
    logger.info("=" * 50)
    logger.info(f"  服务地址: {APP_URL}")
    logger.info(f"  运行模式: {'开发（热重载）' if args.dev else '生产（GUI + 托盘）'}")
    logger.info("=" * 50)

    app = create_app()

    if args.dev:
        # 开发模式：Flask 内置服务器 + 热重载
        logger.info("开发模式：Flask 开发服务器")
        threading.Timer(1.5, lambda: webbrowser.open(APP_URL)).start()
        app.run(host=HOST, port=PORT, debug=True, use_reloader=True)
        return

    # ==================== 生产模式 ====================

    # 1. 启动 Waitress 服务器（后台线程）
    server_thread = threading.Thread(target=_serve_with_restart, args=(app,), daemon=True)
    server_thread.start()

    # 2. 等待端口就绪
    if _wait_for_port():
        logger.info("服务器已就绪")
    else:
        logger.warning("服务启动超时")

    # 3. 启动系统托盘（后台线程）
    icon_img = _create_icon_image()
    tray_thread = threading.Thread(target=_start_tray, args=(icon_img,), daemon=True)
    tray_thread.start()

    # 4. 启动 GUI 主面板（主线程，阻塞）
    _main_window = MainPanel()
    _main_window.run()

    # mainloop 结束后清理
    _cleanup_and_exit()


if __name__ == "__main__":
    main()
