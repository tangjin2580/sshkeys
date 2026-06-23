"""SSH Config 管理路由 — 密钥、配置条目、批量更新"""
from flask import Blueprint, request, jsonify
import logging
from datetime import datetime
import modules.common as _common
from modules.common import _sse_broadcast, _create_progress_callback
from modules.ssh_config import (
    list_existing_keys, parse_ssh_config, add_or_update_host,
    save_key_to_ssh_dir, get_ssh_dir, delete_key_file, remove_host_from_config,
)
from modules.connections_store import add_connection
from modules.key_uploader import KeyUploader

logger = logging.getLogger(__name__)
ssh_config_bp = Blueprint("ssh_config", __name__)

@ssh_config_bp.route("/api/existing-keys", methods=["GET"])
def get_existing_keys():
    """获取本地已有密钥列表"""
    try:
        key_name = request.args.get("key_name")
        key_type = request.args.get("key_type", "ed25519")
        if key_name:
            # 查询单个密钥的公钥
            ssh_dir = get_ssh_dir()
            pub_path = ssh_dir / f"{key_name}.pub"
            if pub_path.exists():
                with open(pub_path, "r", encoding="utf-8") as f:
                    return jsonify({"success": True, "public_key": f.read().strip()})
            return jsonify({"success": False, "error": "文件不存在"}), 404

        keys = list_existing_keys()
        logger.info(f"找到 {len(keys)} 个密钥: {[k['name'] for k in keys]}")
        return jsonify({"success": True, "keys": keys})
    except Exception as e:
        logger.exception("读取已有密钥失败")
        return jsonify({"success": False, "error": str(e)}), 500

@ssh_config_bp.route("/api/ssh-config", methods=["GET"])
def get_ssh_config():
    """获取本地 ~/.ssh 已有密钥列表和 config 条目"""
    try:
        keys = list_existing_keys()
        config_entries = parse_ssh_config()
        ssh_dir = str(get_ssh_dir())
        return jsonify({
            "success": True,
            "ssh_dir": ssh_dir,
            "existing_keys": keys,
            "config_entries": config_entries,
        })
    except Exception as e:
        logger.exception("读取 SSH config 失败")
        return jsonify({"success": False, "error": str(e)}), 500

