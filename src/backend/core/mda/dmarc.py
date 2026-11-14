"""DMARC policy discovery and evaluation for inbound sender authentication.

RFC 7489: discover the From domain's policy (6.6.3), check whether an
authenticated identifier aligns with it (3.1), and report the result with the
disposition the domain requests (6.3, 6.6.4). DKIM results come from
``core.mda.signing.verify_dkim_signatures``, SPF from ``core.mda.spf``.

RFC 9989 (May 2026) obsoletes RFC 7489 and replaces the organizational-domain
fallback with a DNS Tree Walk (4.10) querying up the hierarchy for a ``psd=``
tag. The record syntax is unchanged, so only the two-name walk here would
move. Its ``t=y`` testing flag is honoured; ``np`` and ``psd`` are not.

Lookups are about third-party senders at inbound volume, so they go through
the resolver's shared cache.
"""

import dataclasses
import logging
from typing import Iterable, Optional

from core.mda import spf
from core.mda.addresses import ascii_lower, organizational_domain
from core.mda.signing import DKIM_PASS, DKIM_TEMPERROR, DKIMSignatureResult
from core.services.dns.records import (
    DMARC_ALIGNMENT_STRICTNESS,
    DMARC_DEFAULT_ALIGNMENT,
    DMARC_POLICY_STRICTNESS,
    DMARC_STRICT_ALIGNMENT,
    is_dmarc_record,
    parse_dmarc_tags,
)
from core.services.dns.resolver import (
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    ResolverError,
    resolve_answer_cached,
)

logger = logging.getLogger(__name__)

# RFC 8601 2.7.1 vocabulary.
DMARC_PASS = "pass"  # noqa: S105 (auth-result token, not a password)
DMARC_FAIL = "fail"
DMARC_NONE = "none"
DMARC_TEMPERROR = "temperror"

POLICY_NONE = "none"
POLICY_QUARANTINE = "quarantine"
POLICY_REJECT = "reject"
# 6.6.4: a message not selected by ``pct`` gets the next least strict policy.
_DOWNGRADE = {
    POLICY_REJECT: POLICY_QUARANTINE,
    POLICY_QUARANTINE: POLICY_NONE,
    POLICY_NONE: POLICY_NONE,
}


class DMARCLookupIncomplete(Exception):
    """A policy lookup did not complete, so the policy is unknown."""


def dmarc_txt_values(qname: str) -> list[str]:
    """TXT values at ``qname``. The seam tests patch."""
    return resolve_answer_cached(qname, "TXT").text_values()


@dataclasses.dataclass(frozen=True)
class DMARCPolicy:
    """A discovered DMARC record and where it was found."""

    from_domain: str
    record_domain: str
    tags: dict

    def _enumerated(self, tag: str, allowed, default: Optional[str]):
        value = self.tags.get(tag, "").strip().lower()
        return value if value in allowed else default

    @property
    def is_subdomain_policy(self) -> bool:
        """6.3: ``sp`` applies when the record came from the parent."""
        return self.record_domain != self.from_domain

    @property
    def policy(self) -> str:
        """The requested policy, before ``pct`` / ``t`` sampling.

        A missing or invalid ``p`` requests nothing (6.6.3 would only apply
        ``none`` anyway), and an invalid ``sp`` falls back to ``p``.
        """
        p = self._enumerated("p", DMARC_POLICY_STRICTNESS, POLICY_NONE)
        if self.is_subdomain_policy:
            return self._enumerated("sp", DMARC_POLICY_STRICTNESS, p)
        return p

    @property
    def adkim(self) -> str:
        """DKIM alignment mode, ``"s"`` or ``"r"`` (relaxed by default)."""
        return self._enumerated(
            "adkim", DMARC_ALIGNMENT_STRICTNESS, DMARC_DEFAULT_ALIGNMENT
        )

    @property
    def aspf(self) -> str:
        """SPF alignment mode, ``"s"`` or ``"r"`` (relaxed by default)."""
        return self._enumerated(
            "aspf", DMARC_ALIGNMENT_STRICTNESS, DMARC_DEFAULT_ALIGNMENT
        )

    @property
    def sampled_out(self) -> bool:
        """Whether this message falls outside the enforced share.

        ``pct`` is a random sample in RFC 7489. A verdict shown to users must
        be deterministic, so any ``pct`` below 100 is read as "not selected"
        (6.6.4: apply the next least strict policy), as is RFC 9989's
        ``t=y``. Errs towards a weaker verdict on a domain still rolling out.
        Provisional (see docs/spam.md), may change after shadow comparison.
        """
        if self.tags.get("t", "").strip().lower() == "y":
            return True
        raw = self.tags.get("pct", "").strip()
        if not raw:
            return False
        try:
            pct = int(raw)
        except ValueError:
            return False
        return 0 <= pct < 100

    @property
    def disposition(self) -> str:
        """The policy to apply to a failing message (6.6.4)."""
        policy = self.policy
        return _DOWNGRADE[policy] if self.sampled_out else policy


