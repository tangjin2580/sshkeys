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
_tray_icon = None        # 系统托盘实例
_main_window = None      # 主窗口实例


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

    logger.info("服务已关闭")


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


# ==================== GUI 主面板（PyQt6） ====================

class MainPanel:
    """主面板窗口（PyQt6，现代暗色主题，580×580）"""

    def __init__(self):
        from PyQt6.QtWidgets import (
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QLabel, QPushButton, QTabWidget, QFrame, QListWidget,
            QSpinBox, QMessageBox, QCheckBox, QSystemTrayIcon, QMenu,
            QSizePolicy,
        )
        from PyQt6.QtCore import Qt, QTimer, QRect
        from PyQt6.QtGui import QIcon, QAction, QPixmap, QImage

        app = QApplication.instance()
        self._QApplication = QApplication
        self._QMainWindow = QMainWindow
        self._QTimer = QTimer
        self._QMessageBox = QMessageBox
        self._Qt = Qt
        self._QIcon = QIcon
        self._QAction = QAction

        W, H = 580, 580
        self.win = QMainWindow()
        self.win.setWindowTitle(APP_NAME)
        self.win.setFixedSize(W, H)
        self.win.setStyleSheet("""
            /* === 全局 === */
            QMainWindow { background-color: #090e1a; }
            QWidget { font-family: -apple-system, "Segoe UI", "Helvetica Neue", sans-serif; font-size: 12px; }
            QLabel { color: #cbd5e1; }

            /* === 滚动条 === */
            QScrollBar:vertical { background: transparent; width: 5px; margin: 0; }
            QScrollBar::handle:vertical { background: #334155; border-radius: 3px; min-height: 24px; }
            QScrollBar::handle:vertical:hover { background: #475569; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

            /* === Tab 组件 === */
            QTabWidget::pane { border: none; background: transparent; top: -1px; }
            QTabBar::tab {
                background: transparent; color: #64748b; padding: 9px 22px;
                border: none; border-bottom: 2px solid transparent;
                font-weight: 500; margin-right: 0;
            }
            QTabBar::tab:selected { color: #a5b4fc; border-bottom: 2px solid #818cf8; font-weight: 600; }
            QTabBar::tab:hover:!selected { color: #94a3b8; }

            /* === 按钮 - 主色调 === */
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #6366f1,stop:1 #4f46e5);
                color: white; border: none; border-radius: 10px;
                padding: 9px 22px; font-weight: 600; font-size: 11px;
            }
            QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #818cf8,stop:1 #6366f1); }
            QPushButton:pressed { background: #4338ca; }

            /* === 列表控件 === */
            QListWidget {
                background: #0c1421; color: #94a3b8; border: 1px solid #1e293b;
                border-radius: 10px; padding: 6px; outline: none;
                font-family: "SF Mono", Menlo, Monaco, Consolas, monospace; font-size: 10px;
            }
            QListWidget::item { padding: 5px 10px; border-radius: 6px; }
            QListWidget::item:selected { background: rgba(99,102,241,64); color: #e2e8f0; }
            QListWidget::item:hover { background: rgba(30,41,59,153); }

            /* === 数字输入框 === */
            QSpinBox {
                background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
                border-radius: 10px; padding: 7px 14px;
                font-family: "SF Mono", Menlo, Monaco, Consolas, monospace; font-size: 12px;
            }
            QSpinBox:hover { border-color: #6366f1; }
            QSpinBox:focus { border-color: #818cf8; }
            QSpinBox::up-button, QSpinBox::down-button {
                background: #334155; border-radius: 4px; margin: 2px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #475569; }

            /* === 复选框 === */
            QCheckBox { color: #cbd5e1; font-size: 11px; spacing: 8px; }
            QCheckBox::indicator {
                width: 16px; height: 16px; border: 2px solid #475569; border-radius: 4px;
                background: transparent;
            }
            QCheckBox::indicator:checked {
                background: #6366f1; border-color: #6366f1;
            }

            /* === 上下文菜单 === */
            QMenu { background-color: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 24px; border-radius: 6px; }
            QMenu::item:selected { background-color: #334155; }
            QMenu::separator { height: 1px; background: #334155; margin: 4px 8px; }

            /* === 提示框 === */
            QToolTip { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 6px; padding: 4px 8px; }
        """)

        # 图标
        self._qicon = self._make_qicon()
        if self._qicon:
            self.win.setWindowIcon(self._qicon)

        central = QWidget()
        self.win.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 顶部标题栏 ----
        header = QFrame()
        header.setFixedHeight(56)
        header.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0f172a, stop:0.5 #1a2440, stop:1 #0f172a);
                border-bottom: 1px solid #1e293b;
            }
        """)
        hdr_layout = QHBoxLayout(header)
        hdr_layout.setContentsMargins(20, 0, 20, 0)
        hdr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Logo 图标标签
        logo_lbl = QLabel("🔑")
        logo_lbl.setStyleSheet("font-size: 18px; border: none;")
        hdr_layout.addWidget(logo_lbl)
        hdr_layout.addSpacing(8)

        title_lbl = QLabel("SSH Key Manager")
        title_lbl.setStyleSheet("color: #e2e8f0; font-size: 16px; font-weight: 700; border: none;")
        hdr_layout.addWidget(title_lbl)
        hdr_layout.addStretch()

        # 版本号标签（从 VERSION 文件读取）
        ver_str = "v1.0"
        try:
            ver_file = ROOT_DIR / "VERSION"
            if ver_file.exists():
                ver_str = "v" + ver_file.read_text().strip()
        except Exception:
            pass
        self._current_version = ver_str.lstrip("v")
        ver_lbl = QLabel(ver_str)
        ver_lbl.setStyleSheet("color: #475569; font-size: 10px; border: none;")
        hdr_layout.addWidget(ver_lbl)
        root.addWidget(header)

        # ---- Tab 分页 ----
        self.tabs = QTabWidget()
        self.tabs.setFixedHeight(420)
        root.addWidget(self.tabs)

        self._build_tab_status()
        self._build_tab_processes()
        self._build_tab_sftp()

        # ---- 底部按钮栏 ----
        bottom = QHBoxLayout()
        bottom.setContentsMargins(16, 8, 16, 16)
        bottom.setSpacing(10)

        btn_open = QPushButton("🌐  打开 Web 界面")
        btn_open.clicked.connect(self._open_web)
        bottom.addWidget(btn_open)

        btn_quit = QPushButton("退出程序")
        btn_quit.setObjectName("btnQuit")
        btn_quit.setStyleSheet("""
            QPushButton#btnQuit {
                background: transparent; color: #64748b; border: 1px solid #334155;
                border-radius: 10px; padding: 9px 22px; font-weight: 500;
            }
            QPushButton#btnQuit:hover { background: rgba(239,68,68,38); color: #f87171; border-color: #ef4444; }
        """)
        btn_quit.clicked.connect(self._quit)
        bottom.addWidget(btn_quit)
        root.addLayout(bottom)

        # 居中显示
        screen = app.primaryScreen().geometry()
        x = (screen.width() - W) // 2
        y = (screen.height() - H) // 2 - 40
        self.win.move(x, y)

        # 关闭 → 最小化到托盘
        self.win.closeEvent = lambda e: (e.ignore(), self.win.hide())

        # ---- 系统托盘 ----
        self._setup_tray()

        # ---- 轮询定时器 ----
        self._sse_timer = QTimer()
        self._sse_timer.timeout.connect(self._poll_sse)
        self._sse_timer.start(2000)

        self._proc_timer = QTimer()
        self._proc_timer.timeout.connect(self._poll_processes)
        self._proc_timer.start(5000)

        # 首次默认刷新进程列表
        self._refresh_processes()

        # 首次默认刷新 SSE / 会话状态
        self._poll_sse()

        # 启动后延迟检查更新
        self._QTimer.singleShot(3000, self._check_update)

    def _make_qicon(self):
        """生成 QIcon：优先从 .ico/.icns 直接加载，失败则 PIL 生成"""
        # 1. 尝试从文件直接加载（Windows .ico / macOS .icns）
        try:
            from PyQt6.QtGui import QIcon as _QIcon
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                base = Path(sys._MEIPASS)
            else:
                base = ROOT_DIR
            for ext in ("ico", "icns", "png"):
                path = base / "asset" / f"icon.{ext}"
                if path.exists():
                    icon = _QIcon(str(path))
                    if not icon.isNull():
                        return icon
        except Exception:
            pass
        # 2. 回退：PIL 生成
        try:
            from PIL.ImageQt import ImageQt
            img = _create_icon_image()
            if img is None:
                return None
            qimg = ImageQt(img)
            return QIcon(QPixmap.fromImage(qimg))
        except Exception:
            return None

    def _setup_tray(self):
        """QSystemTrayIcon 系统托盘"""
        from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QAction
        if not self._qicon:
            return
        tray = QSystemTrayIcon(self._qicon, self.win)
        tray.setToolTip(APP_NAME)

        menu = QMenu()
        act_open = QAction("打开主面板", menu)
        act_open.triggered.connect(lambda: (self.win.show(), self.win.raise_()))
        menu.addAction(act_open)

        act_web = QAction("打开 Web 界面", menu)
        act_web.triggered.connect(self._open_web)
        menu.addAction(act_web)
        menu.addSeparator()

        if sys.platform.startswith("win"):
            self._tray_autostart = QAction("开机自启", menu)
            self._tray_autostart.setCheckable(True)
            self._tray_autostart.setChecked(_is_autostart_enabled())
            self._tray_autostart.triggered.connect(self._toggle_autostart_tray)
            menu.addAction(self._tray_autostart)
            menu.addSeparator()

        act_quit = QAction("退出程序", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: (
            self.win.show(), self.win.raise_()
        ) if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        tray.show()
        self._tray = tray

    # ======================== 页1：服务状态 ========================

    def _build_tab_status(self):
        from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QFrame
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QCursor

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # ---- 服务地址卡片 ----
        addr_card = QFrame()
        addr_card.setStyleSheet("""
            QFrame {
                background: #0f172a; border: 1px solid #1e293b;
                border-radius: 12px;
            }
        """)
        addr_layout = QVBoxLayout(addr_card)
        addr_layout.setContentsMargins(14, 10, 14, 10)
        addr_layout.setSpacing(6)

        addr_header = QHBoxLayout()
        status_dot = QLabel("●")
        status_dot.setStyleSheet("color: #10b981; font-size: 10px; border: none;")
        addr_header.addWidget(status_dot)
        addr_header.addWidget(QLabel("服务运行中"))
        addr_header.addStretch()
        addr_layout.addLayout(addr_header)

        url_row = QHBoxLayout()
        url_lbl = QLabel(APP_URL)
        url_lbl.setStyleSheet("""
            color: #818cf8; font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
            font-size: 13px; font-weight: 600; text-decoration: underline; border: none;
        """)
        url_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        def _on_url_click(event):
            webbrowser.open(APP_URL)
        url_lbl.mousePressEvent = _on_url_click
        url_row.addWidget(url_lbl)
        url_row.addStretch()
        addr_layout.addLayout(url_row)
        layout.addWidget(addr_card)

        # ---- 统计卡片行 ----
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)

        def make_card(title, color, icon):
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: #0f172a; border: 1px solid #1e293b;
                    border-radius: 14px;
                }}
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 12, 16, 12)
            card_layout.setSpacing(6)

            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet(f"color: {color}; font-size: 20px; border: none;")
            card_layout.addWidget(icon_lbl)

            num_lbl = QLabel("—")
            num_lbl.setStyleSheet(f"""
                color: {color}; font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
                font-size: 28px; font-weight: 700; border: none;
            """)
            card_layout.addWidget(num_lbl)

            title_lbl = QLabel(title)
            title_lbl.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
            card_layout.addWidget(title_lbl)
            return card, num_lbl

        sse_card, self.lbl_sse_num = make_card("SSE 活跃连接", "#38bdf8", "📡")
        ssh_card, self.lbl_session_num = make_card("WebSSH 活跃会话", "#a78bfa", "💻")
        stats_row.addWidget(sse_card)
        stats_row.addWidget(ssh_card)
        layout.addLayout(stats_row)

        # ---- 清理按钮 ----
        btn_cleanup = QPushButton("🧹  清理僵死 SSE 连接")
        btn_cleanup.setObjectName("btnGhost")
        btn_cleanup.setStyleSheet("""
            QPushButton#btnGhost {
                background: transparent; color: #94a3b8; border: 1px solid #334155;
                border-radius: 10px; padding: 9px 16px; font-weight: 500;
            }
            QPushButton#btnGhost:hover { background: rgba(56,189,248,26); color: #38bdf8; border-color: #38bdf8; }
        """)
        btn_cleanup.clicked.connect(self._cleanup_sse)
        layout.addWidget(btn_cleanup)

        self.lbl_sse_status = QLabel("")
        self.lbl_sse_status.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
        layout.addWidget(self.lbl_sse_status)

        # ---- 检查更新 ----
        update_row = QHBoxLayout()
        update_row.setSpacing(8)

        from PyQt6.QtWidgets import QComboBox
        self.cmb_branch = QComboBox()
        self.cmb_branch.addItems(["qt-gui", "main"])
        self.cmb_branch.setCurrentText("qt-gui")
        self.cmb_branch.setStyleSheet("""
            QComboBox {
                background: #1e293b; color: #cbd5e1; border: 1px solid #334155;
                border-radius: 8px; padding: 6px 10px; font-size: 10px;
            }
            QComboBox:hover { border-color: #6366f1; }
            QComboBox::drop-down { border: none; padding-right: 4px; }
            QComboBox QAbstractItemView {
                background: #1e293b; color: #cbd5e1;
                border: 1px solid #334155; border-radius: 6px;
                selection-background-color: #334155;
            }
        """)
        update_row.addWidget(self.cmb_branch)

        btn_check_update = QPushButton("🔍  检查更新")
        btn_check_update.setObjectName("btnGhost")
        btn_check_update.setStyleSheet("""
            QPushButton#btnGhost {
                background: transparent; color: #94a3b8; border: 1px solid #334155;
                border-radius: 10px; padding: 9px 16px; font-weight: 500;
            }
            QPushButton#btnGhost:hover { background: rgba(16,185,129,0.1); color: #10b981; border-color: #10b981; }
        """)
        btn_check_update.clicked.connect(self._check_update)
        update_row.addWidget(btn_check_update)
        self.lbl_update_status = QLabel("")
        self.lbl_update_status.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
        self.lbl_update_status.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        update_row.addWidget(self.lbl_update_status, 1)
        layout.addLayout(update_row)

        # 开机自启（Windows only）
        if sys.platform.startswith("win"):
            self.cb_autostart = QCheckBox("开机自动启动")
            self.cb_autostart.setChecked(_is_autostart_enabled())
            self.cb_autostart.stateChanged.connect(self._toggle_autostart)
            layout.addWidget(self.cb_autostart)

        layout.addStretch()
        self.tabs.addTab(tab, "📊  服务状态")

    # ======================== 页2：进程管理 ========================

    def _build_tab_processes(self):
        from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("🐍  Python 进程管理"))
        header_row.addStretch()
        layout.addLayout(header_row)

        self.proc_listbox = QListWidget()
        layout.addWidget(self.proc_listbox)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_refresh = QPushButton("🔄  刷新列表")
        btn_refresh.clicked.connect(self._refresh_processes)
        btn_row.addWidget(btn_refresh)

        btn_kill = QPushButton("⛔  结束选中进程")
        btn_kill.setObjectName("btnDanger")
        btn_kill.setStyleSheet("""
            QPushButton#btnDanger {
                background: transparent; color: #f87171; border: 1px solid #7f1d1d;
                border-radius: 10px; padding: 9px 16px; font-weight: 500;
            }
            QPushButton#btnDanger:hover { background: rgba(239,68,68,51); color: #fca5a5; }
        """)
        btn_kill.clicked.connect(self._kill_selected_processes)
        btn_row.addWidget(btn_kill)
        layout.addLayout(btn_row)

        self.lbl_proc_status = QLabel("")
        self.lbl_proc_status.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
        layout.addWidget(self.lbl_proc_status)

        self.tabs.addTab(tab, "📋  进程管理")

    # ======================== 页3：SFTP 设置 ========================

    def _build_tab_sftp(self):
        from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QFrame
        import modules.config as _cfg

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # ---- 下载限制卡片 ----
        limit_card = QFrame()
        limit_card.setStyleSheet("""
            QFrame {
                background: #0f172a; border: 1px solid #1e293b;
                border-radius: 12px;
            }
        """)
        limit_layout = QVBoxLayout(limit_card)
        limit_layout.setContentsMargins(14, 14, 14, 14)
        limit_layout.setSpacing(12)

        desc_lbl = QLabel("通过 WebSSH 下载文件时，单文件大小上限")
        desc_lbl.setStyleSheet("color: #cbd5e1; font-size: 12px; border: none;")
        limit_layout.addWidget(desc_lbl)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(QLabel("大小上限"))
        _cfg.load_config()
        current_mb = _cfg.get("sftp_max_download_mb", 100)
        self.spin_sftp = QSpinBox()
        self.spin_sftp.setRange(0, 9999)
        self.spin_sftp.setValue(current_mb)
        self.spin_sftp.setSuffix(" MB")
        self.spin_sftp.setSingleStep(50)
        self.spin_sftp.setFixedWidth(140)
        row.addWidget(self.spin_sftp)
        row.addStretch()
        limit_layout.addLayout(row)

        layout.addWidget(limit_card)

        # ---- 保存按钮 ----
        btn_save = QPushButton("💾  保存设置")
        btn_save.clicked.connect(self._save_sftp_limit)
        layout.addWidget(btn_save)

        self.lbl_sftp_status = QLabel("")
        self.lbl_sftp_status.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
        layout.addWidget(self.lbl_sftp_status)

        # ---- 提示 ----
        tip_card = QFrame()
        tip_card.setStyleSheet("""
            QFrame {
                background: rgba(245,158,11,20); border: 1px solid rgba(245,158,11,51);
                border-radius: 10px;
            }
        """)
        tip_layout = QHBoxLayout(tip_card)
        tip_layout.setContentsMargins(10, 8, 10, 8)
        tip_layout.setSpacing(8)
        tip_icon = QLabel("💡")
        tip_icon.setStyleSheet("font-size: 14px; border: none;")
        tip_layout.addWidget(tip_icon)
        note = QLabel("修改后即时生效，已进行中的下载不受影响。\n设置为 0 表示不限制（谨慎使用）。")
        note.setStyleSheet("color: #94a3b8; font-size: 10px; border: none;")
        tip_layout.addWidget(note)
        layout.addWidget(tip_card)

        layout.addStretch()

        self.tabs.addTab(tab, "⚙️  SFTP 设置")

    # ======================== 窗口行为 ========================

    def _open_web(self):
        webbrowser.open(APP_URL)

    def _toggle_autostart(self, state):
        if state:
            _enable_autostart()
        else:
            _disable_autostart()

    def _toggle_autostart_tray(self, checked):
        if checked:
            _enable_autostart()
        else:
            _disable_autostart()
        if hasattr(self, 'cb_autostart'):
            self.cb_autostart.setChecked(_is_autostart_enabled())

    def _quit(self):
        _cleanup_and_exit()
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    def show(self):
        self.win.show()
        self.win.raise_()

    def run(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        self.win.show()
        app.exec()

    # ======================== SSE 状态轮询 ========================

    def _poll_sse(self):
        try:
            count = len(_sse_queues)
            self.lbl_sse_num.setText(str(count))
        except Exception as e:
            logger.warning(f"SSE 轮询失败: {e}")
            self.lbl_sse_num.setText("—")
        try:
            from modules.webssh import _ssh_sessions
            count = len(_ssh_sessions)
            self.lbl_session_num.setText(str(count))
        except Exception as e:
            logger.warning(f"SSH 会话轮询失败: {e}")
            self.lbl_session_num.setText("—")

    def _cleanup_sse(self):
        from PyQt6.QtWidgets import QMessageBox
        try:
            from modules.common import _sse_cleanup_stale, get_sse_queue_count
            before = get_sse_queue_count()
            removed = _sse_cleanup_stale()
            after = get_sse_queue_count()
            self.lbl_sse_num.setText(str(after))
            msg = f"已清理 {removed} 个僵死连接" if removed > 0 else "没有需要清理的连接"
            self.lbl_sse_status.setText(msg)
            self._QTimer.singleShot(2000, lambda: self.lbl_sse_status.setText(""))
        except Exception as e:
            QMessageBox.critical(self.win, "清理失败", str(e))

    def _check_update(self):
        """检查 GitHub 最新版本（缓存 5 分钟，后台线程不卡 UI）"""
        branch = self.cmb_branch.currentText()
        now = time.time()
        cache_key = f"{branch}_{self._current_version}"
        # 防止重复请求
        if getattr(self, '_update_checking', False):
            return
        # 5 分钟内已有结果，直接复用缓存
        if hasattr(self, '_update_cache_key') and self._update_cache_key == cache_key:
            if hasattr(self, '_update_cache_time') and (now - self._update_cache_time) < 300:
                self._apply_update_result(self._update_cache_result)
                return
        self._update_checking = True
        self._update_cache_key = cache_key
        self.lbl_update_status.setText(f"检查 {branch} 分支…")
        self.lbl_update_status.setStyleSheet("color: #94a3b8; font-size: 10px; border: none;")
        threading.Thread(target=lambda: self._do_check_update(branch), daemon=True).start()

    def _do_check_update(self, branch: str):
        """后台线程：请求 GitHub API 按分支过滤最新 Release"""
        import requests, json

        API_RELEASES = "https://api.github.com/repos/tangjin2580/sshkeys/releases?per_page=30"
        RELEASES_URL = "https://github.com/tangjin2580/sshkeys/releases"

        def _parse_version(v: str) -> tuple:
            v = v.lstrip("v").strip()
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)

        try:
            logger.info(f"[更新检查] 查询 {branch} 分支 Release…")
            resp = requests.get(API_RELEASES, headers={
                "User-Agent": "SSH-Key-Manager",
                "Accept": "application/vnd.github+json",
            }, timeout=(3, 10))
            resp.raise_for_status()
            releases = resp.json()
            logger.info(f"[更新检查] 共获取 {len(releases)} 个 Release")

            # 按 target_commitish 过滤该分支的 Release
            branch_releases = [r for r in releases if r.get("target_commitish") == branch]
            logger.info(f"[更新检查] 匹配 {branch} 分支: {len(branch_releases)} 个")
            if not branch_releases:
                logger.warning(f"[更新检查] {branch} 无匹配 Release，回退 latest")
                latest_api = "https://api.github.com/repos/tangjin2580/sshkeys/releases/latest"
                resp2 = requests.get(latest_api, headers={
                    "User-Agent": "SSH-Key-Manager",
                    "Accept": "application/vnd.github+json",
                }, timeout=(3, 10))
                resp2.raise_for_status()
                latest_release = resp2.json()
                branch_releases = [latest_release]

            # 取版本号最大的
            latest = max(branch_releases, key=lambda r: _parse_version(r.get("tag_name", "")))
            latest_tag = latest.get("tag_name", "")
            latest_ver = _parse_version(latest_tag)
            current_ver = _parse_version(self._current_version)
            html_url = latest.get("html_url", RELEASES_URL)
            logger.info(f"[更新检查] 本地={self._current_version}({current_ver})  {branch}最新={latest_tag}({latest_ver})  target={latest.get('target_commitish','?')}")

            if latest_ver > current_ver:
                result = ("new", f"🆕 {branch} 有新版本 {latest_tag} → 点击下载", "#10b981", html_url)
                logger.info(f"[更新检查] 发现新版本: {latest_tag}")
            elif latest_ver == current_ver:
                result = ("current", f"✅ {branch} 已是最新 {latest_tag}", "#64748b", None)
            else:
                logger.info(f"[更新检查] 本地版本比 {branch} Release 还新")
                result = ("newer", f"📌 本地 {self._current_version}（{branch} Release 最新 {latest_tag}）", "#f59e0b", None)
        except Exception as e:
            logger.warning(f"[更新检查] 失败: {e}")
            result = ("error", f"❌ 检查失败: {e}", "#f87171", None)

        self._update_cache_time = time.time()
        self._update_cache_result = result
        self._QTimer.singleShot(0, lambda: self._apply_update_result(result))

    def _apply_update_result(self, result):
        """主线程：将检查结果应用到 UI"""
        self._update_checking = False
        _, msg, color, url = result
        self.lbl_update_status.setStyleSheet(
            f"color: {color}; font-size: 10px; border: none;" +
            (" text-decoration: underline;" if url else ""))
        self.lbl_update_status.setText(msg)
        if url:
            def _open_update_url(event):
                webbrowser.open(url)
            self.lbl_update_status.mousePressEvent = _open_update_url
        else:
            self.lbl_update_status.mousePressEvent = None

    # ======================== 进程管理 ========================

    def _poll_processes(self):
        if self.tabs.currentIndex() == 1:
            self._refresh_processes()

    def _refresh_processes(self):
        """异步刷新进程列表（后台线程，不卡 UI）"""
        if getattr(self, '_proc_loading', False):
            return  # 已有刷新在进行中
        self._proc_loading = True
        self.proc_listbox.clear()
        self.proc_listbox.addItem("加载中…")
        self.lbl_proc_status.setText("正在获取进程列表…")
        threading.Thread(target=self._do_refresh_processes, daemon=True).start()

    def _do_refresh_processes(self):
        """后台线程：获取进程列表"""
        err_msg = ""
        proc_pids = None
        try:
            proc_pids = self._list_python_processes()
            logger.info(f"[进程刷新] 找到 {len(proc_pids) if proc_pids else 0} 个进程")
        except Exception as e:
            err_msg = str(e)
            logger.warning(f"[进程刷新] 失败: {e}")
        self._QTimer.singleShot(0, lambda: self._apply_process_result(proc_pids, err_msg))

    def _apply_process_result(self, proc_pids, err_msg=""):
        """主线程：将进程列表应用到 UI"""
        self._proc_loading = False
        self.proc_listbox.clear()
        self._proc_pids = []
        if proc_pids is None:
            self.proc_listbox.addItem(f"刷新失败: {err_msg}")
            self.lbl_proc_status.setText(f"错误: {err_msg}")
            return
        try:
            self._proc_pids = proc_pids
            if not self._proc_pids:
                self.proc_listbox.addItem("未找到 Python 进程")
                self.lbl_proc_status.setText("未找到 Python 进程")
            else:
                current_pid = os.getpid()
                self._current_proc_index = None
                for i, info in enumerate(self._proc_pids):
                    is_current = info["pid"] == current_pid
                    marker = "⭐ 本进程 " if is_current else "   "
                    self.proc_listbox.addItem(
                        f"{marker}PID {info['pid']:>6}  {info['mem']:>8} KB  {info['cmd'][:60]}")
                    if is_current:
                        self._current_proc_index = i
                status_parts = [f"共 {len(self._proc_pids)} 个进程"]
                if self._current_proc_index is not None:
                    status_parts.append(f"⭐ = 当前进程 (PID {current_pid})")
                self.lbl_proc_status.setText("  |  ".join(status_parts))
        except Exception as e:
            self.proc_listbox.addItem(f"刷新失败: {e}")
            self.lbl_proc_status.setText(f"错误: {e}")

    def _list_python_processes(self):
        import sys as _sys, subprocess, csv, io
        is_win = _sys.platform.startswith("win")
        current_pid = os.getpid()
        py_basename = os.path.basename(_sys.executable).lower()
        results = []
        try:
            if is_win:
                out = subprocess.check_output(
                    "tasklist /fo csv /nh", shell=True, timeout=10
                ).decode("gbk", errors="replace")
                reader = csv.reader(io.StringIO(out))
                for row in reader:
                    if len(row) < 5:
                        continue
                    name = row[0].lower()
                    if "python" not in name and "sshkeys" not in name and py_basename not in name:
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
                    ["ps", "aux"], timeout=10
                ).decode("utf-8", errors="replace")
                header_skipped = False
                for raw_line in out.strip().splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if not header_skipped:
                        header_skipped = True
                        continue
                    parts = line.split(None, 10)
                    if len(parts) < 11:
                        continue
                    try:
                        pid = int(parts[1])
                        rss_kb = int(parts[5])
                    except (ValueError, IndexError):
                        continue
                    full_cmd = parts[10] if len(parts) > 10 else ""
                    combined = full_cmd.lower()
                    # 匹配 python / 当前可执行文件 / sshkeys / main.py / ssh_key_manager
                    if not any(kw in combined for kw in ["python", py_basename, "sshkeys", "main.py", "ssh_key_manager"]):
                        continue
                    display_cmd = full_cmd[:100] if len(full_cmd) > 100 else full_cmd
                    results.append({"pid": pid, "cmd": display_cmd, "mem": rss_kb})
        except Exception as e:
            logger.warning(f"[进程列表] 获取失败: {e}")
        # 兜底：空结果时用当前 PID 反查，确保至少能看到自身进程
        if not results:
            logger.info(f"[进程列表] 过滤结果为空，用 ps -p {current_pid} 兜底反查")
            try:
                if not is_win:
                    out2 = subprocess.check_output(
                        ["ps", "-p", str(current_pid), "-o", "pid=,rss=,args="], timeout=5
                    ).decode("utf-8", errors="replace")
                    for line in out2.strip().splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        p = line.split(None, 2)
                        if len(p) >= 2:
                            try:
                                pid2 = int(p[0])
                                rss2 = int(p[1])
                            except ValueError:
                                continue
                            cmd2 = p[2] if len(p) > 2 else sys.executable
                            results.append({"pid": pid2, "cmd": cmd2[:100], "mem": rss2})
            except Exception as e:
                logger.warning(f"[进程列表] 兜底查询失败: {e}")
        results.sort(key=lambda x: x["pid"])
        # PID 去重
        seen_pids = set()
        deduped = []
        for r in results:
            if r["pid"] not in seen_pids:
                seen_pids.add(r["pid"])
                deduped.append(r)
        return deduped

    def _kill_selected_processes(self):
        from PyQt6.QtWidgets import QMessageBox
        import subprocess
        items = self.proc_listbox.selectedItems()
        if not items:
            QMessageBox.warning(self.win, "提示", "请先选中要结束的进程")
            return
        if not self._proc_pids:
            QMessageBox.warning(self.win, "提示", "进程列表为空，请先刷新")
            return
        sel_rows = [self.proc_listbox.row(it) for it in items]
        # 确保索引有效
        sel_rows = [r for r in sel_rows if 0 <= r < len(self._proc_pids)]
        if not sel_rows:
            return
        pids = [self._proc_pids[i]["pid"] for i in sel_rows]
        current_pid = os.getpid()
        if current_pid in pids:
            QMessageBox.warning(
                self.win, "无法操作",
                f"不能结束当前进程 (PID {current_pid})，这是本程序自身。\n"
                "如需退出程序，请使用底部「退出程序」按钮。"
            )
            return
        pid_str = ", ".join(str(p) for p in pids)
        reply = QMessageBox.question(
            self.win, "确认",
            f"确定要结束以下进程吗？\n\nPID: {pid_str}\n\n此操作不可撤销！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        killed = 0
        for pid in pids:
            try:
                if sys.platform.startswith("win"):
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=5)
                else:
                    # SIGKILL 强制终止，比 SIGTERM 更可靠
                    os.kill(pid, signal.SIGKILL)
                killed += 1
            except ProcessLookupError:
                logger.info(f"PID {pid} 已不存在")
                killed += 1
            except PermissionError:
                logger.warning(f"PID {pid} 权限不足，无法结束")
            except Exception as e:
                logger.warning(f"无法结束 PID {pid}: {e}")
        if killed:
            self.lbl_proc_status.setText(f"已结束 {killed} 个进程，正在刷新…")
        self._QTimer.singleShot(800, self._refresh_processes)

    # ======================== SFTP 设置 ========================

    def _save_sftp_limit(self):
        import modules.config as _cfg
        from PyQt6.QtWidgets import QMessageBox
        try:
            val = self.spin_sftp.value()
            _cfg.set("sftp_max_download_mb", val)
            self.lbl_sftp_status.setText(f"已保存：{val} MB")
            self._QTimer.singleShot(2000, lambda: self.lbl_sftp_status.setText(""))
        except Exception as e:
            QMessageBox.critical(self.win, "保存失败", str(e))


# ==================== 主入口 ====================

def main():
    global _main_window
    global HOST, PORT, APP_URL

    parser = argparse.ArgumentParser(description="SSH Key Manager")
    parser.add_argument("--dev", action="store_true",
                        help="开发模式：启用代码热重载（不启动 GUI）")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式：仅启动 Web 服务，不启动 GUI/托盘（适合服务器）")
    parser.add_argument("--port", type=int, default=PORT,
                        help=f"服务端口（默认 {PORT}）")
    parser.add_argument("--host", default=HOST,
                        help=f"绑定地址（默认 {HOST}，局域网可改为 0.0.0.0）")
    args = parser.parse_args()

    HOST = args.host
    PORT = args.port
    APP_URL = f"http://{HOST}:{PORT}"

    logger.info("=" * 50)
    logger.info("  SSH Key Manager")
    logger.info("=" * 50)
    logger.info(f"  服务地址: {APP_URL}")
    if args.headless:
        run_mode = "无头模式（仅 Web 服务）"
    elif args.dev:
        run_mode = "开发（热重载）"
    else:
        run_mode = "生产（PyQt6 GUI + 托盘）"
    logger.info(f"  运行模式: {run_mode}")
    logger.info("=" * 50)

    app = create_app()

    if args.dev:
        logger.info("开发模式：Flask 开发服务器")
        threading.Timer(1.5, lambda: webbrowser.open(APP_URL)).start()
        app.run(host=HOST, port=PORT, debug=True, use_reloader=True)
        return

    if args.headless:
        logger.info("无头模式：仅启动 Web 服务器")
        logger.info(f"访问: {APP_URL}")
        logger.info("按 Ctrl+C 停止服务")
        try:
            from waitress import serve
            serve(app, host=HOST, port=PORT, threads=8)
        except (KeyboardInterrupt, SystemExit):
            logger.info("服务已停止")
        return

    # ==================== 生产模式（PyQt6） ====================

    # 1. 启动 Waitress 服务器（后台线程）
    server_thread = threading.Thread(target=_serve_with_restart, args=(app,), daemon=True)
    server_thread.start()

    # 2. 等待端口就绪
    if _wait_for_port():
        logger.info("服务器已就绪")
    else:
        logger.warning("服务启动超时")

    # 3. 创建 QApplication（必须在主线程）
    from PyQt6.QtWidgets import QApplication
    qt_app = QApplication(sys.argv)

    # 4. 启动 GUI 主面板（含系统托盘）
    _main_window = MainPanel()
    try:
        _main_window.run()
    except (KeyboardInterrupt, SystemExit):
        pass

    _cleanup_and_exit()


if __name__ == "__main__":
    try:
        main()
    except Exception as _exc:
        import traceback
        _log_path = os.path.join(os.path.expanduser("~"), ".ssh_key_manager_crash.log")
        try:
            with open(_log_path, "w", encoding="utf-8") as _f:
                _f.write(f"SSH Key Manager crash at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                _f.write(f"Platform: {sys.platform}\n")
                _f.write(f"Frozen: {getattr(sys, 'frozen', False)}\n")
                _f.write(f"Python: {sys.version}\n")
                _f.write("=" * 60 + "\n")
                traceback.print_exc(file=_f)
        except Exception:
            pass
        raise
