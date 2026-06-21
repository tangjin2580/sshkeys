"""连接管理路由 — 列表、保存、删除、本地终端启动"""
from flask import Blueprint, request, jsonify
from pathlib import Path
import logging
from datetime import datetime
import os
import sys
import shutil
import subprocess
import modules.common as _common
from modules.connections_store import (
    load_all as load_connections,
    add_connection,
    delete_connection,
    batch_sync_from_config,
)
from modules.ssh_config import parse_ssh_config

logger = logging.getLogger(__name__)
connections_bp = Blueprint("connections", __name__)

@connections_bp.route("/api/connections", methods=["GET"])
def list_connections():
    """获取所有连接 — 自动从 ~/.ssh/config 同步"""
    try:
        # 1. 解析 SSH config，批量同步到 connections.json（1 次读 + 1 次写）
        config_entries = parse_ssh_config()
        conns = batch_sync_from_config(config_entries)

        # 2. 附加密钥有效性检查
        for c in conns:
            idf = c.get("identity_file", "")
            if idf:
                # 展开 ~ 为 home 目录
                resolved = Path(idf).expanduser()
                c["key_valid"] = resolved.exists()
            else:
                c["key_valid"] = True  # 无显式密钥，依赖 ssh agent 或默认路径
        return jsonify({"success": True, "connections": conns})
    except Exception as e:
        logger.exception("读取连接列表失败")
        return jsonify({"success": False, "error": str(e)}), 500

@connections_bp.route("/api/connections", methods=["POST"])
def save_connection():
    """手动保存一条连接"""
    data = request.get_json() or {}
    alias = data.get("alias", "").strip()
    hostname = data.get("hostname", "").strip()
    user = data.get("user", "").strip()
    port = data.get("port", 22)
    identity_file = data.get("identity_file", "").strip()

    if not alias or not hostname or not user:
        return jsonify({"success": False, "error": "请填写 alias、hostname、user"}), 400

    result = add_connection(
        alias=alias,
        hostname=hostname,
        user=user,
        identity_file=identity_file,
        port=port,
    )
    return jsonify(result)

@connections_bp.route("/api/connections/<conn_id>", methods=["DELETE"])
def delete_connection_route(conn_id):
    """删除一条连接"""
    result = delete_connection(conn_id)
    return jsonify(result)

