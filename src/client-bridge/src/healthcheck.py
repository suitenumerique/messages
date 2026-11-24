"""Container healthcheck: exit 0 when the enabled IMAP/SMTP listeners accept connections."""

import socket
import sys

from src import settings


def main() -> int:
    """Check every enabled listener."""
    ports = []
    if settings.ENABLE_IMAP:
        ports.append(settings.IMAP_PORT)
    if settings.ENABLE_SMTP:
        ports.append(settings.SMTP_PORT)
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                pass
        except OSError:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