def _policy_domains(from_domain: str) -> list[str]:
    """The names to try for a DMARC record, in RFC 7489 6.6.3 order.

    The From domain first, then its organizational domain. Identical or
    unusable organizational domains are dropped so we never query ``_dmarc.``
    or the same name twice.
    """
    org = organizational_domain(from_domain)
    if not org or org == from_domain:
        return [from_domain]
    return [from_domain, org]


def discover_policy(from_domain: str) -> Optional[DMARCPolicy]:
    """The DMARC policy covering ``from_domain``, or ``None`` if it has none.

    ``None`` also when a name publishes more than one DMARC record: 6.6.3
    step 5 stops DMARC processing rather than pick one, and the walk does not
    continue to the parent.

    Raises :class:`DMARCLookupIncomplete` when a lookup did not complete
    (timeout, SERVFAIL, bogus DNSSEC). That is not the same as learning there
    is no policy, and the walk does not fall back to the parent past it.
    """
    for domain in _policy_domains(from_domain):
        qname = f"_dmarc.{domain}"
        try:
            values = dmarc_txt_values(qname)
        except (NXDOMAINError, NoAnswerError, InvalidNameError):
            # Settled: the name answered, and it publishes no DMARC record.
            continue
        except ResolverError as exc:
            raise DMARCLookupIncomplete(f"{qname}: {exc}") from exc

        records = [value for value in values if is_dmarc_record(value)]
        if len(records) > 1:
            logger.info(
                "%s publishes %d DMARC records; DMARC does not apply",
                qname,
                len(records),
            )
            return None
        if records:
            return DMARCPolicy(
                from_domain=from_domain,
                record_domain=domain,
                tags=parse_dmarc_tags(records[0]) or {},
            )
    return None


def identifiers_aligned(identifier: str, from_domain: str, mode: str) -> bool:
    """RFC 7489 3.1: strict wants equality, relaxed the same org domain."""
    identifier = ascii_lower(identifier).rstrip(".")
    from_domain = ascii_lower(from_domain).rstrip(".")
    if identifier == from_domain:
        return True
    if mode == DMARC_STRICT_ALIGNMENT:
        return False
    return organizational_domain(identifier) == organizational_domain(from_domain)


@dataclasses.dataclass(frozen=True)
class DMARCResult:
    """Outcome of DMARC evaluation for one message.

    ``disposition`` is set only for ``fail``: the policy the From domain asks
    receivers to apply.
    """

    result: str
    policy: Optional[DMARCPolicy] = None
    disposition: Optional[str] = None
    dkim_domain: Optional[str] = None
    spf_aligned: bool = False
    reason: str = ""


def evaluate_dmarc(
    from_domain: str,
    dkim_results: Iterable[DKIMSignatureResult],
    spf_result: Optional[spf.SPFResult],
) -> DMARCResult:
    """RFC 7489 4.2: pass if an aligned DKIM signature or SPF identity passes.

    ``temperror`` ("undecided") rather than ``fail`` when the outcome hinged
    on something we could not establish: an aligned signature whose key we
    could not fetch, an aligned SPF identity that errored (``temperror`` or
    ``permerror``), SPF not evaluated at all (``spf_result`` is ``None``), or
    the policy lookup itself. Only a decided failure may read as forgery.
    """
    try:
        policy = discover_policy(from_domain)
    except DMARCLookupIncomplete as exc:
        return DMARCResult(DMARC_TEMPERROR, reason=str(exc))
    if policy is None:
        return DMARCResult(DMARC_NONE, reason="no DMARC policy")

    dkim_results = list(dkim_results)
    undecided = False
    for signature in dkim_results:
        if not signature.domain or not identifiers_aligned(
            signature.domain, from_domain, policy.adkim
        ):
            continue
        if signature.result == DKIM_PASS:
            return DMARCResult(DMARC_PASS, policy=policy, dkim_domain=signature.domain)
        if signature.result == DKIM_TEMPERROR:
            undecided = True

    if spf_result is None:
        # SPF was not evaluated (no SMTP peer, or a trusted relay in front):
        # a message may pass DMARC on SPF alone, so failing it here would
        # call SPF-only senders forged.
        undecided = True
    elif spf_result.domain and identifiers_aligned(
        spf_result.domain, from_domain, policy.aspf
    ):
        if spf_result.result == spf.PASS:
            return DMARCResult(DMARC_PASS, policy=policy, spf_aligned=True)
        # permerror too: the RFC limits (10 lookups, 2 voids) are stricter
        # than many published records, and a broken record is the
        # publisher's misconfiguration, not a forgery an attacker can cause.
        if spf_result.result in (spf.TEMPERROR, spf.PERMERROR):
            undecided = True

    if undecided:
        return DMARCResult(
            DMARC_TEMPERROR,
            policy=policy,
            reason="an aligned identifier could not be checked",
        )
    return DMARCResult(
        DMARC_FAIL,
        policy=policy,
        disposition=policy.disposition,
        reason="no aligned identifier passed",
    )
