"""
SSH 密钥生成模块
支持 Ed25519 / Ed448 / ECDSA / RSA 全系列，覆盖旧版到最新版本
"""

import os
import base64
import hashlib
import logging
from typing import Tuple, Optional

from cryptography.hazmat.primitives import serialization as crypto_serialization
from cryptography.hazmat.primitives.asymmetric import rsa, dsa, ed25519, ec
from cryptography.hazmat.backends import default_backend

from modules.utils import safe_chmod

logger = logging.getLogger(__name__)


def compute_fingerprint(pub_str: str) -> str:
    """
    计算 OpenSSH 格式公钥的指纹（SHA256，与 ssh-keygen -lf 输出一致）
    pub_str: OpenSSH 格式公钥字符串，如 "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... user@host"
    Returns: "SHA256:xxxx"
    """
    # 解析出 base64 部分
    parts = pub_str.strip().split()
    if len(parts) < 2:
        return ""
    b64data = parts[1]
    # base64 解码得到 wire format
    wire = base64.b64decode(b64data)
    # SHA256 计算摘要
    digest = hashlib.sha256(wire).digest()
    # 标准 base64 编码（非 urlsafe），去掉 padding
    fp_b64 = base64.b64encode(digest).rstrip(b"=").decode("ascii")
    return f"SHA256:{fp_b64}"

# 支持的密钥类型（供前端下拉框使用）
# type: 算法类型，size: 密钥位数，curve: ECDSA 曲线名（可选）
KEY_TYPES = {
    # --- Edwards 曲线（现代推荐） ---
    "Ed25519（推荐）":    {"type": "ed25519", "size": 256},

    # --- ECDSA ---
    "ECDSA P-256":        {"type": "ecdsa",   "size": 256, "curve": "secp256r1"},
    "ECDSA P-384":        {"type": "ecdsa",   "size": 384, "curve": "secp384r1"},
    "ECDSA P-521":        {"type": "ecdsa",   "size": 521, "curve": "secp521r1"},

    # --- RSA ---
    "RSA 1024（旧版兼容）": {"type": "rsa", "size": 1024},
    "RSA 2048":            {"type": "rsa", "size": 2048},
    "RSA 3072":            {"type": "rsa", "size": 3072},
    "RSA 4096":            {"type": "rsa", "size": 4096},
    "RSA 8192":            {"type": "rsa", "size": 8192},

    # --- DSA（已弃用，仅旧系统兼容） ---
    "DSA 1024（已弃用）":   {"type": "dsa", "size": 1024},
}


class SSHKeyGenerator:
    """SSH 密钥对生成器"""

    @staticmethod
    def generate_key_pair(
        key_type: str = "ed25519",
        key_size: int = 256,
        passphrase: Optional[str] = None,
        comment: str = "user@host",
        curve: Optional[str] = None,
    ) -> Tuple[str, str, bytes, bytes]:
        """
        生成 SSH 密钥对

        Args:
            key_type: 密钥类型 (ed25519 / ecdsa / rsa / dsa)
            key_size: 密钥位数
            passphrase: 可选私钥密码
            comment: 公钥注释
            curve: ECDSA 曲线名 (secp256r1 / secp384r1 / secp521r1)

        Returns:
            (private_key_str, public_key_str, private_key_bytes, public_key_bytes)
        """
        logger.info(f"开始生成 {key_type.upper()} 密钥 (bits={key_size})")

        # 1. 生成原始密钥
        if key_type == "ed25519":
            private_key = ed25519.Ed25519PrivateKey.generate()
            public_key = private_key.public_key()
        elif key_type == "ecdsa":
            # 曲线映射（NIST 三条标准曲线）
            curve_map = {
                "secp256r1": ec.SECP256R1(),
                "secp384r1": ec.SECP384R1(),
                "secp521r1": ec.SECP521R1(),
            }
            # 兼容旧接口：如果没有传 curve，用 key_size 反查
            if not curve:
                size_curve_map = {256: "secp256r1", 384: "secp384r1", 521: "secp521r1"}
                curve = size_curve_map.get(key_size, "secp256r1")
            selected_curve = curve_map.get(curve, ec.SECP256R1())
            private_key = ec.generate_private_key(selected_curve, default_backend())
            public_key = private_key.public_key()
        elif key_type == "rsa":
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=key_size,
                backend=default_backend(),
            )
            public_key = private_key.public_key()
        elif key_type == "dsa":
            # DSA 仅支持 1024 位（OpenSSH 限制），忽略 key_size 参数
            private_key = dsa.generate_private_key(
                key_size=1024,
                backend=default_backend(),
            )
            public_key = private_key.public_key()
        else:
            raise ValueError(f"不支持的密钥类型: {key_type}")

        # 2. 确定加密算法（如果有密码）
        if passphrase:
            encryption = crypto_serialization.BestAvailableEncryption(
                passphrase.encode("utf-8")
            )
        else:
            encryption = crypto_serialization.NoEncryption()

        # 3. 序列化私钥 → OpenSSH 格式
        private_key_bytes = private_key.private_bytes(
            encoding=crypto_serialization.Encoding.PEM,
            format=crypto_serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=encryption,
        )
        private_key_str = private_key_bytes.decode("utf-8")

        # 4. 序列化公钥 → OpenSSH 格式 (ssh-ed25519 AAAA... comment)
        public_key_bytes = public_key.public_bytes(
            encoding=crypto_serialization.Encoding.OpenSSH,
            format=crypto_serialization.PublicFormat.OpenSSH,
        )
        public_key_str = public_key_bytes.decode("utf-8").strip()
        # 追加注释（OpenSSH 格式：<type> <base64> <comment>）
        public_key_str_with_comment = f"{public_key_str} {comment}"

        logger.info(f"密钥生成成功: {key_type.upper()} / {key_size} bits")
        return (
            private_key_str,
            public_key_str_with_comment,
            private_key_bytes,
            public_key_bytes,
        )

    @staticmethod
    def save_key_files(
        private_key_str: str,
        public_key_str: str,
        private_path: str,
        public_path: str,
    ) -> None:
        """
        将密钥保存到磁盘，私钥自动设置 600 权限

        Args:
            private_key_str: 私钥 PEM 字符串
            public_key_str: 公钥 OpenSSH 字符串
            private_path: 私钥保存路径
            public_path:  公钥保存路径
        """
        # 确保目录存在
        os.makedirs(os.path.dirname(private_path), exist_ok=True)

        # 写入私钥
        with open(private_path, "w", encoding="utf-8") as f:
            f.write(private_key_str)
        # 设置权限：仅拥有者可读写 (0o600)
        safe_chmod(private_path, 0o600)

        # 写入公钥
        with open(public_path, "w", encoding="utf-8") as f:
            f.write(public_key_str + "\n")
        safe_chmod(public_path, 0o644)

        logger.info(f"密钥文件已保存: {private_path} / {public_path}")
