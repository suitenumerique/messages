"""Handles DKIM signing and verification of email messages."""

import base64
import logging
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dkim import DKIM, DKIMException, MessageFormatError
from dkim import sign as dkim_sign
from dkim.util import parse_tag_value
from sentry_sdk import capture_exception

from core.enums import DKIMAlgorithmChoices
from core.mda.addresses import ascii_lower, organizational_domain
from core.services.dns.records import parse_dkim_tags
from core.services.dns.resolver import (
    Answer,
    DNSSECError,
    NoAnswerError,
    NXDOMAINError,
    ResolutionTimeoutError,
    ResolverError,
    ServfailError,
    resolve_answer,
)

logger = logging.getLogger(__name__)

# Per-signature DKIM results (RFC 8601 2.7.1 vocabulary).
DKIM_PASS = "pass"  # noqa: S105 (auth-result token, not a password)
DKIM_FAIL = "fail"
DKIM_TEMPERROR = "temperror"
DKIM_PERMERROR = "permerror"

# Signatures verified per message. Each costs one key lookup whose name the
# sender chooses, and real mail carries one to three (author, ESP, list).
MAX_DKIM_SIGNATURES = 5
# Base64 length of the largest public key accepted (8192-bit RSA fits).
MAX_DKIM_KEY_B64 = 1500


def dkim_dns_txt(fqdn: str) -> Answer:
    """Look up a DKIM key record.

    The single seam tests patch, so they intercept one named function instead
    of reaching into the resolver.
    """
    return resolve_answer(fqdn, "TXT")


class DKIMSigningError(Exception):
    """Signing failed for a domain that has an active DKIM key.

    Distinct from "this domain isn't set up for DKIM", which legitimately
    sends unsigned mail. Here the domain's DNS advertises a key we then failed
    to use, so the send must not proceed.
    """


class DKIMKeyLookupIncomplete(Exception):
    """A key lookup did not complete, so the key's state is unknown.

    Not a ``DKIMException`` on purpose: dkimpy catches those and reports a
    plain verification failure, which would read a DNS outage as a broken
    signature.
    """


def dkim_key_record(name, require_dnssec: bool = False) -> bytes | None:
    """The DKIM key record at ``name``, in dkimpy's ``dnsfunc`` shape.

    Returns ``None`` for a settled answer holding no usable key. Raises
    :class:`DKIMKeyLookupIncomplete` when the lookup did not complete.
    """
    fqdn = name.decode("ascii") if isinstance(name, bytes) else name
    fqdn = fqdn.rstrip(".")
    try:
        answer = dkim_dns_txt(fqdn)
    except (NXDOMAINError, NoAnswerError):
        logger.warning("No DKIM key record published at %s", fqdn)
        return None
    except DNSSECError:
        # Bogus signatures on the selector's zone. Whatever key is in there,
        # we did not get it from the domain owner.
        logger.warning("DNSSEC validation failed for DKIM record %s", fqdn)
        return None
    except (ServfailError, ResolutionTimeoutError) as exc:
        logger.warning(
            "DKIM key lookup for %s did not complete, key state unknown", fqdn
        )
        raise DKIMKeyLookupIncomplete(str(exc)) from exc
    except ResolverError as exc:
        logger.warning("DNS lookup failed for %s: %s", fqdn, exc)
        raise DKIMKeyLookupIncomplete(str(exc)) from exc

    if require_dnssec and not answer.secure:
        logger.warning(
            "Refusing DKIM key lookup for %s: answer is not DNSSEC-secure", fqdn
        )
        return None

    # Only actual key records count: a selector name commonly carries
    # unrelated TXT (a domain-verification token). ``p`` is the
    # discriminator, not ``v``: RFC 6376 3.6.1 makes v= optional but p=
    # required, and ``parse_dkim_tags`` accepts any ``tag=value`` string.
    key_records = [
        v
        for v in answer.text_values()
        if (tags := parse_dkim_tags(v)) is not None and "p" in tags
    ]
    # A selector must publish exactly one key. Picking one of several at the
    # resolver's ordering whim could pick an attacker's.
    if len(key_records) > 1:
        logger.warning(
            "DKIM selector %s publishes %d key records; refusing to guess "
            "which one is the key",
            fqdn,
            len(key_records),
        )
        return None
    # RSA verification is pure Python in dkimpy and the key is the sender's:
    # cap it (a 8192-bit key is ~1400 base64 characters).
    if key_records and len(parse_dkim_tags(key_records[0])["p"]) > MAX_DKIM_KEY_B64:
        logger.warning("DKIM key at %s is oversized; refusing it", fqdn)
        return None
    if key_records:
        return key_records[0].encode("utf-8", "surrogateescape")
    return None


