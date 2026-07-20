"""Inbound sender authentication checks (DKIM / DMARC).

Returns the verdict the caller records in ``postmark["auth"]``:
  - ``None``: verified, record nothing.
  - ``"none"``: cannot verify (missing DKIM, backend unreachable, no AR
    header from a trusted relay). Frontend shows a yellow "unverified" hint.
  - ``"fail"``: explicit forgery signal (DMARC fail). Frontend shows a red
    "likely forged" warning — stronger than "none" because the sender's own
    domain disavows the message.

Rules applied for every backend:
  - DKIM must be present AND pass (else ``"none"``).
  - If DMARC is present and fails, return ``"fail"`` — even if DKIM passes.
  - If DMARC is absent or passes, DKIM alone decides.

The backend is picked by ``SPAM_CONFIG["inbound_auth"]``:
  - ``"native"``: verify DKIM locally (crypto + DNS) AND require the signing
    ``d=`` domain to match the From: domain (strict alignment). Raw DKIM only
    proves *some* domain signed the message; without alignment an attacker who
    controls any DKIM-enabled domain could sign a message bearing a forged
    From:. Full DMARC policy lookup is not implemented for native, so an
    unaligned-but-cryptographically-valid signature collapses to ``"none"``
    (we can't call it forgery without the From domain's published policy).
  - ``"rspamd"``: read DKIM / DMARC symbols from the rspamd /checkv2 result
    (reused from the spam check, or fetched on demand by the caller).
  - ``"arc"``: dkim/dmarc from the ``ARC-Authentication-Results`` sealed by a
    trusted sealer (``trusted_arc_sealers``; empty = any valid seal). Plaintext
    headers are never read. Unsealed/untrusted -> unverified.
  - ``"authentication-results"``: parse ``dkim=`` / ``dmarc=`` from the
    top-level ``Authentication-Results`` sliced by ``trusted_relays``.
  - missing / ``None``: disabled, always returns ``None``.

Backend failures (DNS lookup blowing up, rspamd unreachable, no AR header from
any trusted relay) collapse to ``"none"`` — we never claim a sender is
verified without positive evidence, but we also don't claim forgery without
an explicit DMARC fail.
"""

import logging
import re
from typing import Any

from jmap_email import JmapEmail, first_address_email

from core.mda.arc import arc_result
from core.mda.signing import verify_message_dkim
from core.mda.utils import headers_blocks

logger = logging.getLogger(__name__)


_PASS = "pass"  # noqa: S105 (auth-result token, not a password)
_FAIL = "fail"
_NONE = "none"  # explicitly no signature / policy


# Rspamd symbol names -> outcome, per check type.
# https://rspamd.com/doc/modules/dkim.html / dmarc
_RSPAMD_SYMBOLS: dict[str, dict[str, str]] = {
    "dkim": {
        "R_DKIM_ALLOW": _PASS,
        "R_DKIM_REJECT": _FAIL,
        "R_DKIM_PERMFAIL": _FAIL,
        "R_DKIM_TEMPFAIL": _FAIL,
        "DKIM_INVALID": _FAIL,
        "R_DKIM_NA": _NONE,
        "DKIM_NA": _NONE,
    },
    "dmarc": {
        "DMARC_POLICY_ALLOW": _PASS,
        "DMARC_POLICY_REJECT": _FAIL,
        "DMARC_POLICY_QUARANTINE": _FAIL,
        "DMARC_BAD_POLICY": _FAIL,
        "DMARC_NA": _NONE,
    },
}


# Matches `dkim=pass`, `dmarc=fail`, etc. in an Authentication-Results header
# value, after CFWS comments and quoted strings have been scrubbed. The leading
# (^|(?<=[\s;])) requires the method token to start at the beginning of the
# value or right after whitespace/`;` — RFC 8601's only legitimate token
# separators — so labels like `x-dkim=fail` or `arc.dkim=pass` are not
# mistaken for a bare `dkim=` token.
_AR_METHOD_RE = re.compile(
    r"(?:^|(?<=[\s;]))(?P<method>dkim|dmarc)\s*=\s*(?P<result>[a-zA-Z]+)",
    re.IGNORECASE,
)

_AR_PASS = {"pass"}
_AR_FAIL = {"fail", "softfail", "permerror", "temperror", "policy"}
_AR_NONE = {"none", "neutral"}


# Aggressive scrubbers run before the method/result regex so attacker-controlled
# free text inside a comment or quoted string can't masquerade as a real token.
# An unmatched opener consumes through end-of-string (the trailing close is
# optional in the pattern), which is the safe direction: prefer dropping
# suspicious bytes over honouring them.
_AR_COMMENT_RE = re.compile(r"\([^)]*\)?")
_AR_QUOTED_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"?')


def _scrub_ar_value(value: str) -> str:
    """Erase RFC 5322 comments and quoted-string contents from an AR value."""
    value = _AR_COMMENT_RE.sub(" ", value)
    return _AR_QUOTED_STRING_RE.sub(" ", value)


def _rspamd_outcome(check: str, rspamd_result: dict[str, Any] | None) -> str | None:
    if not rspamd_result:
        return None
    symbols = rspamd_result.get("symbols") or {}
    if not isinstance(symbols, dict):
        return None
    mapping = _RSPAMD_SYMBOLS.get(check, {})
    outcome: str | None = None
    for symbol, result in mapping.items():
        if symbol not in symbols:
            continue
        # fail dominates pass dominates none.
        if result == _FAIL:
            return _FAIL
        if result == _PASS:
            outcome = _PASS
        elif outcome is None:
            outcome = result
    return outcome


