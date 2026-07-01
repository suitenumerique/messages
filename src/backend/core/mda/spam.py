"""Direction-agnostic spam-scanning primitives.

The rspamd ``/checkv2`` call and the hardcoded header-match rules live here,
independent of any pipeline, so both the inbound pipeline and (in the future)
outbound submission can scan a message. This module deliberately does NOT
interpret the rspamd action into a verdict/decision — that mapping is
direction-specific (inbound routes to Junk/defer/drop; outbound will block the
send), so it stays with each caller (see ``inbound_pipeline._make_rspamd_step``).
"""

# pylint: disable=broad-exception-caught

import logging
import re
from typing import Any, Dict, Optional, Tuple

import requests
from jmap_email import JmapEmail, has_header

from core.mda.utils import headers_blocks

logger = logging.getLogger(__name__)


def check_hardcoded_rules(
    parsed_email: JmapEmail, spam_config: Dict[str, Any]
) -> Optional[bool]:
    """Apply the per-domain hardcoded ``rules`` list, header-matched
    only against headers from trusted relay blocks. Returns ``True`` /
    ``False`` on first matching rule, ``None`` if no rule matched."""
    rules = spam_config.get("rules", [])
    for idx, rule in enumerate(rules):
        header_match = rule.get("header_match") or rule.get("header_match_regex")
        if not header_match:
            continue
        if ":" not in header_match:
            # Log the rule position, not its raw value: ``spam_config``
            # also carries spam-service credentials, so we never echo
            # values read from it into logs.
            logger.warning(
                "Invalid header_match format (missing colon) in spam rule #%d", idx
            )
            continue
        key, raw_value = header_match.split(":", 1)
        key = key.lower().strip()
        # For a literal ``header_match`` we compare case-insensitively by
        # lowercasing both sides. For ``header_match_regex`` we must NOT
        # lowercase the pattern (it would change semantics, e.g. ``\D``→``\d``);
        # the regex is matched against the original header value with
        # ``re.IGNORECASE`` instead.
        is_regex = not rule.get("header_match")
        pattern = raw_value.strip()
        value = pattern.lower()

        # ``Return-Path`` is baked into block 0 by the MDA from the *envelope*
        # MAIL FROM, which is unauthenticated (a spammer sets it freely at SMTP
        # time; the widget uses a raw form field) and unverified at this layer
        # (no SPF/DMARC). Matching it as a trusted header would let a spoofed
        # sender satisfy an ``action: ham`` allowlist and bypass the spam
        # steps, so it is never eligible for a hardcoded-rule match.
        if key == "return-path":
            logger.warning(
                "Ignoring spam rule #%d: 'return-path' is a spoofable envelope "
                "value and cannot be used as a trusted header_match",
                idx,
            )
            continue

        # Existence check first; the trusted value is read from the
        # Received-bounded blocks below.
        if not has_header(parsed_email, key):
            continue

        # Trust window is "block 0 (our MTA's Received + headers above it)
        # + N upstream relay blocks". Default 0: a sender can prepend
        # their own Received lines (landing in block 1+), so trusting
        # those by default would let them forge an allowlist match.
        # Slicing beyond list length is fine — yields all blocks.
        trusted_relays = spam_config.get("trusted_relays", 0)
        blocks_to_check = trusted_relays + 1
        found_value = None
        for block in headers_blocks(parsed_email)[:blocks_to_check]:
            if key in block and block[key]:
                # Blocks are ordered most-recent → oldest; first match wins.
                found_value = block[key][0]
                break
        if found_value is None:
            continue

        header_value_original = (
            found_value if isinstance(found_value, str) else str(found_value)
        ).strip()
        if not is_regex:
            is_match = header_value_original.lower() == value
        else:  # header_match_regex
            try:
                is_match = (
                    re.fullmatch(pattern, header_value_original, re.IGNORECASE)
                    is not None
                )
            except re.error:
                # Skip a malformed rule rather than aborting the whole spam
                # check. Log the rule position only, never the value —
                # ``spam_config`` may carry secrets.
                logger.warning("Invalid regex in spam rule #%d — skipping", idx)
                continue
        if is_match:
            action = rule.get("action") or "spam"
            if action in ("spam", "reject"):
                return True
            if action in ("ham", "no action"):
                return False
    return None


def call_rspamd(
    raw_data: bytes,
    spam_config: Dict[str, Any],
    envelope: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """POST raw RFC-822 bytes to rspamd's ``/checkv2``.

    The SMTP ``envelope`` (MAIL FROM, RCPT TO, connecting IP / HELO / rDNS)
    is forwarded via rspamd's documented scan headers
    (``From``/``Rcpt``/``IP``/``Helo``/``Hostname``) so envelope-based checks
    — SPF, DNS RBLs, the Return-Path-vs-From mismatch symbols — score against
    the real peer. Without them rspamd sees an empty envelope and SPF/RBL
    degrade to no-ops.

    Returns ``(action, error_message, result_dict)``. ``action`` is the raw
    rspamd action string (e.g. "no action", "add header", "reject", …) — this
    function does NOT interpret it; the whole action → verdict/decision mapping
    lives with each caller. ``action`` is ``None`` when rspamd is not
    configured or on error (the ``error_message`` channel distinguishes the
    two); errors are swallowed so a flaky rspamd never blocks delivery.
    """
    url = spam_config.get("rspamd_url")
    if not url:
        logger.debug("SPAM_CONFIG.rspamd_url not configured, skipping rspamd")
        return None, None, None

    headers = {"Content-Type": "message/rfc822"}
    auth = spam_config.get("rspamd_auth")
    if auth:
        headers["Authorization"] = auth

    # Forward the SMTP envelope as rspamd scan headers. Only send the fields
    # we actually have (widget/internal mail carry no HELO/rDNS); an empty
    # value would read as a genuinely empty envelope and skew scoring. HELO
    # and hostname are attacker-influenced, so strip CR/LF to prevent header
    # injection into the rspamd request.
    for header_name, envelope_key in (
        ("From", "mail_from"),
        ("Rcpt", "rcpt_to"),
        ("IP", "ip"),
        ("Helo", "helo"),
        ("Hostname", "hostname"),
    ):
        value = (envelope or {}).get(envelope_key)
        if value:
            headers[header_name] = str(value).replace("\r", "").replace("\n", "")

    try:
        response = requests.post(
            f"{url}/checkv2", data=raw_data, headers=headers, timeout=10
        )
        response.raise_for_status()
        result = response.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        # Network failures, non-2xx (raise_for_status), and a non-JSON
        # body (ValueError covers JSONDecodeError) all funnel here. We
        # don't let a flaky rspamd block delivery — surface the error via the
        # error_message channel and let the caller decide (the inbound step
        # RETRYs rather than failing open).
        logger.exception("Error calling rspamd: %s", exc)
        # Return a stable, sanitized token (exception class name only) — the
        # full detail is logged locally above; the message can carry the
        # endpoint URL / other details and is echoed downstream.
        return None, type(exc).__name__, None
    except Exception as exc:
        logger.exception("Unexpected error calling rspamd: %s", exc)
        return None, type(exc).__name__, None

    if not isinstance(result, dict):
        logger.warning("rspamd returned non-object body: %r", result)
        return None, "rspamd returned non-object body", None

    action = result.get("action", "")
    score = result.get("score", 0.0)
    logger.info("Rspamd: action=%s score=%.2f", action, score)
    return action, None, result
