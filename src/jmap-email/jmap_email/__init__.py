"""jmap-email: a strict-JMAP RFC 8621 Email object library.

Parse raw RFC 5322 bytes into a JMAP Email object dict, compose
JMAP Email object dicts into strict RFC 5322 bytes. Zero runtime
dependencies. Hardened against the documented CVE / research
attack classes — see the README.

Quick start::

    import jmap_email

    email = jmap_email.parse_email(raw_bytes)
    raw = jmap_email.compose_email(email)

Versioning: semantic. Public API is everything exported below; anything
prefixed with ``_`` is internal.
"""

__version__ = "0.2.0"

# The RFC 8621 ``TypedDict`` shapes are annotation-only and live in their
# own namespace — ``from jmap_email.types import JmapEmail`` — rather than
# cluttering the top-level (runtime) API. Re-export the submodule itself so
# ``jmap_email.types`` is always reachable and recognised as public.
from . import types as types
from .addresses import is_valid_addr_spec
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
    is_valid_msg_id,
)
from .filenames import sanitize_filename
from .helpers import (
    body_part_text,
    body_text_joined,
    find_header,
    find_headers,
    first_address,
    first_address_email,
    first_address_name,
    first_msgid,
    has_header,
    msgid_chain,
    now_sent_at,
    sent_at_to_datetime,
)
from .options import (
    DEFAULT_COMPOSE_OPTIONS,
    DEFAULT_PARSE_OPTIONS,
    ComposeOptions,
    ParseOptions,
)
from .parser import (
    decode_rfc2047_header,
    parse_address,
    parse_addresses,
    parse_date,
    parse_email,
)
from .preview import preview_text

__all__ = [
    # Wire-format pair
    "parse_email",
    "compose_email",
    # Field-level parsers
    "parse_address",
    "parse_addresses",
    "parse_date",
    "decode_rfc2047_header",
    # Formatters
    "format_address",
    "format_address_list",
    # Validators
    "is_valid_msg_id",
    "is_valid_addr_spec",
    # Attachment filename hygiene (applied by the parser to every part
    # name it reports; public for names that never went through it)
    "sanitize_filename",
    # Null-safe shape accessors
    "first_address",
    "first_address_email",
    "first_address_name",
    "first_msgid",
    "msgid_chain",
    "now_sent_at",
    "sent_at_to_datetime",
    "find_header",
    "find_headers",
    "has_header",
    "body_part_text",
    "body_text_joined",
    "preview_text",
    # Per-call resource caps
    "ParseOptions",
    "DEFAULT_PARSE_OPTIONS",
    "ComposeOptions",
    "DEFAULT_COMPOSE_OPTIONS",
    # Errors (compose-side only; parse_email returns None on failure)
    "ComposeError",
    "InvalidAddressError",
    "InvalidMessageIdError",
    "InvalidDateError",
    "AttachmentError",
    "HeaderInjectionError",
    # JMAP RFC 8621 type shapes live in the ``jmap_email.types`` submodule
    "types",
    # Package version
    "__version__",
]
