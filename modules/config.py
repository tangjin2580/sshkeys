"""
Shared Configuration - Used by main.py (GUI) and webssh_routes.py (server)
All modifications take effect immediately without restart.
Uses deferred writes to reduce disk I/O.
"""

import json
import threading
import time
import atexit
from pathlib import Path
from typing import Any, Optional

# Config file path: ~/.ssh/sshkeys-config.json
_CONFIG_DIR = Path.home() / ".ssh"
_CONFIG_FILE = _CONFIG_DIR / "sshkeys-config.json"

# Defaults
_DEFAULTS = {
    "sftp_max_download_mb": 100,   # SFTP single file download limit (MB)
    "server_host": "127.0.0.1",
    "server_port": 5201,
}

# Runtime cache (avoid reading file every time)
_cache: dict[str, Any] = dict(_DEFAULTS)

# Deferred write settings
_dirty = False
_dirty_lock = threading.Lock()
_FLUSH_INTERVAL = 5.0  # Seconds to wait before flushing to disk
_flush_deadline = 0.0  # Time when next flush should occur


def _ensure_config() -> dict[str, Any]:
    """Ensure config file exists, return loaded config dict."""
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            # Merge defaults (prevent old configs missing new fields)
            for k, v in _DEFAULTS.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            pass
    # File doesn't exist or read failed -> write defaults
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(_DEFAULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    return dict(_DEFAULTS)


def load_config() -> dict[str, Any]:
    """Load config from file into cache and return."""
    global _cache, _dirty
    _cache = _ensure_config()
    _dirty = False
    return _cache


def get(key: str, default: Any = None) -> Any:
    """Read config value (prefer cache, refresh from file if missing)."""
    global _cache
    if key not in _cache:
        load_config()
    return _cache.get(key, default)


def set(key: str, value: Any) -> None:
    """Write config value (update cache + schedule disk write)."""
    global _dirty, _flush_deadline
    with _dirty_lock:
        _cache[key] = value
        _dirty = True
        _flush_deadline = time.time() + _FLUSH_INTERVAL
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _maybe_flush() -> None:
    """Flush config to disk if dirty and flush deadline has passed."""
    global _dirty
    with _dirty_lock:
        if not _dirty:
            return
        if time.time() < _flush_deadline:
            return
        _dirty = False
    
    try:
        _CONFIG_FILE.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # Re-mark as dirty if write failed
        with _dirty_lock:
            _dirty = True


def flush_config() -> None:
    """Force immediate flush of config to disk."""
    global _dirty
    with _dirty_lock:
        if not _dirty:
            return
        _dirty = False
    
    try:
        _CONFIG_FILE.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # Re-mark as dirty if write failed
        with _dirty_lock:
            _dirty = True
        raise


def _atexit_handler() -> None:
    """Ensure config is flushed on exit."""
    flush_config()


# Register exit handler
atexit.register(_atexit_handler)


def get_sftp_max_download_bytes() -> int:
    """Return SFTP download limit in bytes."""
    mb = get("sftp_max_download_mb", 100)
    return mb * 1024 * 1024
