"""
Tests for SSH Key Generator Module
"""

import pytest
from modules.key_generator import SSHKeyGenerator, KEY_TYPES, compute_fingerprint


class TestComputeFingerprint:
    """Tests for fingerprint computation"""

    def test_ed25519_fingerprint_format(self):
        """Test that Ed25519 fingerprint has correct format"""
        # Generate a real key and use its public key
        _, pub, _, _ = SSHKeyGenerator.generate_key_pair(key_type="ed25519")
        fp = compute_fingerprint(pub)
        assert fp.startswith("SHA256:")
        assert len(fp) > 10

    def test_rsa_fingerprint_format(self):
        """Test that RSA fingerprint has correct format"""
        # Generate a real key and use its public key
        _, pub, _, _ = SSHKeyGenerator.generate_key_pair(key_type="rsa", key_size=2048)
        fp = compute_fingerprint(pub)
        assert fp.startswith("SHA256:")

    def test_ecdsa_fingerprint_format(self):
        """Test that ECDSA fingerprint has correct format"""
        _, pub, _, _ = SSHKeyGenerator.generate_key_pair(key_type="ecdsa", curve="secp256r1")
        fp = compute_fingerprint(pub)
        assert fp.startswith("SHA256:")

    def test_empty_string_returns_empty(self):
        """Test that empty input returns empty string"""
        assert compute_fingerprint("") == ""

    def test_single_word_returns_empty(self):
        """Test that single word input returns empty string"""
        assert compute_fingerprint("invalid") == ""


class TestKeyTypes:
    """Tests for key types configuration"""

    def test_ed25519_is_available(self):
        """Test that Ed25519 (recommended) is available"""
        assert "Ed25519 (Recommended)" in KEY_TYPES

    def test_ed25519_config(self):
        """Test Ed25519 configuration"""
        assert KEY_TYPES["Ed25519 (Recommended)"]["type"] == "ed25519"
        assert KEY_TYPES["Ed25519 (Recommended)"]["size"] == 256

    def test_ecdsa_available(self):
        """Test ECDSA options are available"""
        assert "ECDSA P-256" in KEY_TYPES
        assert "ECDSA P-384" in KEY_TYPES
        assert "ECDSA P-521" in KEY_TYPES

    def test_rsa_max_4096(self):
        """Test RSA is limited to 4096 bits (no 8192)"""
        assert "RSA 4096 (Max)" in KEY_TYPES
        assert "RSA 8192" not in KEY_TYPES

    def test_no_dsa(self):
        """Test DSA is not available (deprecated)"""
        for key_name, config in KEY_TYPES.items():
            assert config.get("type") != "dsa", f"DSA found in {key_name}"


class TestSSHKeyGenerator:
    """Tests for SSH key generation"""

    def test_generate_ed25519_key(self):
        """Test Ed25519 key generation"""
        priv, pub, priv_bytes, pub_bytes = SSHKeyGenerator.generate_key_pair(
            key_type="ed25519",
            comment="test@example.com"
        )
        assert priv.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert pub.startswith("ssh-ed25519 ")
        assert "test@example.com" in pub
        assert len(priv_bytes) > 0
        assert len(pub_bytes) > 0

    def test_generate_ed25519_with_passphrase(self):
        """Test Ed25519 key generation with passphrase"""
        priv, pub, priv_bytes, pub_bytes = SSHKeyGenerator.generate_key_pair(
            key_type="ed25519",
            passphrase="testpassword123",
            comment="test@example.com"
        )
        assert priv.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        priv_no_pass, _, _, _ = SSHKeyGenerator.generate_key_pair(
            key_type="ed25519",
            comment="test@example.com"
        )
        assert len(priv) > len(priv_no_pass)

    def test_generate_ecdsa_p256(self):
        """Test ECDSA P-256 key generation"""
        priv, pub, priv_bytes, pub_bytes = SSHKeyGenerator.generate_key_pair(
            key_type="ecdsa",
            curve="secp256r1"
        )
        assert priv.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert pub.startswith("ecdsa-sha2-nistp256 ")

    def test_generate_ecdsa_p384(self):
        """Test ECDSA P-384 key generation"""
        priv, pub, _, _ = SSHKeyGenerator.generate_key_pair(
            key_type="ecdsa",
            curve="secp384r1"
        )
        assert pub.startswith("ecdsa-sha2-nistp384 ")

    def test_generate_ecdsa_p521(self):
        """Test ECDSA P-521 key generation"""
        priv, pub, _, _ = SSHKeyGenerator.generate_key_pair(
            key_type="ecdsa",
            curve="secp521r1"
        )
        assert pub.startswith("ecdsa-sha2-nistp521 ")

    def test_generate_rsa_2048(self):
        """Test RSA 2048 key generation"""
        priv, pub, _, _ = SSHKeyGenerator.generate_key_pair(
            key_type="rsa",
            key_size=2048
        )
        assert priv.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert pub.startswith("ssh-rsa ")

    def test_generate_rsa_4096(self):
        """Test RSA 4096 key generation"""
        priv, pub, _, _ = SSHKeyGenerator.generate_key_pair(
            key_type="rsa",
            key_size=4096
        )
        assert priv.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert pub.startswith("ssh-rsa ")

    def test_generate_rsa_exceeds_max_raises_error(self):
        """Test that RSA key size exceeding maximum raises ValueError"""
        with pytest.raises(ValueError) as exc_info:
            SSHKeyGenerator.generate_key_pair(
                key_type="rsa",
                key_size=8192
            )
        assert "exceeds maximum" in str(exc_info.value)
        assert "4096" in str(exc_info.value)

    def test_unsupported_key_type_raises_error(self):
        """Test that unsupported key type raises ValueError"""
        with pytest.raises(ValueError) as exc_info:
            SSHKeyGenerator.generate_key_pair(
                key_type="dsa"
            )
        assert "Unsupported key type" in str(exc_info.value)

    def test_fingerprint_consistency(self):
        """Test that same public key always produces same fingerprint"""
        _, pub, _, _ = SSHKeyGenerator.generate_key_pair(key_type="ed25519")
        fp1 = compute_fingerprint(pub)
        fp2 = compute_fingerprint(pub)
        assert fp1 == fp2

    def test_different_keys_produce_different_fingerprints(self):
        """Test that different keys produce different fingerprints"""
        _, pub1, _, _ = SSHKeyGenerator.generate_key_pair(key_type="ed25519")
        _, pub2, _, _ = SSHKeyGenerator.generate_key_pair(key_type="ed25519")
        fp1 = compute_fingerprint(pub1)
        fp2 = compute_fingerprint(pub2)
        assert fp1 != fp2
