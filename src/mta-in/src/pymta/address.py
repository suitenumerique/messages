"""RFC 5321 envelope-address validation.

Strict: refuses control characters (CRLF injection), overlong local-parts or
domains, ``user@`` and ``@domain``, unbalanced quotes or angle brackets, and
what is left of a source route the command parser did not take off.

:func:`validate_envelope_address` takes the address with or without its angle
brackets.
"""

from __future__ import annotations

import contextlib
import ipaddress
import unicodedata

import idna

# Refused in an envelope address: CR, LF and NUL (line injection), TAB (header
# unfolding), DEL, and space outside a quoted local-part.
_FORBIDDEN_CHARS = frozenset({"\r", "\n", "\x00", "\t", " ", "\x7f"})

# ``%`` on a recipient is the historical "percent-routing" relay trick
# (RFC 1123 §5.2.16), so it stays refused there. On a sender it routes nothing
# and is ordinary: VERP return paths encode the recipient with it
# (``bounce-user%40example.org@esp.example``).
_PERCENT = "%"

# Controls, line and paragraph separators, spaces, and format characters.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Zs"})

# The two format characters (Cf) that are part of how a word is written:
# ZERO WIDTH NON-JOINER and ZERO WIDTH JOINER, required in Persian and several
# Indic scripts. The only ones IDNA2008 (RFC 5892) and PRECIS (RFC 8264) let
# into an identifier. The rest of Cf is invisible and changes how the address
# is displayed, not what it is: bidi overrides, BOM, soft hyphen, tags.
_JOIN_CONTROLS = frozenset({chr(0x200C), chr(0x200D)})


def _forbidden(char: str) -> bool:
    return char not in _JOIN_CONTROLS and unicodedata.category(char) in _FORBIDDEN_CATEGORIES


class AddressError(ValueError):
    """An envelope address that fails validation.

    ``reason`` is a short token for logs and metric labels; ``smtp_code`` and
    ``smtp_text`` are the reply to send.
    """

    def __init__(self, reason: str, smtp_text: str, smtp_code: int = 553):
        super().__init__(smtp_text)
        self.reason = reason
        self.smtp_text = smtp_text
        self.smtp_code = smtp_code


def strip_brackets(raw: str) -> str:
    """Remove one pair of surrounding angle brackets, when both are there."""
    if not raw:
        return raw
    if raw.startswith("<") and raw.endswith(">"):
        return raw[1:-1]
    return raw


_BAD_SYNTAX = ("bad_address", "5.1.3 Bad address syntax", 501)

# RFC 5322 atext, the ASCII allowed in a local-part without quotes.
_ATEXT = frozenset("!#$%&'*+-/=?^_`{|}~")


def _split_quoted(address: str) -> tuple[str, str]:
    """Split ``"local"@domain`` into the unescaped local-part and the domain."""
    content, i = [], 1
    while i < len(address) and address[i] != '"':
        if address[i] == "\\":
            i += 1
            if i == len(address):
                raise AddressError(*_BAD_SYNTAX)
        content.append(address[i])
        i += 1
    domain = address[i + 2 :]
    if not content or address[i + 1 : i + 2] != "@" or not domain:
        raise AddressError(*_BAD_SYNTAX)
    return "".join(content), domain


def _needs_quoting(local: str) -> bool:
    """True when ``local`` is not a dot-atom and has to be written in quotes."""
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return True
    return any(
        char.isascii() and not (char.isalnum() or char in _ATEXT or char == ".") for char in local
    )


def _canonical_literal(domain: str) -> str:
    """``[192.0.2.1]`` or ``[IPv6:2001:db8::1]``, with the address normalised."""
    inner = domain[1:-1]
    try:
        if inner[:5].lower() == "ipv6:":
            return f"[IPv6:{ipaddress.IPv6Address(inner[5:])}]"
        return f"[{ipaddress.IPv4Address(inner)}]"
    except ValueError as exc:
        raise AddressError(
            reason="address_literal",
            smtp_code=501,
            smtp_text="5.1.3 Malformed address literal",
        ) from exc


def _dns_labels(domain: str) -> list[str]:
    """``domain``'s labels in the A-label form the DNS length limits apply to.

    An SMTPUTF8 peer may send U-labels (RFC 6531 §3.2), whose UTF-8 length is
    not what DNS stores: a 40-letter Cyrillic label is 76 UTF-8 octets and a
    52-octet A-label. RFC 5890 §4.2 puts the 63 and 255 octet limits on the
    A-label and lets a U-label run to 252 code points.

    Lengths only, not IDNA validity: ``idna.encode`` would refuse domains
    pymta accepts (an ``_`` in a host name). The domain is mapped with UTS 46
    non-transitional, as ``jmap_email`` does, and each label encoded with the
    stdlib ``punycode`` codec (plain RFC 3492, without the IDNA2003 folding
    of the ``idna`` codec). A domain UTS 46 refuses is measured unmapped.
    """
    if domain.isascii():
        return domain.split(".")
    with contextlib.suppress(idna.IDNAError):
        domain = idna.uts46_remap(domain, std3_rules=False, transitional=False)
    return [
        label if label.isascii() else "xn--" + label.encode("punycode").decode("ascii")
        for label in domain.split(".")
    ]


