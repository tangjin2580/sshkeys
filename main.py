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
import signal
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

from modules.common import _sse_queues, _sse_lock
from modules.server import create_app

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
    """清理所有资源并优雅退出"""
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    logger.info("正在关闭服务...")

    # 1. 通知所有 SSE 连接优雅关闭（发送 None 哨兵）
    with _sse_lock:
        queues_snapshot = list(_sse_queues)
    for q in queues_snapshot:
        try:
            q.put_nowait(None)
        except Exception:
            pass

    # 2. 给 SSE 生成器时间发送剩余数据（等待 1 秒）
    time.sleep(1.0)

    # 3. 关闭所有 WebSSH 会话
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

    # 4. 停止系统托盘
    global _tray_icon
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass

    logger.info("服务已关闭")
    # 主线程会在 _quit() 中调用 root.destroy()，此处不主动退出


# ==================== Waitress 生产服务器 ====================

_MAX_RESTARTS = 5
_MAX_BACKOFF = 30

def _serve_with_restart(app_obj):
    """使用 Waitress 提供服务，异常崩溃后自动重启。"""
    from waitress import serve
    restart_count = 0

    while not _shutting_down:
        start_time = time.time()  # 每次尝试的起始时间
        try:
            logger.info("WSGI 服务器: Waitress（生产模式）")
            _server_started.set()
            serve(app_obj, host=HOST, port=PORT, threads=8,
                  channel_timeout=120, cleanup_interval=30)
            break
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            if _shutting_down:
                break
            restart_count += 1
            # 本次运行超过 300 秒，说明曾稳定运行，重置计数器
            if time.time() - start_time > 300:
                restart_count = 0
            if restart_count > _MAX_RESTARTS:
                logger.error(f"连续重启 {_MAX_RESTARTS} 次仍失败，放弃重启")
                break
            delay = min(2 ** restart_count, _MAX_BACKOFF)
            logger.error(f"服务器异常退出: {e}")
            logger.info(f"{delay} 秒后重启（第 {restart_count}/{_MAX_RESTARTS} 次）...")
            time.sleep(delay)


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
    """
    优先从 asset/icon.ico 加载图标（与 exe 图标一致），
    PyInstaller 打包后资源在 sys._MEIPASS 下。
    加载失败则用 PIL 生成绿色钥匙图标兜底。
    """
    # 1. 尝试从文件加载
    try:
        from PIL import Image
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base = Path(sys._MEIPASS)
        else:
            base = ROOT_DIR
        icon_path = base / "asset" / "icon.ico"
        if icon_path.exists():
            img = Image.open(icon_path)
            if img.mode not in ("RGBA", "RGB"):
                img = img.convert("RGBA")
            if img.size != (64, 64):
                img = img.resize((64, 64), Image.LANCZOS)
            return img
    except Exception:
        pass

    # 2. 兜底：PIL 生成绿色钥匙图标
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
    """读取开机自启注册表。非 Windows 始终返回 False。"""
    if not sys.platform.startswith("win"):
        return False
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
    """写入开机自启注册表。非 Windows 直接返回 False。"""
    if not sys.platform.startswith("win"):
        return False
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
    """删除开机自启注册表。非 Windows 直接返回 True（已是禁用状态）。"""
    if not sys.platform.startswith("win"):
        return True
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


# ==================== GUI 主面板（CustomTkinter 现代化） ====================

