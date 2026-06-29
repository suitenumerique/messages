"""Raw-byte manipulation of RFC 5322 message head sections.

Used by the inbound MTA path to strip Messages-internal ``X-StMsg-*``
hint headers from received bytes before re-processing, without going
through the stdlib ``email`` parser/serialiser round-trip (which would
re-fold every retained header and break the DKIM body hash on signed
input).
"""

import typing


def remove_mime_headers(
    raw_email: bytes,
    *,
    prefixes: typing.Iterable[str] = (),
    names: typing.Iterable[str] = (),
) -> bytes:
    """Remove headers from the head section of a raw MIME message.

    A header is dropped when its name (case-insensitive, ASCII) either
    equals one of *names* or starts with one of *prefixes*. RFC 5322
    §2.2.3 folded continuation lines (lines beginning with SP or HTAB)
    are dropped along with the header they continue.

    Operates on the head as raw bytes split at the first blank line;
    the body and the bytes of every retained header are left byte-
    identical. DKIM body hashing is unaffected, and signed headers we
    keep are not refolded or re-encoded.

    Returns the input unchanged when nothing matched.
    """
    name_set = {n.lower().encode("ascii") for n in names}
    prefix_tuple = tuple(p.lower().encode("ascii") for p in prefixes)
    if not name_set and not prefix_tuple:
        return raw_email

    split = raw_email.find(b"\r\n\r\n")
    if split < 0:
        split = raw_email.find(b"\n\n")
    if split < 0:
        head, body = raw_email, b""
    else:
        head, body = raw_email[:split], raw_email[split:]

    out: list[bytes] = []
    dropping = False
    for line in head.splitlines(keepends=True):
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                out.append(line)
            continue
        name, sep, _ = line.partition(b":")
        if not sep:
            # Malformed line — preserve and stop any in-progress drop.
            dropping = False
            out.append(line)
            continue
        name_lc = name.lower()
        if name_lc in name_set or (prefix_tuple and name_lc.startswith(prefix_tuple)):
            dropping = True
            continue
        dropping = False
        out.append(line)

    cleaned = b"".join(out)
    if cleaned == head:
        return raw_email
    return cleaned + body
