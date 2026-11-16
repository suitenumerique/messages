"""Inbound sender authentication checks (DKIM / DMARC).

Returns the verdict the caller records in ``postmark["auth"]``:
  - ``None``: verified, record nothing.
  - ``"none"``: cannot verify (missing DKIM, backend unreachable, no AR
    header from a trusted relay). Frontend shows a yellow "unverified" hint.
  - ``"fail"``: explicit forgery signal (DMARC fail). Frontend shows a red
    "likely forged" warning — stronger than "none" because the sender's own
    domain disavows the message.

Rules applied for every backend:
  - DKIM must pass with a signing domain aligned with the single From domain
    (else ``"none"``). Other failing signatures do not cancel that pass.
  - If DMARC is present and fails, return ``"fail"`` — even if DKIM passes.
  - If DMARC is ``temperror`` or ``permerror``, return ``"none"``.
  - If DMARC is absent or passes, DKIM alone decides.

The backend is picked by ``SPAM_CONFIG["inbound_auth"]``:
  - ``"native"``: full local evaluation. DKIM verified locally, SPF
    evaluated against the SMTP envelope (``core.mda.spf``) when it can change
    the verdict, DMARC policy
    discovered and applied (``core.mda.dmarc``). DKIM counts as ``pass`` only
    when aligned with From. DMARC is ``fail`` only when the From domain asks
    for quarantine or reject; a trusted ARC sealer's ``dmarc=pass`` overrides
    it (forwarded mail).
  - ``"rspamd"``: read DKIM / DMARC symbols from the rspamd /checkv2 result
    (reused from the spam check, or fetched on demand by the caller). The
    signing domain comes from the ``R_DKIM_ALLOW`` options.
  - ``"arc"``: dkim/dmarc from the ``ARC-Authentication-Results`` sealed by a
    trusted sealer (``trusted_arc_sealers``; empty = trust nothing). Plaintext
    headers are never read. Unsealed/untrusted -> unverified.
  - ``"authentication-results"``: parse ``dkim=`` / ``dmarc=`` from the
    top-level ``Authentication-Results`` sliced by ``trusted_relays``.
  For ``arc`` and ``authentication-results`` the signing domain is the
  ``header.d`` (or ``header.i``) of the ``dkim=pass`` result. These backends
  do not tell us ``adkim``, so alignment is relaxed.
  - missing / ``None``: disabled, always returns ``None``.

Backend failures (DNS lookup blowing up, rspamd unreachable, no AR header from
any trusted relay) collapse to ``"none"`` — we never claim a sender is
verified without positive evidence, but we also don't claim forgery without
an explicit DMARC fail.
"""

import logging
import re
from typing import Any

from django.conf import settings

from jmap_email import first_address_email
from jmap_email.types import JmapEmail
from sentry_sdk import capture_exception

from core import enums
from core.mda.addresses import address_domain, ascii_lower, normalize_domain
from core.mda.arc import arc_result
from core.mda.dmarc import (
    DMARC_FAIL,
    DMARC_PASS,
    DMARC_TEMPERROR,
    POLICY_QUARANTINE,
    POLICY_REJECT,
    evaluate_dmarc,
    identifiers_aligned,
)
from core.mda.signing import DKIM_PASS, verify_dkim_signatures
from core.mda.spf import SPFCheck, evaluate_spf, spf_identity
from core.mda.utils import headers_blocks
from core.services.dns.records import DMARC_DEFAULT_ALIGNMENT
from core.services.dns.resolver import dns_budget

logger = logging.getLogger(__name__)


_PASS = "pass"  # noqa: S105 (auth-result token, not a password)
_FAIL = "fail"
_NONE = "none"  # explicitly no signature / policy
# DMARC lookup failed or the record is broken: unverified, whatever DKIM says.
_UNDECIDED = "undecided"


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
        "DMARC_BAD_POLICY": _UNDECIDED,
        "DMARC_DNSFAIL": _UNDECIDED,
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