def _authentication_results_values(
    parsed_email: JmapEmail, trusted_relays: int
) -> list[str]:
    """Collect Authentication-Results header values from trusted header blocks.

    Block 0 is what we (or our MTA) prepended; blocks 1..N are upstream relays
    (most recent first). Anything past ``trusted_relays`` is ignored.
    """
    blocks = headers_blocks(parsed_email)
    blocks_to_check = trusted_relays + 1
    values: list[str] = []
    for block in blocks[:blocks_to_check]:
        ar = block.get("authentication-results")
        if not ar:
            continue
        if isinstance(ar, list):
            values.extend(str(v) for v in ar)
        else:
            values.append(str(ar))
    return values


def _ar_outcome(check: str, ar_values: list[str]) -> str | None:
    if not ar_values:
        return None
    found = False
    outcome: str | None = None
    for value in ar_values:
        scrubbed = _scrub_ar_value(value)
        for match in _AR_METHOD_RE.finditer(scrubbed):
            if match.group("method").lower() != check:
                continue
            found = True
            result = match.group("result").lower()
            if result in _AR_FAIL:
                return _FAIL
            if result in _AR_PASS:
                outcome = _PASS
            elif result in _AR_NONE and outcome is None:
                outcome = _NONE
    return outcome if found else None


def _from_header_domain(parsed_email: JmapEmail) -> str | None:
    """Return the lowercased domain of the RFC5322 From address, or ``None``."""
    from_email = first_address_email(parsed_email.get("from"))
    if not from_email:
        return None
    domain = from_email.strip().rstrip(".").lower().rpartition("@")[2]
    return domain or None


def _native_dkim_outcome(raw_data: bytes, parsed_email: JmapEmail) -> str | None:
    """Verify DKIM locally and require From/DKIM identifier alignment.

    A valid DKIM signature only proves that *some* domain signed the message,
    so we additionally require the signing domain (``d=``) to match the From:
    domain — strict alignment, an exact case-insensitive match. Without it an
    attacker who owns any DKIM-enabled domain could sign a message carrying a
    forged From: and have it shown as verified.

    Native mode never returns ``_FAIL``: it does no DMARC policy lookup, and a
    bare DKIM verify can't tell a *missing* signature from an *invalid* one, so
    it has no grounds to assert an explicit failure. Every non-pass outcome —
    no/invalid signature, or a valid signature whose ``d=`` doesn't align with
    From — collapses to ``_NONE`` ("unverified"). The unaligned case also logs
    the mismatch, since a *valid* signature not matching From is the spoofing
    signature.
    """
    try:
        signing_domain = verify_message_dkim(raw_data)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Native DKIM verification errored: %s", e)
        return None
    if not signing_domain:
        # No signature, or one that didn't validate — a bare verify can't tell
        # them apart, so this is "can't verify", not an explicit failure.
        return _NONE
    from_domain = _from_header_domain(parsed_email)
    if from_domain and signing_domain == from_domain:
        return _PASS
    logger.info(
        "Native DKIM signature not aligned with From: d=%s from=%s -> unverified",
        signing_domain,
        from_domain,
    )
    return _NONE


VERDICT_UNVERIFIED = "none"
VERDICT_FORGED = "fail"


def get_inbound_auth_mode(spam_config: dict[str, Any]) -> str:
    """Return the normalized ``inbound_auth`` mode from a spam config.

    Empty or missing values become an empty string. Callers can treat the
    result with ``if not mode`` as "disabled" and compare directly against
    the supported mode names.
    """
    return (spam_config.get("inbound_auth") or "").strip().lower()


def trusted_arc_sealers(spam_config: dict[str, Any]) -> set[str]:
    """Normalized ``trusted_arc_sealers`` allowlist from the spam config."""
    return {
        s.strip().rstrip(".").lower()
        for s in (spam_config.get("trusted_arc_sealers") or [])
        if isinstance(s, str) and s.strip()
    }


def check_inbound_authentication(
    raw_data: bytes,
    parsed_email: JmapEmail,
    spam_config: dict[str, Any],
    rspamd_result: dict[str, Any] | None = None,
    arc: dict[str, Any] | None = None,
) -> str | None:
    """Return the sender-auth verdict for this message (``postmark["auth"]``)."""
    mode = get_inbound_auth_mode(spam_config)
    if not mode:
        return None

    if mode == "native":
        dkim = _native_dkim_outcome(raw_data, parsed_email)
        dmarc: str | None = None
    elif mode == "rspamd":
        dkim = _rspamd_outcome("dkim", rspamd_result)
        dmarc = _rspamd_outcome("dmarc", rspamd_result)
    elif mode == "arc":
        if arc is None:
            arc = arc_result(raw_data, trusted_arc_sealers(spam_config))
        ar_values = [arc["aar"]] if arc["trusted"] and arc["aar"] else []
        dkim = _ar_outcome("dkim", ar_values)
        dmarc = _ar_outcome("dmarc", ar_values)
    elif mode == "authentication-results":
        trusted_relays = int(spam_config.get("trusted_relays", 0))
        ar_values = _authentication_results_values(parsed_email, trusted_relays)
        dkim = _ar_outcome("dkim", ar_values)
        dmarc = _ar_outcome("dmarc", ar_values)
    else:
        logger.warning("Unknown inbound_auth mode: %s", mode)
        return None

    logger.info("Inbound auth: mode=%s dkim=%s dmarc=%s", mode, dkim, dmarc)

    # DMARC fail is an explicit disavowal by the sender's domain — stronger
    # signal than a missing DKIM, so it wins regardless of DKIM's state.
    if dmarc == _FAIL:
        return VERDICT_FORGED
    if dkim != _PASS:
        return VERDICT_UNVERIFIED
    return None
