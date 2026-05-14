"""
Web admin authentication: bcrypt + Starlette session cookies.

The admin account is configured via environment variables (see
`src.config.Settings.admin_username` and `admin_password_hash`) — not
stored in the database. This is deliberate: the bootstrap admin should
work even if the DB is corrupted or empty, so an operator can always
recover.

The session itself is signed (via Starlette's `SessionMiddleware` +
`itsdangerous`) and contains only the admin username plus a CSRF token.
No secrets travel in the cookie body.
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request, status
from starlette.middleware.sessions import SessionMiddleware

from src.config import Settings

logger = logging.getLogger(__name__)

SESSION_KEY_USERNAME = "admin_username"
SESSION_KEY_CSRF = "csrf_token"

# bcrypt's default cost is 12 (~250ms on a Pi 4); this is appropriate
# for login flows. Don't raise it without measuring on target hardware.
_BCRYPT_ROUNDS = 12


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of `plaintext` suitable for storing in config."""
    if not plaintext:
        raise ValueError("password must not be empty")
    return bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt(_BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    """Constant-time bcrypt password comparison."""
    if not plaintext or not hashed:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash; treat as failure (don't leak details).
        logger.warning("verify_password: malformed hash")
        return False


def verify_credentials(
    username: str, password: str, settings: Settings
) -> bool:
    """Check submitted login credentials against the configured admin.

    Uses `secrets.compare_digest` for username comparison so the
    response time doesn't leak whether the username matched.
    """
    expected_user = settings.admin_username
    user_ok = secrets.compare_digest(
        username.encode("utf-8"), expected_user.encode("utf-8")
    )
    pass_ok = verify_password(
        password, settings.admin_password_hash.get_secret_value()
    )
    # Always do BOTH checks (don't short-circuit) so timing is uniform.
    return user_ok and pass_ok


# =============================================================================
# Session helpers
# =============================================================================


def login_session(request: Request, username: str) -> str:
    """Mark the session as logged in and return a fresh CSRF token.

    The CSRF token must be embedded in every state-changing form so the
    server can confirm the form was rendered by us.
    """
    csrf_token = secrets.token_urlsafe(32)
    request.session[SESSION_KEY_USERNAME] = username
    request.session[SESSION_KEY_CSRF] = csrf_token
    logger.info("Admin %s logged in", username)
    return csrf_token


def logout_session(request: Request) -> None:
    """Clear all session state."""
    request.session.clear()


def current_admin(request: Request) -> Optional[str]:
    """Return the logged-in admin's username, or None if not logged in."""
    return request.session.get(SESSION_KEY_USERNAME)


def require_admin(request: Request) -> str:
    """FastAPI dependency that enforces an authenticated admin session.

    Raises:
        HTTPException 401 if no admin is logged in. For HTML pages this
        is caught upstream and turned into a redirect to /login; for the
        JSON API the 401 is returned as-is.
    """
    username = current_admin(request)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Session"},
        )
    return username


def verify_csrf(request: Request, submitted_token: str) -> bool:
    """Constant-time check of a submitted CSRF token against the session."""
    expected = request.session.get(SESSION_KEY_CSRF, "")
    if not expected or not submitted_token:
        return False
    return secrets.compare_digest(expected, submitted_token)


def install_session_middleware(app, settings: Settings) -> None:
    """Attach Starlette's session middleware using the configured secret.

    Cookies are httponly + samesite=lax — sufficient for an admin UI
    that doesn't accept cross-site embedding. `secure=True` is omitted
    so local development over plain HTTP works; production deployments
    behind TLS should set the cookie's secure flag at the reverse proxy.
    """
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie="rpi_rfid_session",
        same_site="lax",
        https_only=False,
    )
