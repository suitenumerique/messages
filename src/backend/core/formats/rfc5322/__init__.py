"""
RFC5322 email format package.

This package provides functionality for parsing and handling email
content according to RFC5322 standards.
"""

from .composer import (
    EmailComposeError,
    compose_email,
    create_reply_message,
    format_address,
    format_address_list,
)
from .parser import (
    EmailParseError,
    decode_email_header_text,
    parse_date,
    parse_email_address,
    parse_email_addresses,
    parse_email_message,
)

__all__ = [
    # Parser functions
    "parse_email_address",
    "parse_email_addresses",
    "parse_email_message",
    "parse_date",
    "decode_email_header_text",
    "EmailParseError",
    # Composer functions
    "format_address",
    "format_address_list",
    "compose_email",
    "create_reply_message",
    "EmailComposeError",
]
