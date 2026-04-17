"""Inbound sender authentication checks (DKIM / DMARC).

Returns the verdict the caller should stamp as ``X-StMsg-Sender-Auth``:
  - ``None``: verified, do not add the header.
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
  - ``"native"``: verify DKIM locally (crypto + DNS). DMARC is not yet
    implemented for native, so only the DKIM rule applies.
  - ``"rspamd"``: read DKIM / DMARC symbols from the rspamd /checkv2 result
    (reused from the spam check, or fetched on demand by the caller).
  - ``"authentication-results"``: parse ``dkim=`` / ``dmarc=`` entries from the
    ``Authentication-Results`` header set by a trusted upstream relay. The
    header slice respects ``SPAM_CONFIG["trusted_relays"]`` so forged headers
    from untrusted hops are ignored.
  - missing / ``None``: disabled, always returns ``None``.

Backend failures (DNS lookup blowing up, rspamd unreachable, no AR header from
any trusted relay) collapse to ``"none"`` — we never claim a sender is
verified without positive evidence, but we also don't claim forgery without
an explicit DMARC fail.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from core.mda.signing import verify_message_dkim

logger = logging.getLogger(__name__)


_PASS = "pass"
_FAIL = "fail"
_NONE = "none"  # explicitly no signature / policy


# Rspamd symbol names -> outcome, per check type.
# https://rspamd.com/doc/modules/dkim.html / dmarc
_RSPAMD_SYMBOLS: Dict[str, Dict[str, str]] = {
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


# Matches `dkim=pass`, `dmarc=fail`, etc. in an Authentication-Results header.
_AR_METHOD_RE = re.compile(
    r"\b(?P<method>dkim|dmarc)\s*=\s*(?P<result>[a-zA-Z]+)",
    re.IGNORECASE,
)

_AR_PASS = {"pass"}
_AR_FAIL = {"fail", "softfail", "permerror", "temperror", "policy"}
_AR_NONE = {"none", "neutral"}


def _rspamd_outcome(
    check: str, rspamd_result: Optional[Dict[str, Any]]
) -> Optional[str]:
    if not rspamd_result:
        return None
    symbols = rspamd_result.get("symbols") or {}
    if not isinstance(symbols, dict):
        return None
    mapping = _RSPAMD_SYMBOLS.get(check, {})
    outcome: Optional[str] = None
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
    parsed_email: Dict[str, Any], trusted_relays: int
) -> List[str]:
    """Collect Authentication-Results header values from trusted header blocks.

    Block 0 is what we (or our MTA) prepended; blocks 1..N are upstream relays
    (most recent first). Anything past ``trusted_relays`` is ignored.
    """
    blocks = parsed_email.get("headers_blocks") or []
    blocks_to_check = trusted_relays + 1
    values: List[str] = []
    for block in blocks[:blocks_to_check]:
        ar = block.get("authentication-results")
        if not ar:
            continue
        if isinstance(ar, list):
            values.extend(str(v) for v in ar)
        else:
            values.append(str(ar))
    return values


def _ar_outcome(check: str, ar_values: List[str]) -> Optional[str]:
    if not ar_values:
        return None
    found = False
    outcome: Optional[str] = None
    for value in ar_values:
        for match in _AR_METHOD_RE.finditer(value):
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


def _native_dkim_outcome(raw_data: bytes) -> Optional[str]:
    try:
        return _PASS if verify_message_dkim(raw_data) else _FAIL
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Native DKIM verification errored: %s", e)
        return None


VERDICT_UNVERIFIED = "none"
VERDICT_FORGED = "fail"


def check_inbound_authentication(
    raw_data: bytes,
    parsed_email: Dict[str, Any],
    spam_config: Dict[str, Any],
    rspamd_result: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Return the ``X-StMsg-Sender-Auth`` verdict for this message.

    See module docstring for the rule set and supported backends.
    """
    mode = (spam_config.get("inbound_auth") or "").strip().lower() or None
    if not mode:
        return None

    if mode == "native":
        dkim = _native_dkim_outcome(raw_data)
        dmarc: Optional[str] = None
    elif mode == "rspamd":
        dkim = _rspamd_outcome("dkim", rspamd_result)
        dmarc = _rspamd_outcome("dmarc", rspamd_result)
    elif mode == "authentication-results":
        trusted_relays = int(spam_config.get("trusted_relays", 1))
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