@connections_bp.route("/api/connections/connect", methods=["POST"])
def connect_to_server():
    """
    一键打开终端 SSH 连接到服务器
    平台适配: macOS → Terminal.app/iTerm2 | Windows → wt/cmd | Linux → gnome-terminal/konsole/xterm
    """
    data = request.get_json() or {}
    alias = data.get("alias", "").strip()
    terminal_path = data.get("terminal_path", "").strip()

    if not alias:
        return _common.error_response(
            "请指定连接别名",
            code="INVALID_PARAM",
            suggestion="请在「我的连接」中选择一个连接，或先添加一个新连接"
        )
    
    # 校验 alias 是否在连接列表中（可选，用于更友好的错误提示）
    try:
        connections = load_connections()
        matched = [c for c in connections if c.get("alias") == alias]
        if not matched:
            logger.warning(f"连接别名 '{alias}' 不在保存的连接列表中，但仍将尝试连接（可能是 Raw Host）")
    except Exception:
        pass

    _common._sse_broadcast("progress", {"message": f"正在启动 SSH 连接到 {alias} ...", "time": datetime.now().strftime("%H:%M:%S")})
    
    try:
        if sys.platform == "win32":
            # Windows 终端启动逻辑
            def exists(path):
                if not path:
                    return False
                r = subprocess.run(["where", path], capture_output=True, text=True)
                return r.returncode == 0
            
            success = False
            
            # 1. 尝试用户指定的终端
            if terminal_path and exists(terminal_path):
                try:
                    subprocess.Popen([terminal_path, "ssh", alias], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    success = True
                    logger.info(f"使用指定终端启动: {terminal_path}")
                except Exception as e:
                    logger.warning(f"指定终端启动失败: {e}")
                    success = False
            
            # 2. 回退到 wt.exe
            if not success and exists("wt.exe"):
                try:
                    subprocess.Popen(["wt.exe", "ssh", alias], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    success = True
                    logger.info("使用 Windows Terminal 启动")
                except Exception as e:
                    logger.warning(f"Windows Terminal 启动失败: {e}")
            
            # 3. 回退到 powershell
            if not success and exists("powershell.exe"):
                try:
                    subprocess.Popen(["powershell.exe", "-NoExit", "-Command", f"ssh {alias}"], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    success = True
                    logger.info("使用 PowerShell 启动")
                except Exception as e:
                    logger.warning(f"PowerShell 启动失败: {e}")
            
            # 4. 最后回退到 cmd
            if not success:
                try:
                    subprocess.Popen(["cmd.exe", "/c", "start", "cmd", "/k", f"ssh {alias}"], shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    success = True
                    logger.info("使用 CMD 启动")
                except Exception as e:
                    logger.error(f"CMD 启动失败: {e}")
                    return jsonify({"success": False, "error": f"无法启动终端: {str(e)}"}), 500
            
            if not success:
                return _common.error_response(
                    "无法找到可用的终端模拟器",
                    code="TERMINAL_NOT_FOUND",
                    suggestion="请安装 Windows Terminal、PowerShell 或 Git Bash，或在「本地终端设置」中手动指定终端路径"
                )

        elif sys.platform == "darwin":
            # macOS 终端启动逻辑
            logger.info(f"macOS: 尝试启动 SSH 连接 {alias}")
            
            # 1. 尝试使用指定的终端路径
            if terminal_path and os.path.exists(terminal_path):
                try:
                    if "iTerm" in terminal_path:
                        # iTerm2 特殊处理
                        script = f'tell application "iTerm2" to create terminal with profile "Default" command "ssh {alias}"'
                        subprocess.Popen(["osascript", "-e", script])
                    else:
                        # Terminal.app
                        script = f'tell application "Terminal" to do script "ssh {alias}"'
                        subprocess.Popen(["osascript", "-e", script])
                    logger.info(f"使用指定终端启动: {terminal_path}")
                except Exception as e:
                    logger.warning(f"指定终端启动失败: {e}")
            
            # 2. 尝试使用 iTerm2 (如果已安装)
            try:
                if subprocess.run(["osascript", "-e", 'tell app "iTerm2" to get name'], capture_output=True).returncode == 0:
                    script = f'tell application "iTerm2" to create terminal with profile "Default" command "ssh {alias}"'
                    subprocess.Popen(["osascript", "-e", script])
                    logger.info("使用 iTerm2 启动")
                else:
                    raise Exception("iTerm2 未运行")
            except Exception:
                # 3. 回退到 Terminal.app
                try:
                    script = f'tell application "Terminal" to do script "ssh {alias}; echo; echo \\"连接已关闭，按 Cmd+Q 退出\\""'
                    subprocess.Popen(["osascript", "-e", script])
                    logger.info("使用 Terminal.app 启动")
                except Exception as e:
                    logger.error(f"macOS 终端启动失败: {e}")
                    return _common.error_response(
                        f"无法启动终端: {str(e)}",
                        code="TERMINAL_NOT_FOUND",
                        suggestion="请确认 Terminal.app 或 iTerm2 已安装，并且允许此应用控制终端"
                    )

        else:
            # Linux 终端启动逻辑
            logger.info(f"Linux: 尝试启动 SSH 连接 {alias}")
            
            # 终端检测顺序 (按常见程度)
            terminals = [
                ("gnome-terminal", ["gnome-terminal", "--", "bash", "-c", f"ssh {alias}; echo; echo '连接已关闭，按回车退出'; read"]),
                ("konsole", ["konsole", "-e", "bash", "-c", f"ssh {alias}; read"]),
                ("xfce4-terminal", ["xfce4-terminal", "-e", f"bash -c 'ssh {alias}; read'"]),
                ("terminator", ["terminator", "-e", f"bash -c 'ssh {alias}; read'"]),
                ("alacritty", ["alacritty", "-e", "bash", "-c", f"ssh {alias}; read"]),
                ("kitty", ["kitty", "+run", "bash", "-c", f"ssh {alias}; read"]),
                ("xterm", ["xterm", "-e", f"bash -c 'ssh {alias}; read'"]),
                ("x-terminal-emulator", ["x-terminal-emulator", "-e", f"bash -c 'ssh {alias}; read'"]),
            ]
            
            success = False
            for name, cmd in terminals:
                if shutil.which(name):
                    try:
                        subprocess.Popen(cmd)
                        logger.info(f"使用 {name} 启动")
                        success = True
                        break
                    except Exception as e:
                        logger.warning(f"{name} 启动失败: {e}")
                        continue
            
            if not success:
                # 尝试使用 xdg-terminal (跨桌面环境)
                try:
                    subprocess.Popen(["xdg-terminal", "--command", f"bash -c 'ssh {alias}; read'"])
                    success = True
                    logger.info("使用 xdg-terminal 启动")
                except Exception:
                    pass
            
            if not success:
                return _common.error_response(
                    "无法找到可用的终端模拟器",
                    code="TERMINAL_NOT_FOUND",
                    suggestion="请安装 GNOME Terminal、Konsole 或 xterm，然后重试。\n或者手动执行: ssh " + alias
                )

        _common._sse_broadcast("progress", {"message": f"✓ 已启动终端 SSH 连接到 {alias}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": True, "message": f"终端已打开，正在连接 {alias}"})

    except Exception as e:
        logger.exception("启动终端失败")
        return _common.error_response(
            f"启动终端失败: {str(e)}",
            code="SSH_CONNECT_FAILED",
            suggestion="请检查：1) SSH 密钥是否已生成并上传；2) 服务器地址是否正确；3) 网络连接是否正常"
        )
