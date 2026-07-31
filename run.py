#!/usr/bin/env python3
"""SSH Keys Manager — 启动脚本（确保加载最新代码）"""
import sys
import os
import socket
import argparse

# 禁止写入 .pyc 文件，避免缓存问题
sys.dont_write_bytecode = True

# 确保当前目录在 sys.path 最前面（优先加载本地模块）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.server import app

# 关键模块路由检查（缺失任一项则启动失败，避免面板静默失效）
REQUIRED_BLUEPRINTS = {
    "webssh": "WebSSH 终端",
    "keys": "密钥管理",
    "connections": "我的连接",
    "filesync": "文件同步",
}


def parse_args():
    """解析启动参数，支持命令行与环境变量两种方式覆盖默认端口/主机。"""
    parser = argparse.ArgumentParser(description="SSH Keys Manager 启动脚本")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("SSHKEYS_PORT", "5050")),
        help="首选端口（默认 5050，可通过环境变量 SSHKEYS_PORT 覆盖）",
    )
    parser.add_argument(
        "--host", default=os.environ.get("SSHKEYS_HOST", "127.0.0.1"),
        help="监听地址（默认 127.0.0.1，可通过环境变量 SSHKEYS_HOST 覆盖）",
    )
    parser.add_argument(
        "--no-avoid", action="store_true",
        help="关闭端口避让：若首选端口被占用直接退出，而非自动顺延",
    )
    return parser.parse_args()


def is_port_free(port, host="127.0.0.1"):
    """探测指定端口是否空闲（仅做本机 TCP 绑定测试）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


MAX_PORT_TRIES = 100


def resolve_port(preferred, host="127.0.0.1", avoid=True, max_tries=MAX_PORT_TRIES):
    """端口避让：首选端口空闲则直接用，否则顺延到下一个空闲端口。"""
    if is_port_free(preferred, host):
        return preferred
    if not avoid:
        return None  # 不开避让，直接交给调用方判定失败
    for offset in range(1, max_tries + 1):
        candidate = preferred + offset
        if is_port_free(candidate, host):
            return candidate
    return None


def run_startup_checks(app):
    """集中执行启动前检查，结果写入 app.config 供前端展示，返回是否通过。"""
    counts = {}
    for rule in app.url_map.iter_rules():
        bp = rule.endpoint.split(".")[0] if "." in rule.endpoint else ""
        counts[bp] = counts.get(bp, 0) + 1

    messages = []
    missing = []
    for key, name in REQUIRED_BLUEPRINTS.items():
        n = counts.get(key, 0)
        ok = n > 0
        messages.append(f"{name}: {n} 条路由 [{'OK' if ok else '缺失'}]")
        if not ok:
            missing.append(name)

    app.config["STARTUP_CHECKS"] = messages
    return not missing


if __name__ == "__main__":
    args = parse_args()

    # —— 启动前检查 ——
    checks_ok = run_startup_checks(app)
    for m in app.config.get("STARTUP_CHECKS", []):
        print(f"[启动检查] {m}")
    if not checks_ok:
        print("[错误] 关键模块路由未注册，启动中止。")
        sys.exit(1)

    # —— 端口避让 ——
    port = resolve_port(args.port, args.host, avoid=not args.no_avoid)
    if port is None:
        if args.no_avoid:
            print(f"[错误] 端口 {args.port} 已被占用，且已关闭端口避让（--no-avoid）。")
        else:
            print(f"[错误] 端口 {args.port} 起连续 {MAX_PORT_TRIES} 个端口均被占用，无法启动。")
        sys.exit(1)

    avoided = port != args.port
    if avoided:
        print(f"[避让] 首选端口 {args.port} 被占用，已自动改用端口 {port}。")

    # 将绑定信息写入 app.config，供 /api/system/info 读取
    app.config["BIND_HOST"] = args.host
    app.config["BIND_PORT"] = port
    app.config["PREFERRED_PORT"] = args.port
    app.config["PORT_AVOIDED"] = avoided

    print(f"[启动] Flask 服务启动中... http://{args.host}:{port}")
    try:
        app.run(debug=False, port=port, host=args.host, use_reloader=False)
    except OSError as e:
        print(f"[错误] 无法绑定端口 {port}：{e}")
        sys.exit(1)
