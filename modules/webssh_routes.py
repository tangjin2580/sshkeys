"""
WebSSH HTTP Routes - Backward compatibility wrapper

Routes have been refactored into a Blueprint in modules/routes/webssh.py
This file provides the register_webssh_routes() function for backward compatibility.
"""

import logging
from modules.routes.webssh import webssh_bp

logger = logging.getLogger(__name__)


def register_webssh_routes(app):
    """
    Register WebSSH Blueprint routes.
    Called from server.py's create_app().
    """
    app.register_blueprint(webssh_bp)
    logger.info("[WebSSH] Blueprint routes registered")


# Re-export for backward compatibility
from modules.routes.webssh import webssh_bp
from modules.webssh_sessions import (
    cleanup_all_sessions,
    _close_ssh_session,
    _ssh_sessions,
    _ssh_lock,
)
