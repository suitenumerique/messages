"""Per-call parse options for :func:`jmap_email.parse_email` and
:func:`jmap_email.parse_addresses`.

Most options are hard caps against adversarial input — MIME-bomb
nesting, multipart-flood part counts, gigabyte address-lists, etc. — but
the bundle also carries format policy the RFC leaves to the server, such
as the ``preview`` length. A single global default is wrong for any
process that hosts more than one workload: bulk-archive importers want
larger caps than hot-path SMTP gateways, and a global override on a
shared module would leak across threads.

:class:`ParseOptions` is a frozen, hashable bundle of these knobs that
callers pass to ``parse_email(..., options=...)``. The default
:data:`DEFAULT_PARSE_OPTIONS` instance is used when no value is
supplied; it carries the values that ship as the module-level
``MAX_*`` constants on :mod:`jmap_email.parser` so existing call sites
behave identically.

Construct a custom set inline at the call site::

    from jmap_email import ParseOptions, parse_email

    bulk = ParseOptions(max_mime_parts=5000, max_mime_nesting_depth=200)
    parse_email(raw, options=bulk)

Or replace one cap on the default by ``dataclasses.replace``::

    from dataclasses import replace
    from jmap_email import DEFAULT_PARSE_OPTIONS, parse_email

    parse_email(raw, options=replace(DEFAULT_PARSE_OPTIONS, max_mime_parts=500))
"""

from dataclasses import dataclass

__all__ = ["DEFAULT_PARSE_OPTIONS", "ParseOptions"]


@dataclass(frozen=True, slots=True)
class ParseOptions:
    """Per-call parse options: resource caps plus server-set format policy.

    Pass an instance to :func:`jmap_email.parse_email` or
    :func:`jmap_email.parse_addresses` via the ``options=`` keyword.
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
    max_preview_chars : int
        Maximum length (in Unicode characters, per RFC 8621 §4.1.4,
        which counts *characters* not octets) of the server-set
        ``preview`` excerpt. The parser cleans the source (HTML +
        markdown strip) before truncating, so leading syntax never
        eats the budget. 256 is the RFC ceiling — a ``MUST NOT``
        exceed, so this is a hard cap, not a floor; callers wanting a
        shorter preview (e.g. a list-view snippet) may lower it.
        Default: 256.
    max_preview_scan_bytes : int
        Maximum length of body text scanned to build the ``preview``. The
        preview extractor stops itself once it has enough *visible text*,
        but a body that is almost entirely markup (few text nodes) never
        reaches that budget, so this hard cap bounds the work on such
        adversarial input — a peer of the caps above. Applied as a
        character count (≈ bytes for typical mail). Default: 131 072
        (128 KiB); legitimate mail carries visible text far sooner.
    """

    max_mime_nesting_depth: int = 100
    max_mime_parts: int = 1000
    max_header_value_bytes: int = 102_400
    max_address_list_bytes: int = 100_000
    # 256 = RFC 8621 §4.1.4 ceiling for ``preview`` (a hard MUST NOT).
    max_preview_chars: int = 256
    max_preview_scan_bytes: int = 128 * 1024


DEFAULT_PARSE_OPTIONS = ParseOptions()