@ssh_config_bp.route("/api/save-and-setup", methods=["POST"])
def save_and_setup():
    """
    一键：保存私钥到 ~/.ssh + 写入 SSH config + （可选）上传到服务器
    请求体: {
        "host_alias": "myserver",       // SSH config Host 别名
        "hostname": "192.168.1.100",    // 服务器地址
        "user": "root",                 // SSH 用户名
        "port": 22,                     // SSH 端口
        "upload": true,                 // 是否同时上传公钥到服务器
        "upload_password": "xxx",       // 上传所需的密码（可选）
        "key_name": "id_ed25519"        // 使用已有密钥文件名（可选，不填则用当前生成的）
    }
    """
    # _current_keys via _common
    data = request.get_json() or {}

    key_name = data.get("key_name", "").strip()

    # 支持两种方式获取密钥：1) 指定已有密钥文件 2) 当前会话生成的密钥
    if key_name:
        # 从已有密钥文件读取
        from modules.ssh_config import get_ssh_dir, list_existing_keys
        ssh_dir = get_ssh_dir()
        priv_path = ssh_dir / key_name
        pub_path = ssh_dir / f"{key_name}.pub"
        if not priv_path.exists():
            return jsonify({"success": False, "error": f"密钥文件不存在: {key_name}"}), 404
        if not pub_path.exists():
            return jsonify({"success": False, "error": f"公钥文件不存在: {key_name}.pub"}), 404
        try:
            with open(priv_path, "r", encoding="utf-8") as f:
                priv_key = f.read().strip()
            with open(pub_path, "r", encoding="utf-8") as f:
                pub_key = f.read().strip()
            # 从文件名推断密钥类型
            if "rsa" in key_name.lower():
                key_type = "rsa"
            elif "ecdsa" in key_name.lower():
                key_type = "ecdsa"
            else:
                key_type = "ed25519"
            # 临时存到会话中供上传使用
            _common._current_keys = {
                "private_key": priv_key,
                "public_key": pub_key,
                "key_type": key_type,
                "key_size": 256,
                "comment": "user@host",
            }
        except Exception as e:
            logger.exception(f"读取密钥文件失败: {key_name}")
            return jsonify({"success": False, "error": f"读取密钥文件失败: {str(e)}"}), 500
    elif not _common._current_keys.get("private_key"):
        return jsonify({"success": False, "error": "请先生成密钥或指定已有密钥"}), 400

    host_alias = data.get("host_alias", "").strip()
    hostname = data.get("hostname", "").strip()
    user = data.get("user", "").strip()
    port = data.get("port", 22)
    do_upload = data.get("upload", False)

    if not host_alias or not hostname or not user:
        return jsonify({"success": False, "error": "请填写 Host 别名、服务器地址和用户名"}), 400

    progress_cb = _create_progress_callback()
    results = {"saved": None, "config": None, "upload": None}

    try:
        # 1. 保存私钥到 ~/.ssh（仅当新生成的密钥时才保存，已有密钥跳过）
        if key_name:
            # 使用已有密钥，跳过保存步骤，直接使用已有文件名
            saved = {"filename": key_name, "path": str(priv_path)}
            _sse_broadcast("progress", {"message": f"✓ 使用已有密钥: {key_name}", "time": datetime.now().strftime("%H:%M:%S")})
        else:
            _sse_broadcast("progress", {"message": "正在保存密钥到 ~/.ssh ...", "time": datetime.now().strftime("%H:%M:%S")})
            saved = save_key_to_ssh_dir(
                private_key_str=_common._current_keys["private_key"],
                public_key_str=_common._current_keys["public_key"],
                key_type=_common._current_keys.get("key_type", "ed25519"),
            )
            _sse_broadcast("progress", {"message": f"✓ 密钥已保存: {saved['filename']}", "time": datetime.now().strftime("%H:%M:%S")})
        results["saved"] = saved

        # 2. 写入 SSH config
        _sse_broadcast("progress", {"message": f"正在写入 SSH config (Host {host_alias}) ...", "time": datetime.now().strftime("%H:%M:%S")})
        identity_file = f"~/.ssh/{saved['filename']}"
        add_or_update_host(
            host_alias=host_alias,
            hostname=hostname,
            user=user,
            identity_file=identity_file,
            port=port,
        )
        results["config"] = {"host": host_alias, "hostname": hostname, "user": user, "port": port}
        _sse_broadcast("progress", {"message": f"✓ SSH config 已写入: Host {host_alias}", "time": datetime.now().strftime("%H:%M:%S")})

        # 2.5 同步保存到连接管理
        add_connection(
            alias=host_alias,
            hostname=hostname,
            user=user,
            identity_file=identity_file,
            port=port,
        )
        _sse_broadcast("progress", {"message": f"✓ 连接已录入: {host_alias}", "time": datetime.now().strftime("%H:%M:%S")})

        # 3. 可选：同时上传公钥到服务器
        if do_upload:
            _sse_broadcast("progress", {"message": f"正在上传公钥到 {hostname} ...", "time": datetime.now().strftime("%H:%M:%S")})
            upload_result = KeyUploader.upload_to_server(
                public_key=_common._current_keys["public_key"],
                host=hostname,
                username=user,
                password=data.get("upload_password") or None,
                port=port,
                progress_callback=progress_cb,
            )
            results["upload"] = upload_result
            if upload_result.get("success"):
                _sse_broadcast("progress", {"message": "✓ 公钥已上传到服务器", "time": datetime.now().strftime("%H:%M:%S")})
            else:
                _sse_broadcast("progress", {"message": "⚠ 密钥已保存但上传失败: " + upload_result.get("message", ""), "time": datetime.now().strftime("%H:%M:%S")})

        # 汇总结果
        all_ok = results["saved"] is not None and results["config"] is not None
        if do_upload:
            all_ok = all_ok and results["upload"] is not None and results["upload"].get("success")

        summary = "🎉 全部完成！现在可以用 `ssh {alias}` 免密登录了".format(alias=host_alias)
        if not all_ok:
            summary = "⚠ 部分操作完成，请查看日志"

        _sse_broadcast("setup_complete", {"success": all_ok, "message": summary, "results": results})

        return jsonify({
            "success": all_ok,
            "message": summary,
            "results": results,
        })

    except Exception as e:
        logger.exception("一键部署失败")
        _sse_broadcast("progress", {"message": f"✗ 部署失败: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e), "results": results}), 500

@ssh_config_bp.route("/api/delete-key", methods=["POST"])
def delete_key():
    """删除指定密钥文件"""
    data = request.get_json() or {}
    key_name = data.get("key_name", "").strip()
    if not key_name:
        return jsonify({"success": False, "error": "请指定密钥文件名"}), 400

    result = delete_key_file(key_name)
    _sse_broadcast("progress", {"message": result["message"], "time": datetime.now().strftime("%H:%M:%S")})
    return jsonify(result)

@ssh_config_bp.route("/api/delete-config-host", methods=["POST"])
def delete_config_host():
    """删除指定 SSH config Host 条目"""
    data = request.get_json() or {}
    host_alias = data.get("host_alias", "").strip()
    if not host_alias:
        return jsonify({"success": False, "error": "请指定 Host 别名"}), 400

    result = remove_host_from_config(host_alias)
    _sse_broadcast("progress", {"message": result["message"], "time": datetime.now().strftime("%H:%M:%S")})
    return jsonify(result)

@ssh_config_bp.route("/api/ssh-config/batch", methods=["POST"])
def batch_update_ssh_config():
    """
    批量写入 SSH config 条目（追加/覆盖）
    请求体: {
        "entries": [
            {"host": "srv1", "hostname": "1.2.3.4", "user": "root", "port": 22, "identityfile": "~/.ssh/id_ed25519"},
            ...
        ]
    }
    """
    data = request.get_json() or {}
    entries = data.get("entries", [])

    if not entries or not isinstance(entries, list):
        return jsonify({"success": False, "error": "请提供 entries 数组"}), 400

    success_count = 0
    errors = []

    for i, entry in enumerate(entries):
        host = entry.get("host", "").strip()
        hostname = entry.get("hostname", "").strip()
        user = entry.get("user", "").strip()
        port = entry.get("port", 22)
        identityfile = entry.get("identityfile", "").strip()

        if not host or not hostname or not user:
            errors.append(f"条目 {i+1}: Host/HostName/User 为必填")
            continue

        try:
            ok = add_or_update_host(
                host_alias=host,
                hostname=hostname,
                user=user,
                identity_file=identityfile or f"~/.ssh/id_{host}",
                port=int(port) if port else 22,
            )
            if ok:
                success_count += 1
                _sse_broadcast("progress", {
                    "message": f"✓ 已写入: Host {host}",
                    "time": datetime.now().strftime("%H:%M:%S")
                })
            else:
                errors.append(f"条目 {i+1} ({host}): 写入失败")
        except Exception as e:
            errors.append(f"条目 {i+1} ({host}): {str(e)}")

    if success_count == 0 and errors:
        return jsonify({"success": False, "error": "全部失败", "errors": errors}), 500

    result = {
        "success": True,
        "count": success_count,
        "errors": errors if errors else None,
    }
    _sse_broadcast("progress", {
        "message": f"批量写入完成: {success_count}/{len(entries)} 个条目",
        "time": datetime.now().strftime("%H:%M:%S")
    })
    return jsonify(result)
