"""
SSH Key Generator Module
Supports Ed25519 / ECDSA / RSA series (modern recommended algorithms)
"""

import os
import base64
import hashlib
import logging
from typing import Tuple, Optional

from cryptography.hazmat.primitives import serialization as crypto_serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519, ec
from cryptography.hazmat.backends import default_backend

from modules.utils import safe_chmod

logger = logging.getLogger(__name__)


def compute_fingerprint(pub_str: str) -> str:
    """
    Compute fingerprint of OpenSSH format public key (SHA256, matches ssh-keygen -lf output)
    pub_str: OpenSSH format public key string, e.g. "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... user@host"
    Returns: "SHA256:xxxx"
    """
    # Parse base64 part
    parts = pub_str.strip().split()
    if len(parts) < 2:
        return ""
    b64data = parts[1]
    try:
        # base64 decode to wire format
        wire = base64.b64decode(b64data)
    except Exception:
        return ""
    # SHA256 hash
    digest = hashlib.sha256(wire).digest()
    # Standard base64 encoding (not urlsafe), strip padding
    fp_b64 = base64.b64encode(digest).rstrip(b"=").decode("ascii")
    return f"SHA256:{fp_b64}"

# Supported key types (for frontend dropdown)
# type: algorithm type, size: key bits, curve: ECDSA curve name (optional)
KEY_TYPES = {
    # --- Edwards curve (modern recommended) ---
    "Ed25519 (Recommended)": {"type": "ed25519", "size": 256},

    # --- ECDSA ---
    "ECDSA P-256":          {"type": "ecdsa", "size": 256, "curve": "secp256r1"},
    "ECDSA P-384":          {"type": "ecdsa", "size": 384, "curve": "secp384r1"},
    "ECDSA P-521":          {"type": "ecdsa", "size": 521, "curve": "secp521r1"},

    # --- RSA (limited to 4096 for security/performance balance) ---
    "RSA 2048":             {"type": "rsa", "size": 2048},
    "RSA 3072":             {"type": "rsa", "size": 3072},
    "RSA 4096 (Max)":       {"type": "rsa", "size": 4096},
}


class SSHKeyGenerator:
    """SSH Key Pair Generator"""

    # Maximum RSA key size for security/performance
    MAX_RSA_KEY_SIZE = 4096

    @staticmethod
    def generate_key_pair(
        key_type: str = "ed25519",
        key_size: int = 256,
        passphrase: Optional[str] = None,
        comment: str = "user@host",
        curve: Optional[str] = None,
    ) -> Tuple[str, str, bytes, bytes]:
        """
        Generate SSH key pair

        Args:
            key_type: Key type (ed25519 / ecdsa / rsa)
            key_size: Key size in bits
            passphrase: Optional private key password
            comment: Public key comment
            curve: ECDSA curve name (secp256r1 / secp384r1 / secp521r1)

        Returns:
            (private_key_str, public_key_str, private_key_bytes, public_key_bytes)

        Raises:
            ValueError: If key type is unsupported or key size exceeds maximum
        """
        logger.info(f"Generating {key_type.upper()} key (bits={key_size})")

        # Validate RSA key size
        if key_type == "rsa" and key_size > SSHKeyGenerator.MAX_RSA_KEY_SIZE:
            raise ValueError(
                f"RSA key size {key_size} exceeds maximum {SSHKeyGenerator.MAX_RSA_KEY_SIZE}. "
                "For security and performance, RSA keys are limited to 4096 bits."
            )

        # 1. Generate raw key
        if key_type == "ed25519":
            private_key = ed25519.Ed25519PrivateKey.generate()
            public_key = private_key.public_key()
        elif key_type == "ecdsa":
            # Curve mapping (NIST standard curves)
            curve_map = {
                "secp256r1": ec.SECP256R1(),
                "secp384r1": ec.SECP384R1(),
                "secp521r1": ec.SECP521R1(),
            }
            # Fallback: derive curve from key_size if not provided
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
        else:
            raise ValueError(f"Unsupported key type: {key_type}")

        # 2. Determine encryption (if password provided)
        if passphrase:
            encryption = crypto_serialization.BestAvailableEncryption(
                passphrase.encode("utf-8")
            )
        else:
            encryption = crypto_serialization.NoEncryption()

        # 3. Serialize private key -> OpenSSH PEM format
        private_key_bytes = private_key.private_bytes(
            encoding=crypto_serialization.Encoding.PEM,
            format=crypto_serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=encryption,
        )
        private_key_str = private_key_bytes.decode("utf-8")

        # 4. Serialize public key -> OpenSSH format (ssh-ed25519 AAAA... comment)
        public_key_bytes = public_key.public_bytes(
            encoding=crypto_serialization.Encoding.OpenSSH,
            format=crypto_serialization.PublicFormat.OpenSSH,
        )
        public_key_str = public_key_bytes.decode("utf-8").strip()
        # Append comment (OpenSSH format: <type> <base64> <comment>)
        public_key_str_with_comment = f"{public_key_str} {comment}"

        logger.info(f"Key generated successfully: {key_type.upper()} / {key_size} bits")
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
        Save keys to disk with proper permissions

        Args:
            private_key_str: Private key PEM string
            public_key_str: Public key OpenSSH string
            private_path: Private key save path
            public_path: Public key save path
        """
        # Ensure directory exists
        os.makedirs(os.path.dirname(private_path), exist_ok=True)

        # Write private key
        with open(private_path, "w", encoding="utf-8") as f:
            f.write(private_key_str)
        # Set permissions: owner read/write only (0o600)
        safe_chmod(private_path, 0o600)

        # Write public key
        with open(public_path, "w", encoding="utf-8") as f:
            f.write(public_key_str + "\n")
        safe_chmod(public_path, 0o644)

        logger.info(f"Key files saved: {private_path} / {public_path}")
