#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# 优先使用 venv，否则回退到 python3 / python
if [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "错误: 找不到 Python，请先安装 Python 3.10+"
    exit 1
fi

echo "============================================"
echo "  SSH Key Manager"
echo "  Python: $PYTHON"
echo "  参数:   $*"
echo "============================================"

exec "$PYTHON" main.py "$@"