def validate_envelope_address(  # noqa: PLR0912, PLR0913, PLR0915
    raw: str,
    *,
    allow_empty: bool,
    max_local: int | None,
    max_domain: int,
    allow_percent: bool = False,
    require_fqdn: bool = False,
    allow_quoted: bool = False,
    allow_literal: bool = False,
) -> str:
    """Validate ``raw`` as an RFC 5321 envelope address.

    ``allow_empty`` controls whether the null sender ``<>`` is accepted. It
    must be true for MAIL FROM and false for RCPT TO (RFC 5321 §4.5.5).

    ``allow_percent`` accepts ``%`` in the local-part.

    ``require_fqdn`` refuses a domain with a single label (``user@localhost``).

    ``allow_quoted`` accepts a quoted local-part (``"a b"@example.com``), with
    the spaces and the ``@`` it may hold, and the dot placements that are only
    valid inside quotes. The result is quoted when, and only when, its content
    needs it, whatever the peer wrote: ``"alice"@x`` comes back as ``alice@x``,
    and ``a..b@x``, which senders do write, as ``"a..b"@x``.

    ``allow_literal`` accepts an address literal as domain
    (``user@[192.0.2.1]``, ``user@[IPv6:2001:db8::1]``).

    The last three are for MAIL FROM: all are valid per RFC 5321, and nothing
    downstream parses or routes on the sender. RCPT TO stays strict, since a
    recipient has to name a mailbox of ours and none has such a name.

    ``max_local=None`` skips the local-part cap. That is for MAIL FROM:
    §4.5.3.1 asks to avoid these limits where possible, and forwarders' SRS
    or VERP return paths routinely carry a local-part longer than 64 octets.
    The address stays bounded by the 548-octet MAIL command line (see
    ``SMTPConnection._line_limit``).

    Returns the cleaned address (lower-cased domain, original local-part) on
    success, raises :class:`AddressError` on failure.
    """
    address = strip_brackets(raw or "")

    if address == "":
        if allow_empty:
            return ""
        raise AddressError(
            reason="bad_address",
            smtp_code=553,
            smtp_text="5.1.3 Empty recipient address not allowed",
        )

    # ----- 1a. residual angle brackets ---------------------------------------
    # strip_brackets only removes a balanced outer pair; any leftover '<' or '>'
    # means the address is malformed (unbalanced or nested brackets).
    if "<" in address or ">" in address:
        raise AddressError(
            reason="bad_address",
            smtp_code=501,
            smtp_text="5.1.3 Malformed address syntax",
        )

    # ----- 1b. control / CRLF / NUL injection --------------------------------
    # SMTPUTF8 makes the local-part arbitrary UTF-8, so U+2028, U+0085, VT and
    # FF arrive from the wire and ``str.splitlines`` breaks on every one.
    # Refused here so they never reach the MDA, a Received header or a bounce.
    # ``%`` in the domain is refused whatever the caller allows.
    #
    # A quoted local-part may hold plain spaces, and nothing else from this
    # list: they are taken out of what is scanned.
    quoted = _split_quoted(address) if address.startswith('"') else None
    scanned = address if quoted is None else quoted[0].replace(" ", "") + "@" + quoted[1]
    domain_side = scanned.rpartition("@")[2] if quoted is None else quoted[1]
    percent_refused = _PERCENT in (domain_side if allow_percent else scanned)
    if (
        percent_refused
        or _FORBIDDEN_CHARS & set(scanned)
        or any(_forbidden(char) for char in scanned)
    ):
        raise AddressError(
            reason="control_char",
            smtp_code=501,
            smtp_text="5.1.7 Address contains forbidden characters",
        )

    # ----- 1c. valid UTF-8 ---------------------------------------------------
    # Command arguments are decoded with ``errors="surrogateescape"``, so a
    # byte that is not valid UTF-8 arrives as a lone surrogate (0xE9 becomes
    # ``\udce9``). RFC 6531 requires valid UTF-8: refused for good.
    #
    # Checked here because the length checks below call ``.encode("utf-8")``,
    # which raises UnicodeEncodeError on a surrogate: not an AddressError, so
    # it would end as a 421 internal error, telling the sender to retry.
    try:
        address.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AddressError(
            reason="invalid_encoding",
            smtp_code=553,
            smtp_text="5.1.3 Address is not valid UTF-8",
        ) from exc

    # ----- 2. source routes  @host1,@host2:user@host3  -----------------------
    # ``command.split_path`` has already dropped the route of a bracketed
    # path, as RFC 5321 §4.1.1.3 asks. What still starts with "@" is a route
    # without brackets or without its ":", and is refused.
    if address.startswith("@"):
        raise AddressError(
            reason="source_route",
            smtp_code=553,
            smtp_text="5.1.3 Source routes are not accepted",
        )

    # ----- 3. exactly one unquoted '@' ---------------------------------------
    if quoted is not None:
        local, domain = quoted
    else:
        if address.count("@") != 1:
            raise AddressError(
                reason="bad_address",
                smtp_code=501,
                smtp_text="5.1.3 Bad address syntax",
            )
        local, _, domain = address.partition("@")

    if not local or not domain:
        raise AddressError(
            reason="bad_address",
            smtp_code=501,
            smtp_text="5.1.3 Bad address syntax",
        )

    # ----- 3a. quoted local-parts -------------------------------------------
    # ``"bob"@example.com`` is ``bob@example.com`` (RFC 5321 section 4.1.2)
    # and is accepted everywhere. Quotes the content needs are for MAIL FROM
    # only: no mailbox of ours has such a name.
    if quoted is not None and not allow_quoted and _needs_quoting(local):
        raise AddressError(
            reason="bad_address",
            smtp_code=553,
            smtp_text="5.1.3 Quoted local-parts not accepted",
        )
    # Outside quotes a local-part is atoms and dots. Anything else there (a
    # comment, a stray quote, a route) is not an address.
    if quoted is None and any(
        char.isascii() and not (char.isalnum() or char in _ATEXT or char == ".") for char in local
    ):
        raise AddressError(
            reason="bad_address",
            smtp_code=553,
            smtp_text="5.1.3 Malformed local part",
        )

    # ----- 3b. dot placement in unquoted local-part (RFC 5321 §4.1.2) -------
    # A leading dot, trailing dot, or two consecutive dots are only valid
    # inside quotes. Refused where quoting is not accepted; otherwise the
    # quotes go back on below.
    if not allow_quoted and (local.startswith(".") or local.endswith(".") or ".." in local):
        raise AddressError(
            reason="bad_address",
            smtp_code=553,
            smtp_text="5.1.3 Malformed local part",
        )

    # ----- 4. length limits (RFC 5321 §4.5.3.1) -----------------------------
    if max_local is not None and len(local.encode("utf-8")) > max_local:
        raise AddressError(
            reason="oversize_local",
            smtp_code=553,
            smtp_text=f"5.1.3 Local part exceeds {max_local} octets",
        )
    dns_labels = _dns_labels(domain)
    if len(".".join(dns_labels)) > max_domain:
        raise AddressError(
            reason="oversize_domain",
            smtp_code=553,
            smtp_text=f"5.1.3 Domain part exceeds {max_domain} octets",
        )

    # ----- 5. domain shape ---------------------------------------------------
    # Allow IDN/UTF-8 in the domain; reject empty labels, leading dot, label
    # > 63 octets as an A-label, and an address literal unless
    # ``allow_literal``.
    if allow_quoted and _needs_quoting(local):
        local = '"' + local.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if domain.startswith("[") and domain.endswith("]"):
        if not allow_literal:
            raise AddressError(
                reason="address_literal",
                smtp_code=501,
                smtp_text="5.1.3 Address literals not accepted",
            )
        return f"{local}@{_canonical_literal(domain)}"
    # ASCII in a host name is letters, digits, hyphen and dot, plus the
    # underscore some real senders use. Anything else (half a literal, a
    # quote, a slash) is not a domain. Non-ASCII is left to the IDN handling
    # above, which only measures it.
    if any(char.isascii() and not (char.isalnum() or char in "-_.") for char in domain):
        raise AddressError(
            reason="bad_address",
            smtp_code=501,
            smtp_text="5.1.3 Malformed domain",
        )
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        raise AddressError(
            reason="bad_address",
            smtp_code=501,
            smtp_text="5.1.3 Malformed domain",
        )
    # ``dns_labels`` too: UTS 46 maps the ideographic full stops to ".", so a
    # label can be empty only after mapping.
    if (
        not all(domain.split("."))
        or not all(dns_labels)
        or any(len(label) > 63 for label in dns_labels)
    ):
        raise AddressError(
            reason="bad_address",
            smtp_code=501,
            smtp_text="5.1.3 Malformed domain label",
        )
    if require_fqdn and "." not in domain:
        raise AddressError(
            reason="non_fqdn_sender",
            smtp_code=504,
            smtp_text="5.5.2 Sender address rejected: need fully-qualified address",
        )

    return f"{local}@{domain.lower()}"
