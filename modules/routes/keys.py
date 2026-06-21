"""密钥管理路由 — 生成、下载、上传"""
from flask import Blueprint, request, jsonify, send_file
import io
import os
import logging
from datetime import datetime
import modules.common as _common
from modules.common import _sse_broadcast, _create_progress_callback
from modules.key_generator import SSHKeyGenerator, KEY_TYPES, compute_fingerprint
from modules.key_uploader import KeyUploader
from modules.ssh_config import get_ssh_dir

logger = logging.getLogger(__name__)
keys_bp = Blueprint("keys", __name__)

@keys_bp.route("/api/key-types", methods=["GET"])
def get_key_types():
    """获取支持的密钥类型列表"""
    return jsonify(KEY_TYPES)

@keys_bp.route("/api/generate", methods=["POST"])
def generate_key():
    """
    生成 SSH 密钥对
    请求体: {"key_type": "ed25519", "key_size": 256, "passphrase": "", "comment": "user@host"}
    """
    # _current_keys managed via _common
    data = request.get_json() or {}

    key_type = data.get("key_type", "ed25519")
    key_size = data.get("key_size", 256)
    passphrase = data.get("passphrase", "") or None
    comment = data.get("comment", "user@host").strip() or "user@host"
    save_path = data.get("save_path", "")
    curve = data.get("curve") or None

    _sse_broadcast("progress", {"message": f"开始生成 {key_type.upper()} 密钥 ...", "time": datetime.now().strftime("%H:%M:%S")})

    try:
        priv_str, pub_str, priv_bytes, pub_bytes = SSHKeyGenerator.generate_key_pair(
            key_type=key_type,
            key_size=key_size,
            passphrase=passphrase,
            comment=comment,
            curve=curve,
        )

        # 存储到会话
        _common._current_keys = {
            "private_key": priv_str,
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
        }

        # 如果指定了保存路径，写入文件
        save_result = None
        if save_path:
            try:
                private_path = os.path.join(save_path, f"id_{key_type}")
                public_path = private_path + ".pub"
                SSHKeyGenerator.save_key_files(priv_str, pub_str, private_path, public_path)
                save_result = {"private": private_path, "public": public_path}
                _sse_broadcast("progress", {"message": f"密钥已保存到: {private_path}", "time": datetime.now().strftime("%H:%M:%S")})
            except Exception as e:
                _sse_broadcast("progress", {"message": f"保存失败: {e}", "time": datetime.now().strftime("%H:%M:%S")})
                save_result = {"error": str(e)}

        _sse_broadcast("progress", {"message": "✓ 密钥生成完成", "time": datetime.now().strftime("%H:%M:%S")})
        # 计算指纹
        fingerprint = compute_fingerprint(pub_str)

        _sse_broadcast("key_generated", {
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
            "fingerprint": fingerprint,
            "has_passphrase": bool(passphrase),
            "saved": save_result,
        })

        return jsonify({
            "success": True,
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
            "fingerprint": fingerprint,
            "has_passphrase": bool(passphrase),
            "saved": save_result,
        })

    except Exception as e:
        logger.exception("密钥生成失败")
        _sse_broadcast("progress", {"message": f"✗ 生成失败: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e)}), 500

@keys_bp.route("/api/download-private-key", methods=["GET"])
def download_private_key():
    """下载私钥文件"""
    # _current_keys via _common
    if not _common._current_keys.get("private_key"):
        return jsonify({"success": False, "error": "没有可下载的私钥，请先生成"}), 404

    key_type = _common._current_keys.get("key_type", "key")
    filename = f"id_{key_type}"

    return send_file(
        io.BytesIO(_common._current_keys["private_key"].encode("utf-8")),
        mimetype="application/x-pem-file",
        as_attachment=True,
        download_name=filename,
    )

@keys_bp.route("/api/upload", methods=["POST"])
def upload_key():
    """
    上传公钥到指定目标
    请求体: {
        "target": "github" | "gitlab" | "server",
        "token": "...",           // GitHub/GitLab 需要
        "title": "SSH Key",
        "host": "...",            // server 模式需要
        "username": "...",        // server 模式需要
        "password": "...",        // server 模式可选
        "port": 22,               // server 模式可选
        "key_name": "...",        // 指定使用已有密钥文件名（可选）
        "public_key": "...",      // 或直接提供公钥内容（可选）
        "gitlab_url": "..."       // GitLab 自托管实例 (可选)
    }
    """
    # _current_keys via _common
    data = request.get_json() or {}

    # 优先使用指定的密钥文件
    key_name = data.get("key_name", "").strip()
    public_key = data.get("public_key", "").strip()
    
    if key_name:
        # 从指定密钥文件读取公钥
        try:
            ssh_dir = get_ssh_dir()
            key_file = ssh_dir / key_name
            pub_key_file = ssh_dir / f"{key_name}.pub"
            
            if not key_file.exists():
                return jsonify({"success": False, "error": f"密钥文件不存在: {key_name}"}), 404
            if not pub_key_file.exists():
                return jsonify({"success": False, "error": f"公钥文件不存在: {key_name}.pub"}), 404
            
            with open(pub_key_file, 'r', encoding='utf-8') as f:
                public_key = f.read().strip()
        except Exception as e:
            logger.exception(f"读取密钥文件失败: {key_name}")
            return jsonify({"success": False, "error": f"读取密钥文件失败: {str(e)}"}), 500
    elif public_key:
        # 使用前端传来的公钥
        pass
    elif _common._current_keys.get("public_key"):
        # 使用最近生成的密钥
        public_key = _common._current_keys["public_key"]
    else:
        return jsonify({"success": False, "error": "请先生成密钥或选择已有密钥"}), 400

    target = data.get("target", "server")
    progress_cb = _create_progress_callback()

    try:
        if target == "github":
            token = data.get("token", "").strip()
            if not token:
                return jsonify({"success": False, "error": "请输入 GitHub Token"}), 400
            result = KeyUploader.upload_to_github(
                public_key=public_key,
                token=token,
                title=data.get("title", "SSH Key Manager"),
                progress_callback=progress_cb,
            )

        elif target == "gitlab":
            token = data.get("token", "").strip()
            if not token:
                return jsonify({"success": False, "error": "请输入 GitLab Token"}), 400
            result = KeyUploader.upload_to_gitlab(
                public_key=public_key,
                token=token,
                title=data.get("title", "SSH Key Manager"),
                gitlab_url=data.get("gitlab_url", "https://gitlab.com"),
                progress_callback=progress_cb,
            )

        elif target == "server":
            host = data.get("host", "").strip()
            username = data.get("username", "").strip()
            if not host or not username:
                return jsonify({"success": False, "error": "请输入服务器地址和用户名"}), 400
            result = KeyUploader.upload_to_server(
                public_key=public_key,
                host=host,
                username=username,
                password=data.get("password") or None,
                port=data.get("port", 22),
                progress_callback=progress_cb,
            )

        else:
            return jsonify({"success": False, "error": f"不支持的上传目标: {target}"}), 400

        _sse_broadcast("upload_result", result)
        return jsonify(result)

    except Exception as e:
        logger.exception("上传失败")
        _sse_broadcast("progress", {"message": f"✗ 上传异常: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e)}), 500
