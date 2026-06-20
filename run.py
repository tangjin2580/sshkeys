#!/usr/bin/env python3
"""SSH Keys Manager — 启动脚本（确保加载最新代码）"""
import sys
import os

# 禁止写入 .pyc 文件，避免缓存问题
sys.dont_write_bytecode = True

# 确保当前目录在 sys.path 最前面（优先加载本地模块）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.server import app

# 启动前确认路由已注册
webssh_routes = [r.rule for r in app.url_map.iter_rules() if 'webssh' in r.rule]
print(f"[启动检查] WebSSH 路由数量: {len(webssh_routes)}")
print(f"[启动检查] WebSSH 路由: {webssh_routes}")

if not webssh_routes:
    print("[错误] WebSSH 路由未注册！请检查 modules/webssh.py")
    sys.exit(1)

print(f"[启动] Flask 服务启动中... http://127.0.0.1:5000")
app.run(debug=False, port=5000, host='127.0.0.1')