def generate_dkim_key(
    algorithm: DKIMAlgorithmChoices = DKIMAlgorithmChoices.RSA, key_size: int = 2048
) -> tuple[str, str]:
    """Generate a new DKIM key pair.

    Args:
        algorithm: The signing algorithm (DKIMAlgorithmChoices)
        key_size: The key size in bits (e.g., 2048, 4096 for RSA)

    Returns:
        Tuple of (private_key_pem, public_key_base64)

    Raises:
        ValueError: If the algorithm is not supported
    """

    if algorithm != DKIMAlgorithmChoices.RSA:
        raise ValueError(
            f"Unsupported algorithm: {algorithm}. Only RSA is currently supported."
        )

    # Generate RSA private key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)

    # Convert private key to PEM format
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    # Extract public key for DNS records
    public_key_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_b64 = base64.b64encode(public_key_der).decode("ascii")

    return private_key_pem, public_key_b64


def sign_message_dkim(raw_mime_message: bytes, maildomain) -> bytes | None:
    """Sign a raw MIME message with DKIM.

    Uses the most recent active DKIM key for the domain.
    Only signs if the domain has an active DKIM key configured.

    Args:
        raw_mime_message: The raw bytes of the MIME message.
        maildomain: The MailDomain object with DKIM key.

    Returns:
        The DKIM-Signature header bytes if signed, otherwise None.
    """
    domain = maildomain.name

    # Find the most recent active DKIM key for this domain
    dkim_key = maildomain.get_active_dkim_key()

    if not dkim_key:
        logger.warning(
            "Domain %s has no active DKIM key configured, skipping DKIM signing", domain
        )
        return None

    try:
        dkim_private_key = dkim_key.get_private_key_bytes()

        signature = dkim_sign(
            message=raw_mime_message,
            selector=dkim_key.selector.encode("ascii"),
            domain=domain.encode("ascii"),
            privkey=dkim_private_key,
            # Sender and the Resent-* set are signed too. Without them a
            # MITM can add or rewrite ``Sender:`` — which some clients
            # display in place of From — and the signature still verifies.
            # Signing a header that is absent also binds its absence (RFC
            # 6376 §5.4: an over-listed header name prevents it being added
            # later), which is the point here.
            include_headers=[
                b"To",
                b"Cc",
                b"From",
                b"Sender",
                b"Subject",
                b"Message-ID",
                b"Reply-To",
                b"In-Reply-To",
                b"References",
                b"Date",
                b"Resent-Date",
                b"Resent-From",
                b"Resent-Sender",
                b"Resent-To",
                b"Resent-Cc",
                b"Resent-Message-ID",
            ],
            canonicalize=(b"relaxed", b"simple"),
        )
        # dkim_sign returns the full message including the signature header,
        # we only want the header itself.
        signature_header = (
            signature.split(b"\r\n\r\n", 1)[0].split(b"DKIM-Signature:")[1].strip()
        )
        logger.info(
            "Successfully signed message for domain %s with selector %s",
            domain,
            dkim_key.selector,
        )
        return b"DKIM-Signature: " + signature_header
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Fail closed. The domain HAS an active key, so its DNS advertises a
        # DKIM policy and receivers will treat unsigned mail from it as
        # suspect (quarantined or dropped at a DMARC-enforcing receiver).
        logger.error("Error during DKIM signing for domain %s: %s", domain, e)
        raise DKIMSigningError(
            f"DKIM signing failed for domain {domain} which has an active key"
        ) from e


def verify_message_dkim(
    raw_mime_message: bytes, require_dnssec: bool = False
) -> str | None:
    """Verify the first DKIM signature on a raw MIME message using public DNS.

    Args:
        raw_mime_message: The raw bytes of the MIME message with DKIM signature.
        require_dnssec: Refuse a key whose lookup was not DNSSEC-secure. Only
            meaningful with the ``recursive`` resolver backend; the ``system``
            backend never reports an answer as secure.

    Returns:
        The signing domain (the signature's ``d=`` tag, lowercased) if the DKIM
        signature is valid, otherwise ``None``. A valid signature only proves
        that *some* domain signed the message; alignment with From: is the
        caller's job.
    """

    def get_dns_txt(fqdn, **kwargs):  # pylint: disable=unused-argument
        try:
            return dkim_key_record(fqdn, require_dnssec=require_dnssec)
        except DKIMKeyLookupIncomplete:
            return None

    try:
        # Driven through the DKIM object (not the module-level ``verify``) so
        # we can read back the ``d=`` of the signature that validated.
        dkim_obj = DKIM(raw_mime_message)
        if not dkim_obj.verify(dnsfunc=get_dns_txt):
            return None
        signing_domain = dkim_obj.domain
        if not signing_domain:
            return None
        return ascii_lower(signing_domain.decode("ascii", "replace").rstrip("."))

    except DKIMException as e:
        # ``DKIM.verify`` raises for a message, signature or key it cannot
        # make sense of. Inbound that is routine (a list footer breaking the
        # body hash, a malformed signature), so logged and never reported.
        logger.info("DKIM signature did not validate: %s", e)
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Not DNS (classified in ``dkim_key_record``) and not dkimpy rejecting
        # the message, so this is our own code misbehaving.
        logger.error("Error during DKIM verification: %s", e, exc_info=True)
        capture_exception(e)
        return None