_AR_OUTCOMES = {
    "pass": _PASS,
    "fail": _FAIL,
    "softfail": _FAIL,
    "policy": _FAIL,
    "permerror": _UNDECIDED,
    "temperror": _UNDECIDED,
    "none": _NONE,
    "neutral": _NONE,
}

_OUTCOME_RANK = {_NONE: 0, _PASS: 1, _UNDECIDED: 2, _FAIL: 3}


def _stronger(current: str | None, new: str) -> str:
    """fail dominates undecided dominates pass dominates none."""
    if current is None or _OUTCOME_RANK[new] > _OUTCOME_RANK[current]:
        return new
    return current


def _scrub_ar_value(value: str) -> str:
    """Erase RFC 5322 comments and quoted strings from an AR value.

    Run before the method/result regex so attacker-controlled free text inside
    them can't masquerade as a real token. Comments nest and both take
    quoted-pairs (RFC 5322 3.2.2). An unclosed one runs to the end of the
    value: prefer dropping suspicious bytes over honouring them.
    """
    out = []
    depth = 0
    in_quote = False
    chars = iter(value)
    for char in chars:
        if char == "\\" and (depth or in_quote):
            next(chars, None)
        elif in_quote:
            if char == '"':
                in_quote = False
                out.append(" ")
        elif char == "(":
            depth += 1
        elif depth:
            if char == ")":
                depth -= 1
                if not depth:
                    out.append(" ")
        elif char == '"':
            in_quote = True
        else:
            out.append(char)
    return "".join(out)


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
        outcome = _stronger(outcome, result)
    return outcome


def _aligned_with_from(domain: str, from_domain: str | None) -> bool:
    """Relaxed DMARC alignment of a signing domain with the From domain."""
    domain = normalize_domain(domain.strip().rstrip("."))
    return bool(domain and from_domain) and identifiers_aligned(
        domain, from_domain, DMARC_DEFAULT_ALIGNMENT
    )


def _without_unaligned_pass(outcome: str | None) -> str | None:
    """A DKIM pass that was not aligned with From proves nothing."""
    return _NONE if outcome == _PASS else outcome


def _rspamd_dkim_outcome(
    rspamd_result: dict[str, Any] | None, from_domain: str | None
) -> str | None:
    """DKIM from rspamd: ``pass`` for an aligned ``R_DKIM_ALLOW`` signature.

    Its options are ``"<domain>:s=<selector>"``, one per valid signature.
    """
    symbols = (rspamd_result or {}).get("symbols")
    allow = symbols.get("R_DKIM_ALLOW") if isinstance(symbols, dict) else None
    options = allow.get("options") if isinstance(allow, dict) else None
    if isinstance(options, list) and any(
        isinstance(option, str)
        and _aligned_with_from(option.split(":", 1)[0], from_domain)
        for option in options
    ):
        return _PASS
    return _without_unaligned_pass(_rspamd_outcome("dkim", rspamd_result))


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
            result = _AR_OUTCOMES.get(match.group("result").lower())
            if result:
                outcome = _stronger(outcome, result)
    return outcome if found else None


# header.d=example.com or header.i=user@example.com (RFC 8601 2.7.1).
_AR_SIGNER_RE = re.compile(
    r"(?:^|(?<=[\s;]))header\.(?P<ptype>[di])\s*=\s*(?P<value>[^\s;]+)",
    re.IGNORECASE,
)


