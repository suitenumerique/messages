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

__all__ = [
    "DEFAULT_COMPOSE_OPTIONS",
    "DEFAULT_PARSE_OPTIONS",
    "ComposeOptions",
    "ParseOptions",
]


@dataclass(frozen=True, slots=True)
class ParseOptions:
    """Per-call parse options: resource caps plus server-set format policy.

    Pass an instance to :func:`jmap_email.parse_email` or
    :func:`jmap_email.parse_addresses` via the ``options=`` keyword.
    Excess input is silently truncated and a WARNING is logged, except
    for ``max_header_value_bytes``, which **rejects** the message.

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
        Maximum octet-length of a single header value. A message
        carrying a longer field is **rejected** — ``parse_email``
        returns ``None`` — rather than truncated: there is no generally
        safe cut point for an arbitrary field, and a byte cut can
        manufacture a value that was never sent (a shortened address
        list re-parses to a different address). Also guards the
        quadratic-time hot spots reported in gh-136063.

        RFC 5322 §2.2.3 puts no limit on a field — folding makes it
        unbounded while every line stays legal — so this is local
        policy, sourced from Postfix's ``header_size_limit``. Postfix
        discards the excess; we refuse, because it is rewriting a header
        where we assert identity from one. Default: 102 400.
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


@dataclass(frozen=True, slots=True)
class ComposeOptions:
    """Per-call compose policy: what the composer is permitted to emit.

    Pass an instance to :func:`jmap_email.compose_email` via the
    ``options=`` keyword. Unlike :class:`ParseOptions`, which is mostly
    resource caps against hostile input, these are output-correctness
    choices — the composer is strict by design and each of these names a
    place where "strict" is genuinely deployment-dependent rather than
    universal.

    Per-*message* values (``in_reply_to``, ``prepend_headers``) stay
    keyword arguments on ``compose_email``: they change on every call,
    where everything here is a property of the call site and is set once.

    Two of these name ESMTP capabilities of the hop the bytes are
    headed for. The library does not discover those — the caller does,
    and how is the caller's business: a fixed relay whose configuration
    you own, an EHLO response you composed *after* reading, or two
    variants composed up front so the delivery loop can fall back when
    the server answers ``SMTPNotSupportedError``. See the README for
    that last pattern.

    Attributes
    ----------
    emit_bcc : bool
        When False, the ``Bcc:`` header is silently dropped — the entire
        point of Bcc is that it must NOT be transmitted to recipients.
        Set True only for archive reconstruction (e.g. PST import, where
        the Bcc list was already in the source file). Default: False.
    idna_encode_domains : bool
        When True, an addr-spec whose *domain* is non-ASCII is IDNA
        encoded to its A-label form on the wire
        (``contact@exemplé.fr`` → ``contact@xn--exempl-gva.fr``).

        Unlike the two flags below, this one names no ESMTP capability:
        an A-label is a plain ASCII domain, valid at every hop with no
        extension involved, and it is the only form the MX lookup can
        use. It is off by default because the composer does not rewrite
        an address the caller gave it unless asked — not because the
        wire might refuse it. Default: False.

        It governs the domain only. A non-ASCII **local part** needs
        ``allow_smtputf8``: punycode is a DNS algorithm and a local part is not
        a DNS label, so there is nothing to encode it to.
    allow_8bit : bool
        When True, non-ASCII bodies are emitted as raw 8-bit octets
        instead of being promoted to quoted-printable or base64. Requires
        the next hop to advertise 8BITMIME (RFC 6152); a relay without it
        either refuses the message or mangles it. Worth roughly the 33%
        base64 overhead on non-ASCII bodies, and leaves the message
        readable on the wire. Default: False.
    allow_smtputf8 : bool
        When True, headers are emitted as UTF-8 (RFC 6532) rather than
        RFC 2047 encoded-words, and a non-ASCII **local part** is
        permitted instead of raising. Requires the next hop to advertise
        SMTPUTF8 (RFC 6531) *and* the caller to put the ``SMTPUTF8``
        parameter on ``MAIL FROM``.

        There is no downgrade: RFC 6530 dropped the mechanism RFC 5504
        had specified, so a message whose mailbox names require UTF-8
        cannot be rewritten into an ASCII equivalent — if the hop lacks
        the extension, the message bounces. Support is also not
        transitive; a relay that accepts your transaction may forward to
        one that cannot. Compose the ASCII variant too if you need a
        fallback. Default: False.
    """

    emit_bcc: bool = False
    idna_encode_domains: bool = False
    allow_8bit: bool = False
    allow_smtputf8: bool = False

    @property
    def emits_8bit(self) -> bool:
        """True when the output may contain raw 8-bit octets.

        ``allow_smtputf8`` implies it: RFC 6532 headers are UTF-8, which is
        8-bit by construction, and RFC 6531 §3.1 requires any server
        advertising SMTPUTF8 to support 8BITMIME as well. Encoding the
        *body* down to 7-bit while emitting 8-bit *headers* would buy
        nothing.
        """
        return self.allow_8bit or self.allow_smtputf8


DEFAULT_COMPOSE_OPTIONS = ComposeOptions()
