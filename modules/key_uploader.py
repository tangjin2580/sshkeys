"""
SSH 公钥上传模块
支持 GitHub / GitLab API 上传 和 远程服务器 ssh-copy-id 风格上传
"""

import logging
import tempfile
import os
from typing import Optional, Callable

import requests
import paramiko

logger = logging.getLogger(__name__)


class KeyUploader:
    """SSH 公钥上传器"""

    @staticmethod
    def upload_to_github(
        public_key: str,
        token: str,
        title: str = "SSH Key Manager",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """
        通过 GitHub API 上传公钥

        Args:
            public_key: OpenSSH 格式公钥字符串
            token: GitHub Personal Access Token (需 admin:public_key 权限)
            title: 密钥标题
            progress_callback: 进度回调

        Returns:
            {"success": bool, "message": str}
        """
        def _log(msg: str):
            logger.info(msg)
            if progress_callback:
                progress_callback(msg)

        _log("正在连接 GitHub API ...")
        url = "https://api.github.com/user/keys"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }
        data = {"title": title, "key": public_key.strip()}

        try:
            resp = requests.post(url, json=data, headers=headers, timeout=15)
            if resp.status_code == 201:
                _log("✓ GitHub 公钥上传成功")
                return {"success": True, "message": "GitHub 上传成功"}
            elif resp.status_code == 422:
                # 密钥已存在
                _log("⚠ 该公钥可能已存在于 GitHub 账户中")
                return {"success": False, "message": "密钥已存在或格式无效"}
            elif resp.status_code == 401:
                _log("✗ GitHub Token 无效或无权限")
                return {"success": False, "message": "Token 无效，请检查权限 (需 admin:public_key)"}
            else:
                err_detail = resp.json().get("message", resp.text)
                _log(f"✗ GitHub API 错误: {resp.status_code} - {err_detail}")
                return {"success": False, "message": f"上传失败: {err_detail}"}
        except requests.RequestException as e:
            _log(f"✗ 网络请求失败: {e}")
            return {"success": False, "message": f"网络错误: {str(e)}"}

    @staticmethod
    def upload_to_gitlab(
        public_key: str,
        token: str,
        title: str = "SSH Key Manager",
        gitlab_url: str = "https://gitlab.com",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """
        通过 GitLab API 上传公钥

        Args:
            public_key: OpenSSH 格式公钥
            token: GitLab Personal Access Token (需 api 权限)
            title: 密钥标题
            gitlab_url: GitLab 实例 URL (支持自托管)
            progress_callback: 进度回调

        Returns:
            {"success": bool, "message": str}
        """
        def _log(msg: str):
            logger.info(msg)
            if progress_callback:
                progress_callback(msg)

        _log(f"正在连接 GitLab API ({gitlab_url}) ...")
        url = f"{gitlab_url.rstrip('/')}/api/v4/user/keys"
        headers = {"PRIVATE-TOKEN": token}
        data = {"title": title, "key": public_key.strip()}

        try:
            resp = requests.post(url, json=data, headers=headers, timeout=15)
            if resp.status_code == 201:
                _log("✓ GitLab 公钥上传成功")
                return {"success": True, "message": "GitLab 上传成功"}
            elif resp.status_code == 400:
                err_msg = resp.json().get("message", "密钥已存在或指纹冲突")
                _log(f"⚠ {err_msg}")
                return {"success": False, "message": err_msg}
            elif resp.status_code == 401:
                _log("✗ GitLab Token 无效")
                return {"success": False, "message": "Token 无效，请检查权限 (需 api scope)"}
            else:
                err_detail = resp.json().get("message", resp.text)
                _log(f"✗ GitLab API 错误: {resp.status_code} - {err_detail}")
                return {"success": False, "message": f"上传失败: {err_detail}"}
        except requests.RequestException as e:
            _log(f"✗ 网络请求失败: {e}")
            return {"success": False, "message": f"网络错误: {str(e)}"}

    @staticmethod
    def upload_to_server(
        public_key: str,
        host: str,
        username: str,
        password: Optional[str] = None,
        port: int = 22,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """
        通过 SSH 连接将公钥追加到远程服务器的 ~/.ssh/authorized_keys
        等效于 ssh-copy-id 命令

        Args:
            public_key: OpenSSH 格式公钥
            host: 远程主机地址
            username: SSH 用户名
            password: SSH 密码 (若为 None 则尝试默认私钥认证)
            port: SSH 端口
            progress_callback: 进度回调

        Returns:
            {"success": bool, "message": str}
        """
        def _log(msg: str):
            logger.info(msg)
            if progress_callback:
                progress_callback(msg)

        _log(f"正在连接 {username}@{host}:{port} ...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            if password:
                client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    password=password,
                    timeout=15,
                )
            else:
                # 使用默认 SSH 私钥认证
                client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    timeout=15,
                )

            _log("SSH 连接成功，正在写入 authorized_keys ...")

            # 确保 ~/.ssh 目录存在且权限正确
            commands = [
                "mkdir -p ~/.ssh",
                "chmod 700 ~/.ssh",
                f"echo '{public_key.strip()}' >> ~/.ssh/authorized_keys",
                "chmod 600 ~/.ssh/authorized_keys",
            ]

            for cmd in commands:
                stdin, stdout, stderr = client.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    err = stderr.read().decode().strip()
                    _log(f"✗ 命令执行失败: {cmd} - {err}")
                    return {"success": False, "message": f"写入失败: {err}"}

            _log("✓ 公钥已追加到远程服务器 authorized_keys")
            return {"success": True, "message": f"已上传到 {host}"}

        except paramiko.AuthenticationException:
            _log("✗ SSH 认证失败")
            return {"success": False, "message": "认证失败，请检查用户名/密码"}
        except paramiko.SSHException as e:
            _log(f"✗ SSH 连接异常: {e}")
            return {"success": False, "message": f"SSH 连接失败: {str(e)}"}
        except Exception as e:
            _log(f"✗ 未知错误: {e}")
            return {"success": False, "message": f"连接错误: {str(e)}"}
        finally:
            client.close()
