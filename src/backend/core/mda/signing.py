"""Handles DKIM signing of email messages."""

import base64
import logging
from typing import Optional

from django.conf import settings

from dkim import sign as dkim_sign

logger = logging.getLogger(__name__)


def sign_message_dkim(raw_mime_message: bytes, sender_email: str) -> Optional[bytes]:
    """Sign a raw MIME message with DKIM.

    Uses the private key and selector defined in Django settings.
    Only signs domains listed in settings.MESSAGES_DKIM_DOMAINS.

    Args:
        raw_mime_message: The raw bytes of the MIME message.
        sender_email: The email address of the sender (e.g., "user@example.com").

    Returns:
        The DKIM-Signature header bytes if signed, otherwise None.
    """

    dkim_private_key = None
    if settings.MESSAGES_DKIM_PRIVATE_KEY_FILE:
        try:
            with open(settings.MESSAGES_DKIM_PRIVATE_KEY_FILE, "rb") as f:
                dkim_private_key = f.read()
        except FileNotFoundError:
            logger.error(
                "DKIM private key file not found: %s",
                settings.MESSAGES_DKIM_PRIVATE_KEY_FILE,
            )
            return None
    elif settings.MESSAGES_DKIM_PRIVATE_KEY_B64:
        try:
            dkim_private_key = base64.b64decode(settings.MESSAGES_DKIM_PRIVATE_KEY_B64)
        except (TypeError, ValueError):
            logger.error("Failed to decode MESSAGES_DKIM_PRIVATE_KEY_B64.")
            return None

    if not dkim_private_key:
        logger.warning(
            "MESSAGES_DKIM_PRIVATE_KEY_B64/FILE is not set, skipping DKIM signing"
        )
        return None

    try:
        domain = sender_email.split("@")[1]
    except IndexError:
        logger.error("Invalid sender email format for DKIM signing: %s", sender_email)
        return None

    if domain not in settings.MESSAGES_DKIM_DOMAINS:
        logger.warning(
            "Domain %s is not in MESSAGES_DKIM_DOMAINS, skipping DKIM signing", domain
        )
        return None

    try:
        signature = dkim_sign(
            message=raw_mime_message,
            selector=settings.MESSAGES_DKIM_SELECTOR.encode("ascii"),
            domain=domain.encode("ascii"),
            privkey=dkim_private_key,
            include_headers=[
                b"To",
                b"Cc",
                b"From",
                b"Subject",
                b"Message-ID",
                b"Reply-To",
                b"In-Reply-To",
                b"References",
                b"Date",
            ],
            canonicalize=(b"relaxed", b"simple"),
        )
        # dkim_sign returns the full message including the signature header,
        # we only want the header itself.
        signature_header = (
            signature.split(b"\\r\\n\\r\\n", 1)[0].split(b"DKIM-Signature:")[1].strip()
        )
        return b"DKIM-Signature: " + signature_header
    except Exception as e:  # noqa: BLE001 pylint: disable=broad-exception-caught
        logger.error("Error during DKIM signing for domain %s: %s", domain, e)
        return None