class MainPanel:
    """
    主面板窗口（540x520，CustomTkinter 暗色主题）
    - 顶部：绿色标题栏
    - 中部：CTkTabview 分页（服务状态 / 进程管理 / SFTP 设置）
    - 底部：打开 Web / 退出
    """

    def __init__(self):
        import customtkinter as ctk

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title(APP_NAME)
        self.root.geometry("540x520")
        self.root.resizable(False, False)
        self._center_window()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 图标
        try:
            self.icon_img = _create_icon_image()
            if self.icon_img is not None:
                self.tk_icon = _icon_to_tk_photo(self.icon_img)
                self.root.iconphoto(False, self.tk_icon)
        except Exception:
            pass

        # ---- 顶部标题（深色融合，不再抢眼）----
        header = ctk.CTkFrame(self.root, height=52, fg_color="#1e293b")
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header, text="SSH Key Manager",
            font=("Microsoft YaHei UI", 15, "bold"),
            text_color="#e2e8f0",
        ).pack(expand=True)

        # ---- CTkTabview 分页 ----
        self.tabview = ctk.CTkTabview(self.root, height=380)
        self.tabview.pack(fill="both", expand=True, padx=14, pady=(10, 6))

        self.tabview.add("服务状态")
        self.tabview.add("进程管理")
        self.tabview.add("SFTP 设置")

        self._build_tab_status()
        self._build_tab_processes()
        self._build_tab_sftp()

        # ---- 底部栏 ----
        bottom = ctk.CTkFrame(self.root, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(4, 12))

        btn_open = ctk.CTkButton(
            bottom, text="打开 Web 界面", font=("Microsoft YaHei UI", 11),
            corner_radius=8, height=36,
            command=self._open_web,
        )
        btn_open.pack(side="left", expand=True, fill="x", padx=(0, 6))

        btn_quit = ctk.CTkButton(
            bottom, text="退出程序", font=("Microsoft YaHei UI", 11),
            fg_color="#475569", hover_color="#334155",
            corner_radius=8, height=36,
            command=self._quit,
        )
        btn_quit.pack(side="left", expand=True, fill="x", padx=(6, 0))

        self._poll_sse()
        self._poll_processes()

    # ======================== 页1：服务状态 ========================

    def _build_tab_status(self):
        import customtkinter as ctk

        tab = self.tabview.tab("服务状态")

        # ---- 服务地址（紧凑单行，不占整张卡片）----
        addr_row = ctk.CTkFrame(tab, fg_color="transparent")
        addr_row.pack(fill="x", padx=8, pady=(8, 6))

        ctk.CTkLabel(
            addr_row, text="服务地址",
            font=("Microsoft YaHei UI", 9),
            text_color="#94a3b8",
        ).pack(side="left")

        addr_url = ctk.CTkLabel(
            addr_row, text=APP_URL,
            font=("Consolas", 12, "bold"),
            text_color="#38bdf8",
            cursor="hand2",
        )
        addr_url.pack(side="left", padx=(6, 0))
        addr_url.bind("<Button-1>", lambda e: webbrowser.open(APP_URL))

        # ---- 统计卡片行（SSE + WebSSH 并排）----
        stats_row = ctk.CTkFrame(tab, fg_color="transparent")
        stats_row.pack(fill="x", padx=8, pady=(0, 6))

        # SSE 卡片
        sse_card = ctk.CTkFrame(stats_row, corner_radius=10,
                                fg_color="#1e293b", border_color="#334155", border_width=1)
        sse_card.pack(side="left", fill="both", expand=True, padx=(0, 4))

        self.lbl_sse_num = ctk.CTkLabel(
            sse_card, text="—",
            font=("Consolas", 26, "bold"),
            text_color="#38bdf8",
        )
        self.lbl_sse_num.pack(anchor="w", padx=14, pady=(12, 0))

        ctk.CTkLabel(
            sse_card, text="SSE 活跃连接",
            font=("Microsoft YaHei UI", 8),
            text_color="#94a3b8",
        ).pack(anchor="w", padx=14, pady=(0, 12))

        # WebSSH 卡片
        ssh_card = ctk.CTkFrame(stats_row, corner_radius=10,
                                fg_color="#1e293b", border_color="#334155", border_width=1)
        ssh_card.pack(side="left", fill="both", expand=True, padx=(4, 0))

        self.lbl_session_num = ctk.CTkLabel(
            ssh_card, text="—",
            font=("Consolas", 26, "bold"),
            text_color="#a78bfa",
        )
        self.lbl_session_num.pack(anchor="w", padx=14, pady=(12, 0))

        ctk.CTkLabel(
            ssh_card, text="WebSSH 活跃会话",
            font=("Microsoft YaHei UI", 8),
            text_color="#94a3b8",
        ).pack(anchor="w", padx=14, pady=(0, 12))

        # ---- SSE 清理按钮 ----
        btn_cleanup = ctk.CTkButton(
            tab, text="清理僵死 SSE 连接",
            command=self._cleanup_sse,
            fg_color="transparent", border_color="#475569",
            border_width=1, text_color="#94a3b8",
            corner_radius=8, height=30, font=("Microsoft YaHei UI", 9),
            hover_color="#1e293b",
        )
        btn_cleanup.pack(fill="x", padx=8, pady=(4, 4))

        self.var_sse_cleanup_status = ctk.StringVar(value="")
        self.lbl_sse_status = ctk.CTkLabel(
            tab, textvariable=self.var_sse_cleanup_status,
            font=("Microsoft YaHei UI", 8), text_color="#94a3b8",
        )
        self.lbl_sse_status.pack(anchor="w", padx=12, pady=(0, 4))

        # ---- 开机自启（仅 Windows）----
        if sys.platform.startswith("win"):
            self.autostart_var = ctk.BooleanVar(value=_is_autostart_enabled())
            cb = ctk.CTkCheckBox(
                tab, text="开机自动启动", variable=self.autostart_var,
                command=self._toggle_autostart,
                font=("Microsoft YaHei UI", 9),
                checkbox_width=18, checkbox_height=18,
            )
            cb.pack(anchor="w", padx=12, pady=(12, 4))

    # ======================== 页2：进程管理 ========================

    def _build_tab_processes(self):
        import customtkinter as ctk
        import tkinter as tk

        tab = self.tabview.tab("进程管理")

        ctk.CTkLabel(
            tab, text="管理本程序相关的 Python 进程",
            font=("Microsoft YaHei UI", 9), text_color="gray60",
        ).pack(anchor="w", padx=8, pady=(4, 6))

        # 列表框（CTk 无 Listbox，用 tk.Listbox + 暗色主题）
        list_frame = ctk.CTkFrame(tab, corner_radius=8)
        list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        self.proc_listbox = tk.Listbox(
            list_frame, font=("Consolas", 8),
            selectmode="extended", height=10,
            bg="#1e1e1e", fg="#d4d4d4", selectbackground="#3b82f6",
            selectforeground="white", relief="flat", borderwidth=0,
            highlightthickness=0,
        )
        self.proc_listbox.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        scrollbar = ctk.CTkScrollbar(list_frame, command=self.proc_listbox.yview)
        scrollbar.pack(side="right", fill="y", pady=4)
        self.proc_listbox.config(yscrollcommand=scrollbar.set)

        # 按钮行
        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkButton(btn_row, text="刷新列表", command=self._refresh_processes,
                       corner_radius=8, height=30, font=("Microsoft YaHei UI", 9)
                       ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(btn_row, text="结束选中进程", command=self._kill_selected_processes,
                       corner_radius=8, height=30, font=("Microsoft YaHei UI", 9),
                       fg_color="#7f1d1d", hover_color="#991b1b").pack(side="left")

        self.var_proc_status = ctk.StringVar(value="点击「刷新列表」加载进程")
        ctk.CTkLabel(
            tab, textvariable=self.var_proc_status,
            font=("Microsoft YaHei UI", 8), text_color="gray60",
        ).pack(anchor="w", padx=12, pady=(2, 4))

    # ======================== 页3：SFTP 设置 ========================

    def _build_tab_sftp(self):
        import customtkinter as ctk
        import modules.config as _cfg

        tab = self.tabview.tab("SFTP 设置")
        _cfg.load_config()
        current_mb = _cfg.get("sftp_max_download_mb", 100)

        ctk.CTkLabel(
            tab, text="通过 WebSSH 下载文件时，单文件大小上限：",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=8, pady=(8, 10))

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(anchor="w", padx=8, pady=(0, 12))

        ctk.CTkLabel(row, text="大小上限：", font=("Microsoft YaHei UI", 9, "bold")).pack(
            side="left")

        self.var_sftp_mb = ctk.IntVar(value=current_mb)

        def _inc():
            v = self.var_sftp_mb.get()
            if v < 2048:
                self.var_sftp_mb.set(v + 50)

        def _dec():
            v = self.var_sftp_mb.get()
            if v > 1:
                self.var_sftp_mb.set(v - 50)

        btn_dec = ctk.CTkButton(row, text="−", width=28, height=24,
                                 command=_dec, corner_radius=6,
                                 font=("Consolas", 10, "bold"),
                                 fg_color="gray30", hover_color="gray40")
        btn_dec.pack(side="left", padx=(0, 4))

        entry = ctk.CTkEntry(row, textvariable=self.var_sftp_mb, width=70,
                              font=("Consolas", 10), corner_radius=6,
                              justify="center")
        entry.pack(side="left", padx=4)

        btn_inc = ctk.CTkButton(row, text="+", width=28, height=24,
                                 command=_inc, corner_radius=6,
                                 font=("Consolas", 10, "bold"),
                                 fg_color="gray30", hover_color="gray40")
        btn_inc.pack(side="left", padx=(4, 6))

        ctk.CTkLabel(row, text="MB", font=("Microsoft YaHei UI", 9)).pack(side="left")

        save_btn = ctk.CTkButton(
            tab, text="保存设置", font=("Microsoft YaHei UI", 10),
            corner_radius=8, height=32,
            command=self._save_sftp_limit,
        )
        save_btn.pack(anchor="w", padx=8, pady=(0, 12))

        self.var_sftp_status = ctk.StringVar(value="")
        self.lbl_sftp_status = ctk.CTkLabel(
            tab, textvariable=self.var_sftp_status,
            font=("Microsoft YaHei UI", 8),
        )
        self.lbl_sftp_status.pack(anchor="w", padx=8)

        ctk.CTkFrame(tab, height=1, fg_color="gray30").pack(fill="x", padx=8, pady=10)

        note = ctk.CTkLabel(
            tab,
            text="提示：修改后即时生效，已在进行中的下载不受影响。\n"
                  "设置为 0 表示不限制（谨慎使用，大文件可能耗尽内存）。",
            font=("Microsoft YaHei UI", 8), text_color="gray60",
            justify="left",
        )
        note.pack(anchor="w", padx=8, pady=(4, 0))

    # ======================== 窗口行为 ========================

    def _center_window(self):
        self.root.update_idletasks()
        w, h = 540, 520
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2 - 40}")

    def _on_close(self):
        self.root.withdraw()

    def show(self):
        self.root.after(0, lambda: self.root.deiconify())

    def _open_web(self):
        webbrowser.open(APP_URL)

    def _toggle_autostart(self):
        if self.autostart_var.get():
            _enable_autostart()
        else:
            _disable_autostart()

    def _quit(self):
        _cleanup_and_exit()
        if _tray_icon:
            _tray_icon.stop()
        self.root.after(0, self.root.destroy)

    def run(self):
        self.root.mainloop()

    # ======================== SSE 状态轮询 ========================

    def _poll_sse(self):
        """每 2 秒刷新 SSE 连接数和 WebSSH 会话数。"""
        try:
            from modules.server import _sse_queues
            count = len(_sse_queues)
            self.lbl_sse_num.configure(text=str(count))
        except Exception:
            self.lbl_sse_num.configure(text="—")

        try:
            from modules.webssh_sessions import _ssh_sessions
            count = len(_ssh_sessions)
            self.lbl_session_num.configure(text=str(count))
        except Exception:
            self.lbl_session_num.configure(text="—")

        self.root.after(2000, self._poll_sse)

    def _cleanup_sse(self):
        """清理僵死 SSE 连接"""
        import tkinter.messagebox as mbox
        try:
            from modules.common import _sse_cleanup_stale, get_sse_queue_count
            before = get_sse_queue_count()
            removed = _sse_cleanup_stale()
            after = get_sse_queue_count()
            self.lbl_sse_num.configure(text=str(after))
            if removed > 0:
                self.var_sse_cleanup_status.set(f"已清理 {removed} 个僵死连接")
            else:
                self.var_sse_cleanup_status.set("没有需要清理的连接")
            self.root.after(2000, lambda: self.var_sse_cleanup_status.set(""))
        except Exception as e:
            mbox.showerror("清理失败", str(e))

    # ======================== 进程管理 ========================

    def _refresh_processes(self):
        """刷新进程列表"""
        self.proc_listbox.delete(0, "end")
        self._proc_pids = []
        try:
            self._proc_pids = self._list_python_processes()
            if not self._proc_pids:
                self.proc_listbox.insert("end", "未找到 Python 进程")
                self.var_proc_status.set("未找到 Python 进程")
            else:
                for info in self._proc_pids:
                    self.proc_listbox.insert(
                        "end", f"PID {info['pid']:>6}  {info['mem']:>8} KB  {info['cmd'][:60]}")
                self.var_proc_status.set(f"共 {len(self._proc_pids)} 个进程")
        except Exception as e:
            self.proc_listbox.insert("end", f"刷新失败: {e}")
            self.var_proc_status.set(f"错误: {e}")

    def _list_python_processes(self):
        """
        跨平台枚举 Python 相关进程。
        Windows:  tasklist /fo csv /nh
        Linux:    ps -ww -o pid=,rss=,args=
        macOS:    ps -ww -o pid=,rss=,args=
        返回 [{"pid":..., "cmd":..., "mem":...}, ...]
        """
        import sys
        import subprocess
        import csv
        import io

        is_win = sys.platform.startswith("win")
        results = []

        try:
            if is_win:
                out = subprocess.check_output(
                    "tasklist /fo csv /nh",
                    shell=True, timeout=10,
                ).decode("gbk", errors="replace")
                reader = csv.reader(io.StringIO(out))
                for row in reader:
                    if len(row) < 5:
                        continue
                    name = row[0].lower()
                    if "python" not in name and "sshkeys" not in name:
                        continue
                    try:
                        pid = int(row[1])
                        mem_str = row[4].replace(",", "").replace(" K", "").replace(" KB", "")
                        mem_kb = int(mem_str) if mem_str.isdigit() else 0
                    except (ValueError, IndexError):
                        continue
                    results.append({"pid": pid, "cmd": row[0], "mem": mem_kb})

            else:
                out = subprocess.check_output(
                    ["ps", "-ww", "-o", "pid=,rss=,args="],
                    timeout=10,
                ).decode("utf-8", errors="replace")

                for raw_line in out.strip().splitlines():
                    line = raw_line.strip()
                    if not line or not line[:1].isdigit():
                        continue
                    parts = line.split(None, 2)
                    if len(parts) < 3:
                        continue
                    try:
                        pid = int(parts[0])
                        mem_kb = int(parts[1])
                    except ValueError:
                        continue
                    full_cmd = parts[2] if len(parts) > 2 else ""
                    combined = full_cmd.lower()
                    if "python" not in combined and "sshkeys" not in combined:
                        continue
                    display_cmd = full_cmd[:80] if len(full_cmd) > 80 else full_cmd
                    results.append({"pid": pid, "cmd": display_cmd, "mem": mem_kb})

        except Exception as e:
            print(f"[进程列表] 失败: {e}")
            return results

        results.sort(key=lambda x: x["pid"])
        return results

    def _kill_selected_processes(self):
        """结束 Listbox 中选中的进程（跨平台，带确认弹窗）。"""
        import sys
        import tkinter.messagebox as mbox
        import subprocess

        sel = self.proc_listbox.curselection()
        if not sel:
            mbox.showwarning("提示", "请先选中要结束的进程")
            return

        pids = [self._proc_pids[i]["pid"] for i in sel]
        pid_str = ", ".join(str(p) for p in pids)

        if not mbox.askyesno("确认",
            f"确定要结束以下进程吗？\n\nPID: {pid_str}\n\n此操作不可撤销！"):
            return

        is_win = sys.platform.startswith("win")
        killed, failed = [], []

        for pid in pids:
            try:
                if is_win:
                    r = subprocess.run(
                        f"taskkill /PID {pid} /F",
                        shell=True, capture_output=True, timeout=10,
                    )
                    if r.returncode == 0:
                        killed.append(str(pid))
                    else:
                        err = r.stderr.decode("gbk", errors="replace").strip()[:60]
                        failed.append(f"PID {pid}（{err or '拒绝访问'})")
                else:
                    r = subprocess.run(
                        ["kill", "-9", str(pid)],
                        capture_output=True, timeout=10,
                    )
                    if r.returncode == 0:
                        killed.append(str(pid))
                    else:
                        err = r.stderr.decode("utf-8", errors="replace").strip()[:60]
                        failed.append(f"PID {pid}（{err or '无权限'})")
            except Exception as e:
                failed.append(f"PID {pid}（{e}）")

        parts = []
        if killed:
            parts.append(f"已结束：{', '.join(killed)}")
        if failed:
            parts.append(f"失败：{', '.join(failed)}")
        mbox.showinfo("结束进程", "\n".join(parts) or "未结束任何进程")
        self._refresh_processes()

    # ======================== SFTP 设置 ========================

    def _save_sftp_limit(self):
        """保存 SFTP 大小限制到 config.json。"""
        try:
            mb = self.var_sftp_mb.get()
            if mb < 0:
                raise ValueError("不能为负数")
        except Exception:
            self.var_sftp_status.set("请输入有效的数字（1~2048）")
            self.lbl_sftp_status.configure(text_color="#f87171")
            return

        try:
            import modules.config as _cfg
            _cfg.set("sftp_max_download_mb", mb)
            self.var_sftp_status.set(f"已保存，新的上限为 {mb} MB（即时生效）")
            self.lbl_sftp_status.configure(text_color="#38bdf8")
        except Exception as e:
            self.var_sftp_status.set(f"保存失败：{e}")
            self.lbl_sftp_status.configure(text_color="#f87171")

    # ======================== 进程列表轮询 ========================

    def _poll_processes(self):
        """每 5 秒自动刷新进程列表（仅在「进程管理」页可见时）。"""
        try:
            if self.tabview.get() == "进程管理":
                self._refresh_processes()
        except Exception:
            pass
        self.root.after(5000, self._poll_processes)


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

    # 构建托盘菜单（自启选项仅 Windows）
    menu_items = [
        MenuItem("打开主面板", _tray_open, default=True),
        MenuItem("打开 Web 界面", lambda i, it: webbrowser.open(APP_URL)),
        Menu.SEPARATOR,
    ]
    if sys.platform.startswith("win"):
        menu_items.append(
            MenuItem(
                "开机自启",
                lambda i, it: _toggle_autostart_tray(i),
                checked=lambda item: _is_autostart_enabled(),
            )
        )
        menu_items.append(Menu.SEPARATOR)
    menu_items.append(MenuItem("退出程序", _tray_quit))

    _tray_icon = Icon(
        APP_NAME,
        icon=icon_img,
        title=APP_NAME,
        menu=Menu(*menu_items),
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
    try:
        _main_window.run()
    except (KeyboardInterrupt, SystemExit):
        pass

    # mainloop 结束后清理
    _cleanup_and_exit()


if __name__ == "__main__":
    main()
