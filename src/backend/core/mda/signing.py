"""Handles DKIM signing and verification of email messages."""

import base64
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dkim import DKIM, DKIMException
from dkim import sign as dkim_sign
from sentry_sdk import capture_exception

from core.enums import DKIMAlgorithmChoices
from core.mda.addresses import ascii_lower
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


def dkim_dns_txt(fqdn: str) -> Answer:
    """Look up a DKIM key record.

    The single seam tests patch. It exists so they can intercept one named
    function instead of reaching into the shared resolver, whose cache and
    root-walking would otherwise fire real queries from the test suite.
    """
    return resolve_answer(fqdn, "TXT")


class DKIMSigningError(Exception):
    """Signing failed for a domain that has an active DKIM key.

    Distinct from "this domain isn't set up for DKIM", which is a
    configuration state that legitimately sends unsigned mail. This one means
    the domain's DNS advertises a policy we then failed to honour, so the
    send must not proceed.
    """


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
        # suspect — quarantined or dropped at a DMARC-enforcing receiver.
        # Callers turn this into a 400 (the send is synchronous in the
        # request, so there is nothing to retry) and the operator gets a log
        # line naming the domain.
        logger.error("Error during DKIM signing for domain %s: %s", domain, e)
        raise DKIMSigningError(
            f"DKIM signing failed for domain {domain} which has an active key"
        ) from e


def verify_message_dkim(
    raw_mime_message: bytes, require_dnssec: bool = False
) -> str | None:
    """Verify a DKIM signature on a raw MIME message using public DNS.

    This verifies that the DKIM signature will pass validation when the receiving
    server checks it via DNS, ensuring the signature is valid and the DNS records
    are correctly configured.

    Args:
        raw_mime_message: The raw bytes of the MIME message with DKIM signature.
        require_dnssec: Refuse a key whose lookup was not DNSSEC-secure. A
            per-call argument rather than a straight read of
            ``MESSAGES_DKIM_VERIFY_OUTGOING_REQUIRE_DNSSEC``, because the two
            callers want opposite things. Checking our *own* domain before
            sending, an unsigned answer is worth refusing: we control that
            zone and can sign it. Checking an arbitrary sender inbound, most
            zones are still unsigned, so requiring it would mark ordinary mail
            from most of the internet as unverified. Only the outbound
            self-check passes the setting through.

    Returns:
        The signing domain (the signature's ``d=`` tag, lowercased) if the DKIM
        signature is valid, otherwise ``None``. Returning the domain rather than
        a bare bool lets callers enforce identifier alignment against the From:
        header — a valid signature only proves that *some* domain signed the
        message, not that the visible From: address is authentic (that is
        DMARC's job). Callers that only care whether *any* valid signature
        exists can treat the result as truthy/falsy.
    """
    try:
        # Create a DNS function that performs actual DNS lookups
        def get_dns_txt(fqdn, **kwargs):
            # Convert FQDN to string if it's bytes
            fqdn_str = fqdn.decode("ascii") if isinstance(fqdn, bytes) else fqdn
            # Remove trailing dot if present
            if fqdn_str.endswith("."):
                fqdn_str = fqdn_str[:-1]

            try:
                # The resolver validates DNSSEC itself, walking from the root,
                # so ``secure`` is a property of the chain we verified rather
                # than an AD bit asserted by an upstream resolver we reach
                # over plain UDP/53. Without that, verification of a monitored
                # domain trusts whatever a poisoned resolver returns — enough
                # to mint signatures that pass this check.
                answer = dkim_dns_txt(fqdn_str)
                if require_dnssec and not answer.secure:
                    logger.warning(
                        "Refusing DKIM key lookup for %s: answer is not DNSSEC-secure",
                        fqdn_str,
                    )
                    return None

                # One string per record, character-strings joined per RFC 6376
                # §3.6.2.2 — an RSA-2048 key is always split in two.
                txt_values = answer.text_values()

                # Only actual key records count: a selector name commonly
                # carries unrelated TXT, a domain-verification token say, and
                # counting those against the ambiguity guard below would make
                # a working key unusable.
                #
                # ``p`` is the discriminator, not ``v``: RFC 6376 3.6.1 makes
                # v= optional (defaulting to DKIM1) but p= required, so
                # ``parse_dkim_tags`` accepts any ``tag=value`` string —
                # including ``google-site-verification=...`` — and only the
                # presence of a public key separates a key record from a
                # bystander.
                key_records = [
                    v
                    for v in txt_values
                    if (tags := parse_dkim_tags(v)) is not None and "p" in tags
                ]

                # A DKIM selector must publish exactly one key record.
                # Returning key_records[0] out of several silently picked one
                # at resolver-ordering's whim — including an attacker's, if
                # they managed to add a record. Ambiguity is a failure.
                if len(key_records) > 1:
                    logger.warning(
                        "DKIM selector %s publishes %d key records; refusing "
                        "to guess which one is the key",
                        fqdn_str,
                        len(key_records),
                    )
                    return None
                if key_records:
                    return key_records[0].encode("utf-8", "surrogateescape")
            except (NXDOMAINError, NoAnswerError):
                # Settled: the name answered, and it publishes no key.
                logger.warning("No DKIM key record published at %s", fqdn_str)
                return None
            except (ServfailError, ResolutionTimeoutError):
                # Not settled: the lookup did not complete, which is not the
                # same as learning there is no key. SERVFAIL belongs here and
                # not above — ``check_single_record`` draws the same line, and
                # grouping it with NXDOMAIN said "no such record" about a
                # server that never answered the question.
                logger.warning(
                    "DKIM key lookup for %s did not complete, key state unknown",
                    fqdn_str,
                )
                return None
            except DNSSECError:
                # Bogus signatures on the selector's zone. Whatever key is in
                # there, we did not get it from the domain owner.
                logger.warning("DNSSEC validation failed for DKIM record %s", fqdn_str)
                return None
            except ResolverError as exc:
                logger.warning("DNS lookup failed for %s: %s", fqdn_str, exc)
                return None

            return None

        # Verify the DKIM signature using public DNS. We drive the DKIM object
        # directly (rather than the module-level ``verify`` helper) so we can
        # read back the ``d=`` domain of the signature that validated: ``verify``
        # records it on ``self.domain``.
        dkim_obj = DKIM(raw_mime_message)
        if not dkim_obj.verify(dnsfunc=get_dns_txt):
            return None
        signing_domain = dkim_obj.domain
        if not signing_domain:
            return None
        return ascii_lower(signing_domain.decode("ascii", "replace").rstrip("."))

    except DKIMException as e:
        # ``DKIM.verify`` documents that it raises for a message, signature or
        # key it cannot make sense of, and does not catch its own exceptions —
        # only the module-level ``dkim.verify()`` helper does, and we drive the
        # object directly to read back ``d=``. So a body-hash mismatch reaches
        # here, and inbound that is routine: a mailing list appending a footer
        # breaks the hash, and a malformed DKIM-Signature is what spam looks
        # like. Not a bug, so logged and never reported.
        logger.info("DKIM signature did not validate: %s", e)
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Not a DNS failure (``get_dns_txt`` classifies those) and not dkimpy
        # rejecting the message, so this is our own code misbehaving. Reported
        # rather than only logged: on the outbound self-check it silently marks
        # every recipient for retry.
        logger.error("Error during DKIM verification: %s", e, exc_info=True)
        capture_exception(e)
        return None
