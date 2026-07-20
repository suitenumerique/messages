"""ARC chain verification for inbound relay-trust (RFC 8617)."""

import logging
from typing import Any, Dict, Optional, Set

from dkim import CV_Pass, arc_verify

logger = logging.getLogger(__name__)


def _sealer_trusted(sealer: Optional[str], trusted: Set[str]) -> bool:
    """True if sealer equals or is a subdomain of a trusted sealer."""
    if not sealer:
        return False
    if sealer in trusted:
        return True
    return any(sealer.endswith("." + t) for t in trusted)


def arc_result(raw_data: bytes, trusted_sealers: Set[str]) -> Dict[str, Any]:
    """Verify the ARC chain; empty trusted_sealers accepts any valid seal."""
    result: Dict[str, Any] = {
        "trusted": False,
        "sealer": None,
        "aar": None,
        "dnsfail": False,
    }

    try:
        cv, results, _reason = arc_verify(raw_data)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("ARC verify errored (treating as untrusted): %s", exc)
        return result

    if not results:
        return result

    outer = results[0]
    sealer_raw = outer.get("ams-domain")
    if isinstance(sealer_raw, (bytes, bytearray)):
        sealer = sealer_raw.decode("ascii", "replace")
    else:
        sealer = sealer_raw or None
    if sealer:
        sealer = sealer.strip().rstrip(".").lower() or None
    result["sealer"] = sealer

    allowed = not trusted_sealers or _sealer_trusted(sealer, trusted_sealers)
    if cv == CV_Pass and allowed:
        result["trusted"] = True
        aar_raw = outer.get("aar-value")
        if isinstance(aar_raw, (bytes, bytearray)):
            result["aar"] = aar_raw.decode("utf-8", "replace")
        elif isinstance(aar_raw, str):
            result["aar"] = aar_raw

    return result
