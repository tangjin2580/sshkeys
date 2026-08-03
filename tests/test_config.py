"""
Tests for Configuration Module
"""

import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestConfigModule:
    """Tests for config module with mocked filesystem"""

    def test_default_values(self):
        """Test that default values are correctly defined"""
        # Import after patching to avoid file operations
        with patch.dict('sys.modules', {'modules.config': MagicMock()}):
            # Test defaults are accessible
            from modules import config
            # This will use actual defaults if module is already loaded
            pass

    def test_get_with_default(self):
        """Test get() returns default for missing keys"""
        # Create a fresh config module instance for testing
        import importlib
        import sys
        
        # Remove cached module if exists
        if 'modules.config' in sys.modules:
            del sys.modules['modules.config']
        
        # Mock the config file to not exist
        with patch.object(Path, 'exists', return_value=False):
            with patch.object(Path, 'mkdir'):
                with patch.object(Path, 'write_text'):
                    from modules.config import _DEFAULTS, get
                    
                    # get should return default for non-existent key
                    result = get("nonexistent_key", "default_value")
                    assert result == "default_value"
                    
                    # get should return cached default
                    result = get("sftp_max_download_mb", 999)
                    # Default is 100
                    assert result == 100

    def test_flush_config_function_exists(self):
        """Test that flush_config function exists"""
        from modules.config import flush_config
        assert callable(flush_config)

    def test_load_config_function_exists(self):
        """Test that load_config function exists"""
        from modules.config import load_config
        assert callable(load_config)

    def test_set_function_exists(self):
        """Test that set function exists"""
        from modules.config import set
        assert callable(set)


class TestConfigDefaults:
    """Tests for configuration defaults"""

    def test_sftp_max_download_mb_default(self):
        """Test SFTP download limit default is 100 MB"""
        from modules.config import _DEFAULTS
        assert _DEFAULTS["sftp_max_download_mb"] == 100

    def test_server_host_default(self):
        """Test server host default is localhost"""
        from modules.config import _DEFAULTS
        assert _DEFAULTS["server_host"] == "127.0.0.1"

    def test_server_port_default(self):
        """Test server port default is 5201"""
        from modules.config import _DEFAULTS
        assert _DEFAULTS["server_port"] == 5201

    def test_config_file_path(self):
        """Test config file path is in .ssh directory"""
        from modules.config import _CONFIG_FILE
        assert ".ssh" in str(_CONFIG_FILE)
        assert _CONFIG_FILE.name == "sshkeys-config.json"


class TestSftpBytesConversion:
    """Tests for SFTP bytes conversion"""

    def test_get_sftp_max_download_bytes(self):
        """Test conversion from MB to bytes"""
        from modules.config import get_sftp_max_download_bytes, _DEFAULTS
        
        # Default should be 100 MB = 104857600 bytes
        expected = _DEFAULTS["sftp_max_download_mb"] * 1024 * 1024
        result = get_sftp_max_download_bytes()
        assert result == expected
