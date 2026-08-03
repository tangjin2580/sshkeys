"""
Tests for Utils Module
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock


class TestSafeChmod:
    """Tests for safe_chmod function"""

    def test_safe_chmod_exists(self):
        """Test that safe_chmod function exists"""
        from modules.utils import safe_chmod
        assert callable(safe_chmod)

    def test_safe_chmod_on_unix(self):
        """Test that safe_chmod calls os.chmod on Unix"""
        from modules.utils import safe_chmod
        
        if sys.platform.startswith("win"):
            pytest.skip("Test only for Unix-like systems")
        
        with patch('os.chmod') as mock_chmod:
            safe_chmod("/tmp/test", 0o600)
            mock_chmod.assert_called_once_with("/tmp/test", 0o600)

    def test_safe_chmod_on_windows(self):
        """Test that safe_chmod does nothing on Windows"""
        from modules.utils import safe_chmod
        
        if not sys.platform.startswith("win"):
            pytest.skip("Test only for Windows")
        
        # Should not raise any exception
        safe_chmod("C:\\test", 0o600)


class TestISWindows:
    """Tests for IS_WINDOWS constant"""

    def test_is_windows_exists(self):
        """Test that IS_WINDOWS constant exists"""
        from modules.utils import IS_WINDOWS
        assert isinstance(IS_WINDOWS, bool)

    def test_is_windows_value(self):
        """Test IS_WINDOWS matches platform"""
        from modules.utils import IS_WINDOWS
        assert IS_WINDOWS == (sys.platform == "win32")
