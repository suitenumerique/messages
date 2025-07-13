"""Handles DKIM signing of email messages."""

import base64
import logging
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dkim import sign as dkim_sign

from core.enums import DKIMAlgorithmChoices

logger = logging.getLogger(__name__)


def generate_dkim_key(
    algorithm: DKIMAlgorithmChoices = DKIMAlgorithmChoices.RSA, key_size: int = 2048
) -> tuple[str, str]:
    """Generate a new DKIM key pair.

    Args:
        algorithm: The signing algorithm (DKIMAlgorithmChoices)
        key_size: The key size in bits (e.g., 2048, 4096 for RSA)

    Returns:
        Tuple of (private_key_pem, public_key_base64)

    Raises:
        ValueError: If the algorithm is not supported
    """

    if algorithm != DKIMAlgorithmChoices.RSA:
        raise ValueError(
            f"Unsupported algorithm: {algorithm}. Only RSA is currently supported."
        )

    # Generate RSA private key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)

    # Convert private key to PEM format
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    # Extract public key for DNS records
    public_key_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_b64 = base64.b64encode(public_key_der).decode("ascii")

    return private_key_pem, public_key_b64


def sign_message_dkim(raw_mime_message: bytes, maildomain) -> Optional[bytes]:
    """Sign a raw MIME message with DKIM.

    Uses the most recent active DKIM key for the domain.
    Only signs if the domain has an active DKIM key configured.

    Args:
        raw_mime_message: The raw bytes of the MIME message.
        maildomain: The MailDomain object with DKIM key.

    Returns:
        The DKIM-Signature header bytes if signed, otherwise None.
    """
    domain = maildomain.name

    # Find the most recent active DKIM key for this domain
    dkim_key = maildomain.get_active_dkim_key()

    if not dkim_key:
        logger.warning(
            "Domain %s has no active DKIM key configured, skipping DKIM signing", domain
        )
        return None

    try:
        dkim_private_key = dkim_key.get_private_key_bytes()

        signature = dkim_sign(
            message=raw_mime_message,
            selector=dkim_key.selector.encode("ascii"),
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
            signature.split(b"\r\n\r\n", 1)[0].split(b"DKIM-Signature:")[1].strip()
        )
        logger.info(
            "Successfully signed message for domain %s with selector %s",
            domain,
            dkim_key.selector,
        )
        return b"DKIM-Signature: " + signature_header
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error during DKIM signing for domain %s: %s", domain, e)
        return None
