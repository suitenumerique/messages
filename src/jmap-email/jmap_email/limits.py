"""Per-call resource caps for :func:`jmap_email.parse_email` and
:func:`jmap_email.parse_addresses`.

The parser enforces hard caps against adversarial input — MIME-bomb
nesting, multipart-flood part counts, gigabyte address-lists, etc. A
single global default is wrong for any process that hosts more than
one workload: bulk-archive importers want larger caps than hot-path
SMTP gateways, and a global override on a shared module would leak
across threads.

:class:`ParseLimits` is a frozen, hashable bundle of caps that callers
pass to ``parse_email(..., limits=...)``. The default
:data:`DEFAULT_PARSE_LIMITS` instance is used when no value is
supplied; it carries the values that ship as the module-level
``MAX_*`` constants on :mod:`jmap_email.parser` so existing call sites
behave identically.

Construct a custom set inline at the call site::

    from jmap_email import ParseLimits, parse_email

    bulk = ParseLimits(max_mime_parts=5000, max_mime_nesting_depth=200)
    parse_email(raw, limits=bulk)

Or replace one cap on the default by ``dataclasses.replace``::

    from dataclasses import replace
    from jmap_email import DEFAULT_PARSE_LIMITS, parse_email

    parse_email(raw, limits=replace(DEFAULT_PARSE_LIMITS, max_mime_parts=500))
"""

from dataclasses import dataclass

__all__ = ["DEFAULT_PARSE_LIMITS", "ParseLimits"]


@dataclass(frozen=True, slots=True)
class ParseLimits:
    """Hard caps applied during a parse call.

    Pass an instance to :func:`jmap_email.parse_email` or
    :func:`jmap_email.parse_addresses` via the ``limits=`` keyword.
    Excess input is silently truncated and a WARNING is logged.

    Attributes
    ----------
    max_mime_nesting_depth : int
        Maximum depth of nested ``multipart/*`` containers walked by
        the body-tree parser. Sourced from Postfix's
        ``mime_nesting_limit``. Default: 100.
    max_mime_parts : int
        Maximum total number of MIME parts (leaves + multipart roots)
        visited during the body-tree walk. Caps memory blow-up on
        flat ``multipart/mixed`` inputs with millions of children.
        Sourced from Go's ``multipartmaxparts``. Default: 1000.
    max_header_value_bytes : int
        Maximum byte-length of a single header value retained for
        downstream processing. Values above this size are truncated
        before the stdlib's ``_header_value_parser`` runs — guards
        against the quadratic-time hot spots reported in gh-136063.
        Sourced from Postfix's ``header_size_limit``. Default: 102 400.
    max_address_list_bytes : int
        Maximum byte-length of an address-list value handed to
        :func:`jmap_email.parse_addresses`. Cap protects against the
        Dovecot CVE-2024-23184 class of unbounded-allocation
        attacks. Default: 100 000.
    """

    max_mime_nesting_depth: int = 100
    max_mime_parts: int = 1000
    max_header_value_bytes: int = 102_400
    max_address_list_bytes: int = 100_000


DEFAULT_PARSE_LIMITS = ParseLimits()
