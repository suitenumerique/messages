"""DMARC record discovery for inbound sender authentication.

Discovery only (RFC 7489 §6.6.3), not evaluation. Native inbound auth uses
this to learn which DKIM alignment mode the From domain asks for; deciding
that a message actually *fails* DMARC needs an SPF evaluator we do not have,
since a message can pass DMARC through aligned SPF carrying no DKIM
signature at all. Record syntax lives in ``core.services.dns.records``.

RFC 9989 (May 2026) obsoletes RFC 7489 and replaces the organizational-domain
fallback below with a DNS Tree Walk (§4.10), querying up the hierarchy for a
``psd=`` tag instead of consulting the Public Suffix List. The record syntax
is unchanged, so only the two-name walk here would move.
"""

import logging

from core.mda.addresses import organizational_domain
from core.services.dns.records import (
    DMARC_DEFAULT_ALIGNMENT,
    dmarc_alignment,
    is_dmarc_record,
)
from core.services.dns.resolver import (
    InvalidNameError,
    NoAnswerError,
    NXDOMAINError,
    ResolverError,
    resolve_txt_values,
)

logger = logging.getLogger(__name__)


def _policy_domains(from_domain: str) -> list[str]:
    """The names to try for a DMARC record, in RFC 7489 §6.6.3 order.

    The From domain first, then its organizational domain — a record at
    ``example.com`` covers ``mail.example.com``, which is how a domain owner
    publishes one policy for a whole estate. Identical or unusable
    organizational domains are dropped so we never query ``_dmarc.`` or the
    same name twice.
    """
    org = organizational_domain(from_domain)
    if not org or org == from_domain:
        return [from_domain]
    return [from_domain, org]


def dkim_alignment_mode(from_domain: str) -> str:
    """The ``adkim`` mode the From domain publishes: ``"s"`` or ``"r"``.

    Relaxed on anything short of a clear answer, which is both RFC 7489 6.3's
    default and the safe direction here: this only ever narrows what counts as
    an aligned signature, so answering "relaxed" can at worst leave the
    pre-existing behaviour in place, while wrongly answering "strict" would
    mark legitimate mail unverified on a DNS blip.

    Three cases return the default:

    - the lookup did not complete (timeout, SERVFAIL, bogus DNSSEC). Not the
      same as learning there is no policy, and the same settled/unsettled line
      ``verify_message_dkim`` and ``check_single_record`` draw.
    - no DMARC record at either name.
    - more than one DMARC record at a name. RFC 7489 §6.6.3 step 5 stops
      DMARC processing entirely when the set is not exactly one, rather than
      picking whichever the resolver happened to order first — the same
      ambiguity guard the DKIM key lookup applies to a selector.
    """
    for domain in _policy_domains(from_domain):
        qname = f"_dmarc.{domain}"
        try:
            values = resolve_txt_values(qname)
        except (NXDOMAINError, NoAnswerError, InvalidNameError):
            # Settled: the name answered, and it publishes no DMARC record.
            continue
        except ResolverError as exc:
            logger.info(
                "DMARC lookup for %s did not complete (%s): assuming relaxed alignment",
                qname,
                exc,
            )
            return DMARC_DEFAULT_ALIGNMENT

        records = [value for value in values if is_dmarc_record(value)]
        if len(records) > 1:
            logger.info(
                "%s publishes %d DMARC records; DMARC does not apply",
                qname,
                len(records),
            )
            return DMARC_DEFAULT_ALIGNMENT
        if records:
            return dmarc_alignment(records[0], "adkim")

    return DMARC_DEFAULT_ALIGNMENT
