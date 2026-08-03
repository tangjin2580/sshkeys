"""Key Management Routes - Generate, Download, Upload"""
from flask import Blueprint, request, jsonify, send_file
import io
import os
import logging
from datetime import datetime
import modules.common as _common
from modules.common import _sse_broadcast, _create_progress_callback
from modules.key_generator import SSHKeyGenerator, KEY_TYPES, compute_fingerprint
from modules.key_uploader import KeyUploader
from modules.ssh_config import get_ssh_dir, add_or_update_host
from modules.connections_store import add_connection
from modules.server import limiter

logger = logging.getLogger(__name__)
keys_bp = Blueprint("keys", __name__)


@keys_bp.route("/api/key-types", methods=["GET"])
def get_key_types():
    """Get supported key types list"""
    return jsonify(KEY_TYPES)


@keys_bp.route("/api/generate", methods=["POST"])
@limiter.limit("10 per minute")  # Limit key generation to prevent resource exhaustion
def generate_key():
    """
    Generate SSH key pair
    Request body: {"key_type": "ed25519", "key_size": 256, "passphrase": "", "comment": "user@host"}
    """
    # _current_keys managed via _common
    data = request.get_json() or {}

    key_type = data.get("key_type", "ed25519")
    key_size = data.get("key_size", 256)
    passphrase = data.get("passphrase", "") or None
    comment = data.get("comment", "user@host").strip() or "user@host"
    save_path = data.get("save_path", "")
    curve = data.get("curve") or None

    _sse_broadcast("progress", {"message": f"Generating {key_type.upper()} key...", "time": datetime.now().strftime("%H:%M:%S")})

    try:
        priv_str, pub_str, priv_bytes, pub_bytes = SSHKeyGenerator.generate_key_pair(
            key_type=key_type,
            key_size=key_size,
            passphrase=passphrase,
            comment=comment,
            curve=curve,
        )

        # Store in session
        _common._current_keys = {
            "private_key": priv_str,
            "public_key": pub_str,
            "key_type": key_type,
            "key_size": key_size,
            "comment": comment,
        }

        # Write to file if save path specified
        save_result = None
        if save_path:
            try:
                private_path = os.path.join(save_path, f"id_{key_type}")
                public_path = private_path + ".pub"
                SSHKeyGenerator.save_key_files(priv_str, pub_str, private_path, public_path)
                save_result = {"private": private_path, "public": public_path}
                _sse_broadcast("progress", {"message": f"Key saved to: {private_path}", "time": datetime.now().strftime("%H:%M:%S")})
            except Exception as e:
                _sse_broadcast("progress", {"message": f"Save failed: {e}", "time": datetime.now().strftime("%H:%M:%S")})
                save_result = {"error": str(e)}

        _sse_broadcast("progress", {"message": "✓ Key generation complete", "time": datetime.now().strftime("%H:%M:%S")})
        # Calculate fingerprint
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

    except ValueError as e:
        # Handle validation errors (e.g., unsupported key type, invalid size)
        logger.warning(f"Key generation validation failed: {e}")
        _sse_broadcast("progress", {"message": f"✗ Validation failed: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.exception("Key generation failed")
        _sse_broadcast("progress", {"message": f"✗ Generation failed: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e)}), 500


@keys_bp.route("/api/download-private-key", methods=["GET"])
@limiter.limit("20 per minute")
def download_private_key():
    """Download private key file"""
    # _current_keys via _common
    if not _common._current_keys.get("private_key"):
        return jsonify({"success": False, "error": "No private key available. Please generate one first."}), 404

    key_type = _common._current_keys.get("key_type", "key")
    filename = f"id_{key_type}"

    return send_file(
        io.BytesIO(_common._current_keys["private_key"].encode("utf-8")),
        mimetype="application/x-pem-file",
        as_attachment=True,
        download_name=filename,
    )


@keys_bp.route("/api/upload", methods=["POST"])
@limiter.limit("30 per minute")  # Limit uploads to external services
def upload_key():
    """
    Upload public key to specified target
    Request body: {
        "target": "github" | "gitlab" | "server",
        "token": "...",           // Required for GitHub/GitLab
        "title": "SSH Key",
        "host": "...",            // Required for server mode
        "username": "...",        // Required for server mode
        "password": "...",        // Optional for server mode
        "port": 22,               // Optional for server mode
        "key_name": "...",        // Specify existing key filename (optional)
        "public_key": "...",      // Or provide public key content directly (optional)
        "gitlab_url": "...",      // GitLab self-hosted instance (optional)
        "host_alias": "...",      // Server mode: SSH config Host alias (optional)
        "write_config": true      // Server mode: whether to write SSH config (optional)
    }
    """
    # _current_keys via _common
    data = request.get_json() or {}

    # Prefer specified key file
    key_name = data.get("key_name", "").strip()
    public_key = data.get("public_key", "").strip()
    
    if key_name:
        # Read public key from specified key file
        try:
            ssh_dir = get_ssh_dir()
            key_file = ssh_dir / key_name
            pub_key_file = ssh_dir / f"{key_name}.pub"
            
            if not key_file.exists():
                return jsonify({"success": False, "error": f"Private key file not found: {key_name}"}), 404
            if not pub_key_file.exists():
                return jsonify({"success": False, "error": f"Public key file not found: {key_name}.pub"}), 404
            
            with open(pub_key_file, 'r', encoding='utf-8') as f:
                public_key = f.read().strip()
        except Exception as e:
            logger.exception(f"Failed to read key file: {key_name}")
            return jsonify({"success": False, "error": f"Failed to read key file: {str(e)}"}), 500
    elif public_key:
        # Use public key from frontend
        pass
    elif _common._current_keys.get("public_key"):
        # Use recently generated key
        public_key = _common._current_keys["public_key"]
    else:
        return jsonify({"success": False, "error": "Please generate a key or select an existing one"}), 400

    target = data.get("target", "server")
    progress_cb = _create_progress_callback()

    try:
        if target == "github":
            token = data.get("token", "").strip()
            if not token:
                return jsonify({"success": False, "error": "GitHub token is required"}), 400
            result = KeyUploader.upload_to_github(
                public_key=public_key,
                token=token,
                title=data.get("title", "SSH Key Manager"),
                progress_callback=progress_cb,
            )

        elif target == "gitlab":
            token = data.get("token", "").strip()
            if not token:
                return jsonify({"success": False, "error": "GitLab token is required"}), 400
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
                return jsonify({"success": False, "error": "Server address and username are required"}), 400
            result = KeyUploader.upload_to_server(
                public_key=public_key,
                host=host,
                username=username,
                password=data.get("password") or None,
                port=data.get("port", 22),
                progress_callback=progress_cb,
            )

            # Optional: write to SSH config
            host_alias = data.get("host_alias", "").strip()
            write_config = data.get("write_config", False)
            if write_config and host_alias and result.get("success"):
                try:
                    # Determine IdentityFile path
                    if key_name:
                        identity_file = f"~/.ssh/{key_name}"
                    elif _common._current_keys.get("private_key"):
                        # Use default naming for current session key
                        kt = _common._current_keys.get("key_type", "ed25519")
                        identity_file = f"~/.ssh/id_{kt}"
                    else:
                        identity_file = f"~/.ssh/id_ed25519"

                    add_or_update_host(
                        host_alias=host_alias,
                        hostname=host,
                        user=username,
                        identity_file=identity_file,
                        port=data.get("port", 22),
                    )
                    result["config_written"] = True
                    result["config_host"] = host_alias
                    _sse_broadcast("progress", {"message": f"✓ SSH config written: Host {host_alias}", "time": datetime.now().strftime("%H:%M:%S")})

                    # Sync save to connection management
                    add_connection(
                        alias=host_alias,
                        hostname=host,
                        user=username,
                        identity_file=identity_file,
                        port=data.get("port", 22),
                    )
                except Exception as e:
                    logger.exception("Failed to write SSH config")
                    result["config_error"] = str(e)

        else:
            return jsonify({"success": False, "error": f"Unsupported upload target: {target}"}), 400

        _sse_broadcast("upload_result", result)
        return jsonify(result)

    except Exception as e:
        logger.exception("Upload failed")
        _sse_broadcast("progress", {"message": f"✗ Upload error: {e}", "time": datetime.now().strftime("%H:%M:%S")})
        return jsonify({"success": False, "error": str(e)}), 500
