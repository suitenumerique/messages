"""RFC 5321 envelope-address validation.

The functions in this module are intentionally strict: they reject anything
the inbound SMTP server should not have to deal with — source routes
(RFC 5321 §4.1.1.3), control characters (CRLF injection vector), overlong
local-parts or domains, and the common ``user@`` / ``@domain`` truncations.

They never accept already-unbalanced quoting or angle brackets.
:func:`validate_envelope_address` accepts either the wrapped (``<user@host>``)
or unwrapped form; :func:`strip_brackets` runs unconditionally on entry.
"""

from __future__ import annotations

# Characters that must never appear unquoted in an envelope address. CR, LF,
# and NUL are the CRLF-injection and frame-confusion vectors. TAB is a header
# unfolding vector. ``%`` is included to keep us out of historical
# "percent-routing" relay tricks (RFC 1123 §5.2.16). DEL (0x7f) and bare
# space have no place in an address received from the wire.
_FORBIDDEN_CHARS = frozenset({"\r", "\n", "\x00", "\t", " ", "%", "\x7f"})


class AddressError(ValueError):
    """Raised when an envelope address fails validation.

    The ``reason`` attribute carries a short token suitable for a Prometheus
    metric label and the ``smtp_code`` / ``smtp_text`` tuple gives the exact
    SMTP reply the caller should send back to the peer.
    """

    def __init__(self, reason: str, smtp_text: str, smtp_code: int = 553):
        super().__init__(smtp_text)
        self.reason = reason
        self.smtp_text = smtp_text
        self.smtp_code = smtp_code


def strip_brackets(raw: str) -> str:
    """Strip a single pair of surrounding angle brackets.

    Returns the input unchanged if there is no leading ``<``. Does not validate
    that the address inside is well-formed.
    """
    if not raw:
        return raw
    if raw.startswith("<") and raw.endswith(">"):
        return raw[1:-1]
    return raw


def validate_envelope_address(  # noqa: PLR0912
    raw: str,
    *,
    allow_empty: bool,
    max_local: int,
    max_domain: int,
) -> str:
    """Validate ``raw`` as an RFC 5321 envelope address.

    ``allow_empty`` controls whether the null sender ``<>`` is accepted. It
    must be true for MAIL FROM and false for RCPT TO (RFC 5321 §4.5.5).

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
    bad = _FORBIDDEN_CHARS & set(address)
    if bad:
        raise AddressError(
            reason="control_char",
            smtp_code=501,
            smtp_text="5.1.7 Address contains forbidden control characters",
        )

    # ----- 2. source routes  @host1,@host2:user@host3  -----------------------
    # RFC 5321 §4.1.1.3 allows ignoring source routes; we reject outright.
    if address.startswith("@"):
        raise AddressError(
            reason="source_route",
            smtp_code=553,
            smtp_text="5.1.3 Source routes are not accepted",
        )

    # ----- 3. exactly one unquoted '@' ---------------------------------------
    # Quoted local-parts could legally contain '@', but we don't accept those
    # on the public inbound path — most senders never use them and they are
    # a fertile parser-confusion ground.
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
    # aiosmtpd's email-parser unwraps the quoted form (``"a"@b.com`` arrives
    # with quotes intact in the local-part). The MDA's mailbox lookup
    # normalises differently than our address validator, which is a
    # parser-mismatch vector. Reject double quotes outright.
    if '"' in local:
        raise AddressError(
            reason="bad_address",
            smtp_code=553,
            smtp_text="5.1.3 Quoted local-parts not accepted",
        )

    # ----- 3b. dot placement in unquoted local-part (RFC 5321 §4.1.2) -------
    # A leading dot, trailing dot, or two consecutive dots are illegal in an
    # unquoted local-part. Different mailbox-lookup paths normalise these
    # inconsistently, so reject at the gate.
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise AddressError(
            reason="bad_address",
            smtp_code=553,
            smtp_text="5.1.3 Malformed local part",
        )

    # ----- 4. length limits (RFC 5321 §4.5.3.1) -----------------------------
    if len(local.encode("utf-8")) > max_local:
        raise AddressError(
            reason="oversize_local",
            smtp_code=553,
            smtp_text=f"5.1.3 Local part exceeds {max_local} octets",
        )
    if len(domain.encode("utf-8")) > max_domain:
        raise AddressError(
            reason="oversize_domain",
            smtp_code=553,
            smtp_text=f"5.1.3 Domain part exceeds {max_domain} octets",
        )

    # ----- 5. domain shape ---------------------------------------------------
    # Allow IDN/UTF-8 in the domain; reject empty labels, leading dot, label
    # > 63 octets, and bare IP literals (the inbound path expects FQDNs from
    # legitimate senders).
    if domain.startswith("[") and domain.endswith("]"):
        raise AddressError(
            reason="address_literal",
            smtp_code=501,
            smtp_text="5.1.3 Address literals not accepted",
        )
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        raise AddressError(
            reason="bad_address",
            smtp_code=501,
            smtp_text="5.1.3 Malformed domain",
        )
    for label in domain.split("."):
        if not label or len(label.encode("utf-8")) > 63:
            raise AddressError(
                reason="bad_address",
                smtp_code=501,
                smtp_text="5.1.3 Malformed domain label",
            )

    return f"{local}@{domain.lower()}"