def _ar_dkim_outcome(ar_values: list[str], from_domain: str | None) -> str | None:
    """DKIM from AR values: ``pass`` for an aligned ``dkim=pass`` result.

    Read per result (``;``-separated), so a ``header.d`` is only credited to
    the ``dkim=pass`` beside it. A result holding several method tokens is
    ambiguous and ignored.
    """
    for value in ar_values:
        for resinfo in _scrub_ar_value(value).split(";"):
            methods = list(_AR_METHOD_RE.finditer(resinfo))
            if len(methods) != 1:
                continue
            method = methods[0]
            if (method.group("method").lower(), method.group("result").lower()) != (
                "dkim",
                "pass",
            ):
                continue
            # header.i may omit the local part ("@example.com", RFC 6376
            # 3.5), which the address helpers reject.
            signers = [
                m.group("value").rpartition("@")[2]  # pylint: disable=address-manual-split
                if m.group("ptype").lower() == "i"
                else m.group("value")
                for m in _AR_SIGNER_RE.finditer(resinfo)
            ]
            if signers and all(_aligned_with_from(s, from_domain) for s in signers):
                return _PASS
    return _without_unaligned_pass(_ar_outcome("dkim", ar_values))


def _from_header_domain(parsed_email: JmapEmail) -> str | None:
    """Return the canonical domain of the RFC5322 From address, or ``None``.

    ASCII-folded, never ``str.lower()``: this domain is compared against a
    DKIM ``d=`` for alignment, and Unicode folding would let two different
    domains compare equal.
    """
    from_email = first_address_email(parsed_email.get("from"))
    if not from_email:
        return None
    domain = normalize_domain(address_domain(from_email.strip()).rstrip("."))
    return domain or None


def _from_header_domains(parsed_email: JmapEmail) -> set[str]:
    """Canonical domains of every From address."""
    domains = set()
    for entry in parsed_email.get("from") or []:
        email = (entry.get("email") if isinstance(entry, dict) else "") or ""
        domain = normalize_domain(address_domain(email.strip()).rstrip("."))
        if domain:
            domains.add(domain)
    return domains


def _authenticatable_from_domain(parsed_email: JmapEmail) -> str | None:
    """The From domain to authenticate, or ``None`` if there is no single one.

    ``None`` for several From domains (RFC 7489 6.6.1: authenticating one says
    nothing about the others) and for several From fields: the parser shows
    the first, while a DKIM ``h=from`` covers the last, so a From added above
    a validly signed one would display unauthenticated.
    """
    from_fields = [
        h
        for h in parsed_email.get("headers") or []
        if isinstance(h, dict) and str(h.get("name", "")).lower() == "from"
    ]
    if len(from_fields) > 1 or len(_from_header_domains(parsed_email)) != 1:
        return None
    return _from_header_domain(parsed_email)


def _native_spf(
    envelope: dict[str, Any] | None, spam_config: dict[str, Any]
) -> SPFCheck | None:
    """SPF for the SMTP session that delivered the message, when we saw it.

    Only for mail our MTA received (``origin == "mta"``): imports, widget
    submissions and internal mail have no SMTP peer. Skipped when
    ``trusted_relays`` is set, because the peer is then the upstream gateway
    and SPF would judge the gateway rather than the sender.
    """
    envelope = envelope or {}
    if envelope.get("origin") != enums.InboundOrigin.MTA or not envelope.get("ip"):
        return None
    if int(spam_config.get("trusted_relays", 0)) > 0:
        return None
    ip, mail_from, helo = (
        envelope["ip"],
        envelope.get("mail_from") or "",
        envelope.get("helo") or "",
    )
    return SPFCheck(
        domain=spf_identity(mail_from, helo)[1],
        run=lambda: evaluate_spf(ip, mail_from, helo),
    )


_AR_HEADER_FROM_RE = re.compile(
    r"(?:^|(?<=[\s;]))header\.from\s*=\s*(?P<domain>[^\s;]+)", re.IGNORECASE
)


def _sealed_dmarc_pass_for(aar: str, from_domain: str) -> bool:
    """Whether a sealed AAR reports exactly one ``dmarc=pass``, for our From.

    Exactly one ``dmarc=`` result: a second one beside a ``none`` could have
    been injected through a value the sealer did not escape. ``header.from``
    must name our From domain, or the seal vouches for another message.
    """
    scrubbed = _scrub_ar_value(aar)
    results = [
        m.group("result").lower()
        for m in _AR_METHOD_RE.finditer(scrubbed)
        if m.group("method").lower() == "dmarc"
    ]
    if results != ["pass"]:
        return False
    domains = {
        normalize_domain(m.group("domain").rstrip("."))
        for m in _AR_HEADER_FROM_RE.finditer(scrubbed)
    }
    return domains == {from_domain}