@dataclass(frozen=True)
class DKIMSignatureResult:
    """Outcome of one DKIM-Signature header."""

    domain: str | None
    result: str


def _signature_tags(header_value: bytes) -> dict | None:
    try:
        return parse_tag_value(header_value)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _signature_domain(tags: dict | None) -> str | None:
    domain = (tags or {}).get(b"d")
    if not domain:
        return None
    return ascii_lower(domain.decode("ascii", "replace").strip().rstrip(".")) or None


def _unacceptable_signature(tags: dict) -> str | None:
    """Why a signature that may well verify must still not count, if it must not.

    - ``h=`` without From: the signature does not bind the identity DMARC
      aligns on, so it can be replayed under any From (RFC 6376 5.4 makes
      signing From mandatory; dkimpy only enforces it when signing).
    - ``l=``: only a prefix of the body is signed, so anything can be
      appended below it (RFC 6376 8.2). Rarely used by legitimate senders.
    """
    signed = [h.strip().lower() for h in tags.get(b"h", b"").split(b":") if h.strip()]
    if b"from" not in signed:
        return "h= does not sign From"
    if b"l" in tags:
        return "l= body length limit not accepted"
    return None


def verify_dkim_signatures(
    raw_mime_message: bytes,
    max_signatures: int = MAX_DKIM_SIGNATURES,
    prefer_domain: str | None = None,
) -> list[DKIMSignatureResult]:
    """Verify the DKIM-Signatures of a message, up to ``max_signatures``.

    DMARC passes on *any* aligned passing signature (RFC 7489 4.2), so a
    message signed by both an ESP and the author domain must not be judged on
    the topmost signature alone. Signatures whose ``d=`` shares
    ``prefer_domain``'s organizational domain (the From domain) are verified
    first, so relay or list signatures added on top cannot push the author's
    out of the cap.

    Results per RFC 8601 2.7.1: ``pass``, ``fail`` (signature or key does not
    verify, including no key published), ``temperror`` (key lookup did not
    complete) and ``permerror`` (signature header unusable, or unacceptable:
    see ``_unacceptable_signature``). An empty list means unsigned.
    """
    try:
        dkim_obj = DKIM(raw_mime_message)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.info("Message could not be parsed for DKIM: %s", e)
        return []

    signatures = [
        value for name, value in dkim_obj.headers if name.lower() == b"dkim-signature"
    ]
    parsed = [_signature_tags(value) for value in signatures]
    order = list(range(len(signatures)))
    if prefer_domain:
        preferred = organizational_domain(prefer_domain)

        def _not_preferred(idx):
            domain = _signature_domain(parsed[idx])
            return not domain or organizational_domain(domain) != preferred

        order.sort(key=_not_preferred)

    results: list[DKIMSignatureResult] = []
    for idx in order[:max_signatures]:
        tags = parsed[idx]
        domain = _signature_domain(tags)
        reason = "tag list does not parse" if tags is None else None
        reason = reason or _unacceptable_signature(tags)
        if reason:
            # Decided without any key lookup.
            logger.info("DKIM signature %d (d=%s) not counted: %s", idx, domain, reason)
            results.append(DKIMSignatureResult(domain, DKIM_PERMERROR))
            continue
        try:
            ok = dkim_obj.verify(idx=idx, dnsfunc=_dkimpy_dnsfunc)
        except DKIMKeyLookupIncomplete:
            results.append(DKIMSignatureResult(domain, DKIM_TEMPERROR))
            continue
        except MessageFormatError as e:
            # The signature header itself does not parse.
            logger.info("DKIM signature %d unusable: %s", idx, e)
            results.append(DKIMSignatureResult(domain, DKIM_PERMERROR))
            continue
        except DKIMException as e:
            # Raised for a signature that does not verify (body hash mismatch,
            # bad key, missing tags): RFC 8601 "fail".
            logger.info("DKIM signature %d (d=%s) did not verify: %s", idx, domain, e)
            results.append(DKIMSignatureResult(domain, DKIM_FAIL))
            continue
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error during DKIM verification: %s", e, exc_info=True)
            capture_exception(e)
            results.append(DKIMSignatureResult(domain, DKIM_TEMPERROR))
            continue
        results.append(DKIMSignatureResult(domain, DKIM_PASS if ok else DKIM_FAIL))
    if len(signatures) > max_signatures:
        logger.info(
            "Message carries %d DKIM signatures; only %d were verified",
            len(signatures),
            max_signatures,
        )
    return results


def _dkimpy_dnsfunc(name, **kwargs):  # pylint: disable=unused-argument
    return dkim_key_record(name)
