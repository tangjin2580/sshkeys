"""
SSH Key Manager - 系统托盘应用
无终端窗口后台运行 Flask 服务器，托盘图标右键菜单：打开 Web / 开机自启 / 关闭程序
"""

import os
import sys
import threading
import webbrowser

# 确保能导入项目模块
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem


# ============ 配置 ============
HOST = "127.0.0.1"
PORT = 5000
WEB_URL = f"http://{HOST}:{PORT}"
APP_NAME = "SSH Key Manager"

# 开机自启注册表路径
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "SSHKeyManager"


# ============ 图标生成 ============
def create_icon_image():
    """用 PIL 生成一个简单的钥匙图标"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆形背景（渐变绿色）
    for i in range(size // 2, 0, -1):
        alpha = 255
        r = 34 + (size // 2 - i) * 2
        g = 197 + (size // 2 - i)
        b = 94
        draw.ellipse(
            [size // 2 - i, size // 2 - i, size // 2 + i, size // 2 + i],
            fill=(r, g, b, alpha),
        )

    # 钥匙圆环（白色）
    cx, cy = size // 2 - 8, size // 2
    r = 10
    draw.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        outline=(255, 255, 255, 255),
        width=3,
    )
    # 钥匙圆环内部透明
    draw.ellipse(
        [cx - r + 4, cy - r + 4, cx + r - 4, cy + r - 4],
        fill=(34, 197, 94, 255),
    )

    # 钥匙杆（白色矩形）
    draw.rectangle(
        [cx + r - 2, cy - 3, cx + r + 18, cy + 3],
        fill=(255, 255, 255, 255),
    )
    # 钥匙齿
    draw.rectangle(
        [cx + r + 10, cy + 3, cx + r + 14, cy + 10],
        fill=(255, 255, 255, 255),
    )
    draw.rectangle(
        [cx + r + 15, cy + 3, cx + r + 18, cy + 8],
        fill=(255, 255, 255, 255),
    )

    return img


# ============ Flask 服务器 ============
_server_thread = None
_server_started = threading.Event()


def start_server():
    """在后台线程中启动 Flask 服务器"""
    global _server_thread

    def _run():
        try:
            from modules.server import app

            # 关闭 Werkzeug 的请求日志输出（避免终端输出）
            import logging

            logging.getLogger("werkzeug").setLevel(logging.ERROR)

            _server_started.set()
            app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
        except Exception as e:
            print(f"[ERROR] 服务器启动失败: {e}", file=sys.stderr)
            _server_started.set()  # 即使失败也设置，避免主线程永久等待

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
    # 等待服务器启动
    _server_started.wait(timeout=10)


# ============ 开机自启 ============
def is_autostart_enabled():
    """检查开机自启是否已启用"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def enable_autostart():
    """启用开机自启"""
    try:
        import winreg

        # 使用 pythonw.exe 无窗口启动
        pythonw = os.path.join(
            os.path.dirname(sys.executable), "pythonw.exe"
        )
        if not os.path.exists(pythonw):
            pythonw = sys.executable

        script_path = os.path.join(BASE_DIR, "tray.py")
        vbs_path = os.path.join(BASE_DIR, "start.vbs")

        # 优先使用 VBS 脚本启动（确保无窗口）
        if os.path.exists(vbs_path):
            value = f'wscript.exe "{vbs_path}"'
        else:
            value = f'"{pythonw}" "{script_path}"'

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            AUTOSTART_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, value)
        return True
    except Exception as e:
        print(f"[ERROR] 启用开机自启失败: {e}", file=sys.stderr)
        return False


def disable_autostart():
    """禁用开机自启"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            AUTOSTART_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
        return True
    except FileNotFoundError:
        return True  # 不存在就是已禁用
    except Exception as e:
        print(f"[ERROR] 禁用开机自启失败: {e}", file=sys.stderr)
        return False


def toggle_autostart(icon, item):
    """切换开机自启状态"""
    if is_autostart_enabled():
        disable_autostart()
    else:
        enable_autostart()
    icon.update_menu()


# ============ 菜单动作 ============
def open_web(icon=None, item=None):
    """打开 Web 界面"""
    webbrowser.open(WEB_URL)


def quit_app(icon, item):
    """关闭程序"""
    icon.stop()


# ============ 主入口 ============
def main():
    # 1. 先启动 Flask 服务器（后台线程）
    start_server()

    # 2. 创建系统托盘图标
    icon = Icon(
        APP_NAME,
        icon=create_icon_image(),
        title=APP_NAME,
        menu=Menu(
            MenuItem(
                "打开 Web 界面",
                open_web,
                default=True,  # 双击托盘图标也触发此动作
            ),
            Menu.SEPARATOR,
            MenuItem(
                "开机自启",
                toggle_autostart,
                checked=lambda item: is_autostart_enabled(),
            ),
            Menu.SEPARATOR,
            MenuItem("关闭程序", quit_app),
        ),
    )

    # 3. 运行托盘（阻塞主线程，直到 icon.stop() 被调用）
    icon.run()


if __name__ == "__main__":
    main()
