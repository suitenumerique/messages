"""Client-bridge settings loaded from environment variables."""

import os


def _env_bool(name: str, default: bool = True) -> bool:
    """Read a boolean from an environment variable."""
    val = os.environ.get(name, "").strip().lower()
    if val in ("0", "false", "no"):
        return False
    if val in ("1", "true", "yes"):
        return True
    return default


def _env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable with a clear error message."""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from None


MESSAGES_API_BASE_URL = os.environ.get("MESSAGES_API_BASE_URL", "http://localhost:8000/api/v1.0/")
CLIENTBRIDGE_API_SECRET = os.environ.get("CLIENTBRIDGE_API_SECRET", "")

ENABLE_IMAP = _env_bool("ENABLE_IMAP", default=True)
IMAP_HOST = os.environ.get("IMAP_HOST", "0.0.0.0")
IMAP_PORT = _env_int("IMAP_PORT", 143)

ENABLE_SMTP = _env_bool("ENABLE_SMTP", default=True)
SMTP_HOST = os.environ.get("SMTP_HOST", "0.0.0.0")
SMTP_PORT = _env_int("SMTP_PORT", 587)
# Idle timeout in seconds — aiosmtpd drops the connection after this much inactivity
SMTP_SESSION_TIMEOUT = _env_int("SMTP_SESSION_TIMEOUT", 300)
# Max DATA commands per session before aiosmtpd drops the connection
SMTP_MAX_MESSAGES_PER_SESSION = _env_int("SMTP_MAX_MESSAGES_PER_SESSION", 50)
