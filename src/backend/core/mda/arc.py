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

from dkim import ARC, CV_Pass, arc_verify
from dkim.util import parse_tag_value

from core.mda.addresses import ascii_lower
from core.services.dns.records import parse_dkim_tags
from core.services.dns.resolver import (
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    resolve_txt_values,
)

logger = logging.getLogger(__name__)


def arc_dns_txt(name, timeout=5):  # pylint: disable=unused-argument
    """Fetch an ARC sealer's public key, in dkimpy's ``dnsfunc`` shape.

    Replaces ``dkim.get_txt``, which builds its own stub resolver. ``timeout``
    is part of the contract dkimpy calls us with and cannot be honoured: the
    shared resolver takes its bounds at construction, not per call. Note which
    way that cuts — dkimpy asks for 5s and the resolver's own ceiling is
    ``DNS_RESOLVER_MAX_RESOLUTION_TIME``, so ignoring the parameter *raises*
    the per-lookup limit rather than holding it down. The cap that actually
    bounds an attacker-supplied chain is ``_MAX_ARC_DNS_LOOKUPS`` in
    :func:`arc_result`, not this argument.

    Returns ``None`` for a *settled* answer holding no key: the name does not
    exist, cannot be formed at all, publishes no TXT, or publishes only
    records that are not keys. A lookup that did not complete raises instead,
    which is how the caller tells a sealer that has no key from a resolver it
    could not reach.

    ``InvalidNameError`` counts as settled because the name is built from the
    signature's own ``s=`` and ``d=``: a forged chain naming a trusted sealer
    with a malformed selector would otherwise be held for the whole deferral
    window on a name that can never resolve.

    Only values that parse as a DKIM key record carrying ``p=`` count, as on
    the DKIM verify path (RFC 8617 4.1.1 reuses RFC 6376 key records). A
    selector name commonly carries a domain-verification token beside the key
    and TXT ordering is unspecified, so taking the first value outright fails
    a working sealer whenever the resolver lists the token first. Several
    *key* records still yields the first: unlike the DKIM
    verify path, this name belongs to a sealer the operator explicitly trusts,
    so an extra key is their config oddity and refusing it would hold every
    message from them.
    """
    fqdn = name.decode("ascii") if isinstance(name, bytes) else name
    try:
        values = resolve_txt_values(fqdn.rstrip("."))
    except (NXDOMAINError, NoAnswerError, InvalidNameError):
        return None
    key_records = [
        v for v in values if (tags := parse_dkim_tags(v)) is not None and "p" in tags
    ]
    if not key_records:
        return None
    return key_records[0].encode("utf-8", "surrogateescape")


# Upper bound on ARC instances we are willing to cryptographically verify. A
# forged chain claiming a trusted sealer could otherwise force one DNS lookup
# per instance; real chains are a handful of hops, so cap well below that.
# Beyond this the message is simply ``untrusted`` (never verified).
_MAX_ARC_INSTANCES = 20

# Upper bound on key lookups for one message, and the cap that actually bounds
# the DNS cost of a forged chain. ``ARC.verify`` walks every instance looking
# up both the ARC-Message-Signature and the ARC-Seal key, so the instance cap
# alone still allows ~40 recursive-from-root resolutions — and only the
# *outermost* d= is checked against the allowlist, so every inner d=/s= is
# attacker-chosen and may point at a deliberately slow authoritative server.
# At DNS_RESOLVER_MAX_RESOLUTION_TIME each, that is an inbound worker held for
# minutes by one message. A genuine chain resolves a handful of keys, so this
# only ever bites forgeries; past it the lookups return "no key", which fails
# the chain into ``untrusted`` rather than into a retry hold.
_MAX_ARC_DNS_LOOKUPS = 8


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
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("ARC-Message-Signature tag parse failed: %s", exc)
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
    lookups = 0

    def _tracking_dnsfunc(name, timeout=5):
        nonlocal dns_incomplete, lookups
        if lookups >= _MAX_ARC_DNS_LOOKUPS:
            # Refused, not failed: reported as "no key" so the chain settles
            # into untrusted. Raising here would set dns_incomplete and hold
            # the message for retry, which re-runs the same lookups on every
            # attempt — turning the cap into the amplifier it exists to stop.
            logger.info(
                "ARC: key lookup budget spent (%d) — refusing further lookups",
                _MAX_ARC_DNS_LOOKUPS,
            )
            return None
        lookups += 1
        try:
            return arc_dns_txt(name, timeout=timeout)
        except Exception:  # pylint: disable=broad-exception-caught
            # Only a lookup that did not complete is indeterminate. A settled
            # "this sealer publishes no key" comes back as None: the seal can
            # never validate, so the verdict is untrusted now rather than a
            # hold that reaches the same place after the deferral window.
            dns_incomplete = True
            raise

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