def _arc_vouches_for_dmarc(
    raw_data: bytes,
    spam_config: dict[str, Any],
    arc: dict[str, Any] | None,
    from_domain: str,
) -> bool:
    """Whether a trusted ARC sealer saw this message pass DMARC.

    Forwarding (mailing lists, redirects) breaks SPF by design and DKIM often,
    so an otherwise legitimate message fails DMARC on arrival. RFC 7489 6.7
    lets a receiver override the policy for such "trusted forwarders": with a
    populated ``trusted_arc_sealers``, a sealed ``dmarc=pass`` from one of
    them cancels the forgery verdict. Empty allowlist: no override, no cost.
    """
    trusted = trusted_arc_sealers(spam_config)
    if not trusted:
        return False
    if arc is None:
        arc = arc_result(raw_data, trusted)
    if not arc["trusted"] or not arc["aar"]:
        return False
    return _sealed_dmarc_pass_for(arc["aar"], from_domain)


def _native_outcomes(
    raw_data: bytes,
    parsed_email: JmapEmail,
    spam_config: dict[str, Any],
    envelope: dict[str, Any] | None,
    arc: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """DKIM and DMARC outcomes computed locally (crypto + DNS).

    DKIM is ``pass`` only for a valid signature whose ``d=`` aligns with the
    From domain, in the ``adkim`` mode its DMARC record asks for (relaxed by
    default, RFC 7489 6.3). A valid signature only proves that *some* domain
    signed the message: without alignment anyone owning a DKIM-enabled domain
    could have a forged From: shown as verified.

    DMARC is ``fail`` only when no aligned identifier passed *and* the From
    domain asks receivers to quarantine or reject. ``p=none`` is a domain
    saying "do not act on this": ``none``. A ``temperror`` is ``undecided``:
    not a forgery claim, but without the record we do not know ``adkim``.
    """
    from_domain = _authenticatable_from_domain(parsed_email)
    if not from_domain:
        logger.info("Native auth: no single From domain -> unverified")
        return _NONE, None
    signatures = verify_dkim_signatures(raw_data, prefer_domain=from_domain)
    dmarc = evaluate_dmarc(from_domain, signatures, _native_spf(envelope, spam_config))
    spf_result = dmarc.spf_result
    adkim = dmarc.policy.adkim if dmarc.policy else DMARC_DEFAULT_ALIGNMENT
    dkim = (
        _PASS
        if any(
            sig.result == DKIM_PASS
            and sig.domain
            and identifiers_aligned(sig.domain, from_domain, adkim)
            for sig in signatures
        )
        else _NONE
    )

    dmarc_outcome: str | None = None
    if dmarc.result == DMARC_PASS:
        dmarc_outcome = _PASS
    elif dmarc.result == DMARC_FAIL and dmarc.disposition in (
        POLICY_QUARANTINE,
        POLICY_REJECT,
    ):
        if _arc_vouches_for_dmarc(raw_data, spam_config, arc, from_domain):
            logger.info(
                "Native auth: DMARC fail for %s overridden by a trusted ARC seal",
                from_domain,
            )
            dmarc_outcome = _NONE
        else:
            dmarc_outcome = _FAIL
    elif dmarc.result == DMARC_TEMPERROR:
        dmarc_outcome = _UNDECIDED
    elif dmarc.policy is not None:
        dmarc_outcome = _NONE

    logger.info(
        "Native auth: from=%s spf=%s(%s) dkim=[%s] dmarc=%s disposition=%s "
        "policy_domain=%s reason=%s",
        from_domain,
        spf_result.result if spf_result else None,
        spf_result.domain if spf_result else None,
        ", ".join(f"{sig.domain}:{sig.result}" for sig in signatures),
        dmarc.result,
        dmarc.disposition,
        dmarc.policy.record_domain if dmarc.policy else None,
        dmarc.reason,
    )
    return dkim, dmarc_outcome


VERDICT_UNVERIFIED = "none"
VERDICT_FORGED = "fail"


def get_inbound_auth_mode(
    spam_config: dict[str, Any], key: str = "inbound_auth"
) -> str:
    """Return the normalized ``inbound_auth`` mode from a spam config.

    Empty or missing values become an empty string. Callers can treat the
    result with ``if not mode`` as "disabled" and compare directly against
    the supported mode names. ``key`` reads ``inbound_auth_shadow`` instead.
    """
    return (spam_config.get(key) or "").strip().lower()


def trusted_arc_sealers(spam_config: dict[str, Any]) -> set[str]:
    """Normalized ``trusted_arc_sealers`` allowlist from the spam config."""
    sealers = spam_config.get("trusted_arc_sealers")
    if isinstance(sealers, str):
        sealers = [sealers]
    elif not isinstance(sealers, (list, tuple, set)):
        sealers = []

    return {
        ascii_lower(s.strip().rstrip("."))
        for s in sealers
        if isinstance(s, str) and s.strip()
    }


def check_inbound_authentication(
    raw_data: bytes,
    parsed_email: JmapEmail,
    spam_config: dict[str, Any],
    rspamd_result: dict[str, Any] | None = None,
    arc: dict[str, Any] | None = None,
    envelope: dict[str, Any] | None = None,
    mode: str | None = None,
) -> str | None:
    """Return the sender-auth verdict for this message (``postmark["auth"]``).

    ``envelope`` is the SMTP envelope (``InboundMessage.envelope``), used by
    native SPF. ``mode`` overrides ``inbound_auth``, for shadow evaluation.
    """
    if mode is None:
        mode = get_inbound_auth_mode(spam_config)
    if not mode:
        return None

    dmarc: str | None
    if mode == "native":
        try:
            # Every key, record and policy lookup is sender-chosen and could
            # point at a deliberately slow nameserver: one budget for all.
            with dns_budget(settings.DNS_RESOLVER_INBOUND_AUTH_BUDGET):
                dkim, dmarc = _native_outcomes(
                    raw_data, parsed_email, spam_config, envelope, arc
                )
        except Exception as e:  # pylint: disable=broad-exception-caught
            # A bug here must neither block delivery nor claim forgery.
            logger.exception("Native inbound auth errored: %s", e)
            capture_exception(e)
            dkim, dmarc = _NONE, None
    elif mode in ("rspamd", "arc", "authentication-results"):
        from_domain = _authenticatable_from_domain(parsed_email)
        if mode == "rspamd":
            dkim = _rspamd_dkim_outcome(rspamd_result, from_domain)
            dmarc = _rspamd_outcome("dmarc", rspamd_result)
        else:
            if mode == "arc":
                if arc is None:
                    arc = arc_result(raw_data, trusted_arc_sealers(spam_config))
                ar_values = [arc["aar"]] if arc["trusted"] and arc["aar"] else []
            else:
                trusted_relays = int(spam_config.get("trusted_relays", 0))
                ar_values = _authentication_results_values(parsed_email, trusted_relays)
            dkim = _ar_dkim_outcome(ar_values, from_domain)
            dmarc = _ar_outcome("dmarc", ar_values)
    else:
        logger.warning("Unknown inbound_auth mode: %s", mode)
        return None

    logger.info("Inbound auth: mode=%s dkim=%s dmarc=%s", mode, dkim, dmarc)

    # DMARC fail is an explicit disavowal by the sender's domain — stronger
    # signal than a missing DKIM, so it wins regardless of DKIM's state.
    if dmarc == _FAIL:
        return VERDICT_FORGED
    if dkim != _PASS or dmarc == _UNDECIDED:
        return VERDICT_UNVERIFIED
    return None
