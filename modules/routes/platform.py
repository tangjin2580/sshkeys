"""平台信息路由 — 终端路径检测"""
from flask import Blueprint, request, jsonify
import sys
import os
import shutil

platform_bp = Blueprint("platform", __name__)

@platform_bp.route("/api/platform-info", methods=["GET"])
def platform_info():
    """
    返回当前运行平台信息。
    用于前端根据平台显示不同的终端候选列表。
    """
    import sys
    import os
    
    platform = sys.platform
    info = {
        "platform": platform,
        "is_windows": platform == "win32",
        "is_macos": platform == "darwin",
        "is_linux": platform == "linux",
        "platform_name": {
            "win32": "Windows",
            "darwin": "macOS",
            "linux": "Linux"
        }.get(platform, platform)
    }
    
    # 尝试获取更详细的版本信息
    if platform == "win32":
        try:
            import platform as plat
            info["version"] = plat.version()
            info["release"] = plat.release()
        except Exception:
            pass
    elif platform == "darwin":
        try:
            import platform as plat
            info["mac_version"] = plat.mac_ver()[0]
        except Exception:
            pass
    elif platform == "linux":
        try:
            import distro
            info["distro"] = distro.name()
            info["distro_version"] = distro.version()
        except ImportError:
            # distro 未安装，尝试读取 /etc/os-release
            try:
                with open("/etc/os-release", "r") as f:
                    for line in f:
                        if line.startswith("NAME="):
                            info["distro"] = line.split("=")[1].strip().strip('"')
                            break
            except Exception:
                pass
    
    return jsonify(info)

@platform_bp.route("/api/check-terminal-path", methods=["POST"])
def check_terminal_path():
    """
    检测终端路径是否有效。
    接受 POST，body: {"path": "wt.exe"} 或 {"path": "C:\\path\\to\\term.exe"}
    返回: {"valid": bool, "path": str, "message": str}
    """
    data = request.get_json() or {}
    path = data.get("path", "").strip()
    if not path:
        return jsonify({"valid": False, "message": "路径不能为空"}), 400

    import shutil
    import os

    # 判断是否是完整路径（包含路径分隔符）
    is_absolute = "/" in path or "\\" in path

    if is_absolute:
        # 完整路径：直接检查文件是否存在
        exists = os.path.isfile(path)
        if exists:
            return jsonify({
                "valid": True,
                "path": path,
                "message": f"✅ 路径有效：{path}"
            })
        else:
            return jsonify({
                "valid": False,
                "path": path,
                "message": f"❌ 文件不存在：{path}"
            })

    # 不是完整路径：在 PATH 中查找
    found = shutil.which(path)
    if found:
        return jsonify({
            "valid": True,
            "path": found,
            "message": f"✅ 已找到：{found}"
        })

    # Windows 下尝试加 .exe 后缀
    if sys.platform == "win32" and not path.lower().endswith(".exe"):
        found = shutil.which(path + ".exe")
        if found:
            return jsonify({
                "valid": True,
                "path": found,
                "message": f"✅ 已找到：{found}"
            })

    return jsonify({
        "valid": False,
        "path": path,
        "message": f"❌ 未在 PATH 中找到：{path}"
    })
