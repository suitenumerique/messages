"""jmap-email: a strict-JMAP RFC 8621 Email object library.

Parse raw RFC 5322 bytes into a JMAP Email object dict, compose
JMAP Email object dicts into strict RFC 5322 bytes. Zero runtime
dependencies. Hardened against the documented CVE / research
attack classes — see the README.

Quick start::

    import jmap_email

    email = jmap_email.parse_email(raw_bytes)
    raw = jmap_email.compose_email(email)
    reply = jmap_email.make_reply(email, body_text="thanks")

Versioning: semantic. Public API is everything exported below; anything
prefixed with ``_`` is internal.
"""

__version__ = "0.1.0"

from .composer import (
    AttachmentError,
    ComposeError,
    HeaderInjectionError,
    InvalidAddressError,
    InvalidDateError,
    InvalidMessageIdError,
    compose_email,
    format_address,
    format_address_list,
    make_forward,
    make_reply,
    reply_subject,
)
from .parser import (
    ParseError,
    decode_rfc2047_header,
    parse_address,
    parse_addresses,
    parse_date,
    parse_email,
    parse_headers,
)
from .types import (
    Attachment,
    EmailAddress,
    EmailBodyPart,
    EmailBodyValue,
    EmailHeader,
    JmapEmail,
    JmapEmailExt,
)
from .utils import (
    extract_inline_images_html,
    extract_inline_images_text,
    remove_mime_headers,
)

__all__ = [
    # Wire-format pair
    "parse_email",
    "parse_headers",
    "compose_email",
    # Object construction helpers
    "make_reply",
    "make_forward",
    # Field-level parsers
    "parse_address",
    "parse_addresses",
    "parse_date",
    "decode_rfc2047_header",
    # Formatters
    "format_address",
    "format_address_list",
    "reply_subject",
    # HTML / MIME helpers
    "extract_inline_images_html",
    "extract_inline_images_text",
    "remove_mime_headers",
    # Errors
    "ParseError",
    "ComposeError",
    "InvalidAddressError",
    "InvalidMessageIdError",
    "InvalidDateError",
    "AttachmentError",
    "HeaderInjectionError",
    # JMAP RFC 8621 type shapes
    "Attachment",
    "EmailAddress",
    "EmailBodyPart",
    "EmailBodyValue",
    "EmailHeader",
    "JmapEmail",
    "JmapEmailExt",
    # Package version
    "__version__",
]
