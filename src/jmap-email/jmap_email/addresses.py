"""Addr-spec shape validation, shared by the parser and the composer.

One mailbox, one predicate. The parse side uses it to decide whether
something the stdlib's lenient splitter surfaced is really an address;
the compose side uses it to refuse building a header out of one that
isn't. Keeping a single definition is the point — a value the parser
calls valid and the composer would mangle (or vice versa) is exactly
the gap an injection lives in.
"""

# Characters the composer strips from every header value on the way out
# (``_sanitize_header_value``). Defined here because this predicate has to
# agree with it: a value carrying one of these is not usable as it stands,
# whatever its shape. TAB is absent deliberately — the composer keeps it,
# and the whitespace rule below rejects it inside an addr-spec anyway.
STRIPPED_HEADER_CHARS = frozenset(
    [chr(c) for c in range(0x00, 0x20) if c != 0x09]
    + ["\x7f", "\u0085", "\u2028", "\u2029"]
)


# RFC 5322 §3.2.3 ``specials``, minus ``.`` which is the dot-atom
# separator. Outside a quoted-string or a domain-literal each of these
# ends whatever token a reader is in the middle of: ``(`` opens a comment
# that runs to the closing paren, ``:`` opens a group, ``[`` opens a
# domain-literal, ``\\`` escapes the next character. A value carrying one
# is therefore not one mailbox to everybody — ``a(b@c.co, victim@x.co``
# reads as *zero* addresses to a comment-aware parser, and ``a:b@c.co``
# reads as the group ``a`` containing ``b@c.co``.
_SPECIALS = frozenset('()<>[]:;@\\,"')

# RFC 5322 §3.4.1 ``dtext``: printable ASCII inside a domain-literal,
# excluding the framing brackets and the escape. The comma and the
# parens are legal dtext but are excluded anyway: this predicate
# promises one mailbox *to everybody*, and a lenient reader that does
# not track literal brackets cuts a mailbox-list at every comma and
# opens a comment at every ``(`` — ``email.utils.getaddresses``
# recovers **zero** mailboxes from ``x@[a,b], victim@x.co`` and from
# ``x@[a(b], victim@x.co`` alike, the exact reader disagreement
# described above.
_DTEXT_EXCLUDED = frozenset("[]\\,()")


def _is_valid_domain(domain: str) -> bool:
    """Return ``True`` when *domain* is a dot-atom or a domain-literal."""
    if domain.startswith("[") and domain.endswith("]") and len(domain) >= 2:
        inner = domain[1:-1]
        return not any(c.isspace() or c in _DTEXT_EXCLUDED for c in inner)
    return not any(c.isspace() or c in _SPECIALS for c in domain)


def is_valid_addr_spec(addr: str | None) -> bool:
    """Return ``True`` when *addr* is a single, well-formed addr-spec.

    RFC 5322 §3.4.1: ``local-part "@" domain``. The check that matters
    here is that it is **one** mailbox — an ``email`` value carrying a
    comma is two addresses, and a mailbox-list is built by joining on
    commas, so letting one through turns a single recipient into two.
    Whitespace is rejected for the same reason (a space separates a
    display name from an angle-addr), as are ``<>;`` and an empty
    local-part or domain.

    A quoted-string local-part (RFC 5322 §3.4.1 permits
    ``"john doe"@example.com``) may contain those characters, since the
    quoting is what keeps it one mailbox. Non-ASCII is accepted
    throughout: RFC 6531 addresses are valid and this is not the place
    to relitigate that.

    ``True`` means usable **as it stands**, so a value carrying a
    character the composer would strip on the way out — a line
    terminator, a control character — is rejected rather than reported
    valid and silently cleaned later. Same rule as
    :func:`jmap_email.is_valid_msg_id`: the caller keeps the raw string
    it validated, and may write it somewhere other than
    :func:`compose_email`.
    """
    if not addr or "@" not in addr:
        return False
    if any(c in STRIPPED_HEADER_CHARS for c in addr):
        return False
    local, _, domain = addr.rpartition("@")
    if not local or not domain:
        return False
    if not _is_valid_domain(domain):
        return False
    if len(local) >= 2 and local.startswith('"') and local.endswith('"'):
        return _is_terminated_quoted_string(local)
    return not any(c.isspace() or c in _SPECIALS for c in local)


def _is_terminated_quoted_string(local: str) -> bool:
    """Return ``True`` when *local* is one complete quoted-string.

    RFC 5322 §3.2.4: ``DQUOTE *(qtext / quoted-pair) DQUOTE``, where a
    quoted-pair is a backslash plus exactly one character. Escapes must
    be walked left to right — stripping ``\\\\`` and ``\\"`` pairs and
    checking for a leftover quote is not equivalent, because it accepts a
    local-part ending in a lone backslash: in ``"a\\"`` that backslash
    escapes the *closing* DQUOTE, so the string never terminates.

    Termination matters because the value is emitted into a header
    verbatim. An unterminated quoted string keeps quoting whatever
    follows it, so in a mailbox-list it swallows the comma before the
    next recipient — and a reader that ends the string elsewhere than we
    did counts a different number of mailboxes. That is the parser
    disagreement the rest of this library exists to avoid.
    """
    interior = local[1:-1]
    index = 0
    while index < len(interior):
        char = interior[index]
        if char == "\\":
            # A quoted-pair needs a character to escape. A backslash in
            # final position has none left but the closing DQUOTE.
            if index + 1 >= len(interior):
                return False
            index += 2
            continue
        if char == '"':
            # An unescaped quote ends the string early, and everything
            # after it is outside the quoting that made this one mailbox.
            return False
        index += 1
    return True
