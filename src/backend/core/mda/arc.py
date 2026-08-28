"""ARC chain verification for inbound relay-trust (RFC 8617).

The rule-facing outcome is a **binary verdict**: a message is either ``trusted``
(a valid ARC chain sealed by an allowlisted sealer) or ``untrusted``
(everything else — no chain, a chain from a sealer we don't list, or a chain
that fails to validate). ``dnsfail`` is an *internal, transient* signal, not a
verdict: a key lookup that didn't complete for a message claiming one of our
trusted sealers is held for retry by the pipeline (never dropped, never
delivered as verified), so it must never reach a gating rule.

Performance / attack surface: we only pay the crypto + DNS cost of full chain
verification when the message's outermost sealer is one we could actually trust.
An unlisted sealer is ``untrusted`` regardless of whether its chain is valid, so
validating it buys nothing — and skipping it means attacker-controlled mail
(which never names a trusted sealer) triggers zero DNS traffic.
"""

import logging
from typing import Any, Dict, Optional, Set, Tuple

from dkim import ARC, CV_Pass, arc_verify, get_txt
from dkim.util import parse_tag_value

from core.mda.addresses import ascii_lower

logger = logging.getLogger(__name__)


# Upper bound on ARC instances we are willing to cryptographically verify. A
# forged chain claiming a trusted sealer could otherwise force one DNS lookup
# per instance; real chains are a handful of hops, so cap well below that.
# Beyond this the message is simply ``untrusted`` (never verified).
_MAX_ARC_INSTANCES = 20


def _sealer_trusted(sealer: Optional[str], trusted: Set[str]) -> bool:
    """True if sealer equals or is a subdomain of a trusted sealer."""
    if not sealer:
        return False
    if sealer in trusted:
        return True
    return any(sealer.endswith("." + t) for t in trusted)


def _normalize_domain(raw: Any) -> Optional[str]:
    """Lowercase, strip surrounding whitespace and a trailing dot from a d=.

    ASCII-folded, never ``str.lower()``: the result is matched against the
    ``trusted_arc_sealers`` allowlist, and Unicode folding would let a sealer
    we do not trust compare equal to one we do.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("ascii", "replace")
    if not isinstance(raw, str):
        return None
    return ascii_lower(raw.strip().rstrip(".")) or None


def _outermost_sealer(raw_data: bytes) -> Tuple[Optional[str], int]:
    """Cheaply (no crypto, no DNS) find the outermost ARC sealer.

    Returns ``(sealer_domain, max_instance)`` where ``sealer_domain`` is the
    ``d=`` of the ``ARC-Message-Signature`` at the highest instance, and
    ``max_instance`` is ``0`` when the message carries no (parseable) ARC chain.

    Uses dkimpy's own header sorting so the sealer we gate on is exactly the one
    ``arc_verify`` would treat as outermost — no second parser to diverge from.
    Any parse error is reported as "no chain" (``0``); a malformed ARC structure
    would fail full verification anyway, and reporting no-chain fails *safe*
    (the message is treated as untrusted, never over-trusted).
    """
    try:
        max_instance, arc_headers = ARC(raw_data).sorted_arc_headers()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("ARC header parse failed (treating as no chain): %s", exc)
        return None, 0
    if max_instance == 0:
        return None, 0
    for instance, (name, value) in arc_headers:
        if instance == max_instance and name.lower() == b"arc-message-signature":
            try:
                domain = parse_tag_value(value).get(b"d")
            except Exception:  # pylint: disable=broad-exception-caught
                return None, max_instance
            return _normalize_domain(domain), max_instance
    return None, max_instance


def arc_result(raw_data: bytes, trusted_sealers: Set[str]) -> Dict[str, Any]:
    """Classify a message's ARC relay-trust.

    **Fail closed:** an empty ``trusted_sealers`` trusts *nothing* — you must
    list the sealers you trust. (Anyone can produce a valid ARC seal, so trusting
    "any valid seal" would let a spammer self-seal into "verified".)

    Returns a dict:
      - ``trusted``: ``cv=pass`` AND sealed by an allowlisted sealer.
      - ``sealer``:  the outermost ARC-Message-Signature ``d=`` (``None`` if no
        chain). Used to scope the retry-on-DNS-failure hold.
      - ``aar``:     the outermost ARC-Authentication-Results value, but ONLY
        when ``trusted``; ``None`` otherwise.
      - ``dnsfail``: internal — a key lookup for a *claimed-trusted* sealer did
        not complete, so the result is indeterminate. Callers hold (retry), they
        never gate on it. See the module docstring.
    """
    result: Dict[str, Any] = {
        "trusted": False,
        "sealer": None,
        "aar": None,
        "dnsfail": False,
    }

    # Fail closed: with no allowlist nothing is trusted, so there is nothing to
    # parse or verify.
    if not trusted_sealers:
        return result

    # --- Cheap gate: decide whether full verification is even worth it. ---
    sealer, max_instance = _outermost_sealer(raw_data)
    result["sealer"] = sealer
    if max_instance == 0:
        # No ARC chain -> untrusted. No crypto, no DNS.
        return result
    if max_instance > _MAX_ARC_INSTANCES:
        # Implausibly long chain — refuse to verify (amplification guard).
        logger.info("ARC chain too long (%d instances) — untrusted", max_instance)
        return result
    if not _sealer_trusted(sealer, trusted_sealers):
        # The outermost sealer is not one we trust, so the chain's validity is
        # irrelevant — untrusted without spending any crypto/DNS on it.
        return result

    # --- The outermost sealer could be trusted: verify the chain (crypto+DNS).
    dns_incomplete = False

    def _tracking_dnsfunc(name, timeout=5):
        nonlocal dns_incomplete
        try:
            txt = get_txt(name, timeout=timeout)
        except Exception:  # pylint: disable=broad-exception-caught
            dns_incomplete = True
            raise
        if not txt:
            dns_incomplete = True
        return txt

    try:
        cv, results, _reason = arc_verify(raw_data, dnsfunc=_tracking_dnsfunc)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("ARC verify errored (treating as untrusted): %s", exc)
        result["dnsfail"] = dns_incomplete
        return result

    if not results:
        # Structurally broken chain; if a lookup was also outstanding, treat it
        # as indeterminate (hold) rather than a definite untrusted verdict.
        result["dnsfail"] = dns_incomplete
        return result

    # Prefer dkimpy's own parsed domain for the trust decision (authoritative).
    outer = results[0]
    verified_sealer = _normalize_domain(outer.get("ams-domain"))
    if verified_sealer:
        result["sealer"] = verified_sealer

    if cv == CV_Pass:
        if _sealer_trusted(result["sealer"], trusted_sealers):
            result["trusted"] = True
            aar_raw = outer.get("aar-value")
            if isinstance(aar_raw, (bytes, bytearray)):
                result["aar"] = aar_raw.decode("utf-8", "replace")
            elif isinstance(aar_raw, str):
                result["aar"] = aar_raw
    elif dns_incomplete:
        # Claimed-trusted sealer whose key we couldn't fetch: indeterminate, not
        # forged — held for retry by the arc pipeline step.
        result["dnsfail"] = True

    return result
