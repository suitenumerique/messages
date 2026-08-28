"""Case and Unicode normalization for email addresses.

One policy, applied everywhere:

- **Local part**: ASCII only, ASCII-lowercased for anything we own or look up
  (mailboxes, login identities, inbound recipients). RFC 5321 §2.4 makes the
  local part case-sensitive on the wire but discourages exploiting it; every
  large provider folds it, so we do too.
- **Domain**: case-insensitive per DNS. Lowercased, and IDNA-encoded (UTS-46,
  non-transitional) to its A-label when it carries non-ASCII.
- Addresses we merely carry (contacts, MIME headers) keep the case they were
  typed or received in. Only addresses we resolve get folded.

Folding is restricted to ``A-Z`` on purpose. Unicode ``str.lower()`` maps
non-ASCII code points onto ASCII (U+212A KELVIN SIGN → ``k``), so a
Unicode-lowercased lookup lets ``nicK@example.com`` resolve to
``nick@example.com``. That is the Django CVE-2019-19844 account-takeover
class of bug, and here it would reach mailbox lookup and OIDC autojoin.
"""

import string

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible

import idna
from jmap_email import is_valid_addr_spec

__all__ = [
    "AddrSpecValidator",
    "address_domain",
    "address_local_part",
    "ascii_lower",
    "envelope_address",
    "needs_smtputf8",
    "normalize_address",
    "normalize_domain",
    "split_address",
]

_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def ascii_lower(value: str) -> str:
    """Lowercase ``A-Z``, leaving every other code point untouched."""
    return value.translate(_ASCII_LOWER)


def split_address(address: str) -> tuple[str, str] | None:
    """Split an addr-spec into ``(local_part, domain)``, or None if malformed.

    Splits on the LAST ``@``: that is the domain separator for an unquoted
    addr-spec, and a quoted local part may legally contain one.
    """
    local_part, separator, domain = address.strip().rpartition("@")
    if not separator or not local_part or not domain:
        return None
    return local_part, domain


def address_local_part(address: str) -> str:
    """Return the local part of *address*, or ``""`` if it has no domain.

    Case is preserved. Fold it with :func:`ascii_lower` for a lookup key.
    """
    parts = split_address(address)
    return parts[0] if parts else ""


def address_domain(address: str) -> str:
    """Return the domain of *address*, or ``""`` if it has none.

    Returned as written. Wrap in :func:`normalize_domain` for a lookup or
    comparison key.
    """
    parts = split_address(address)
    return parts[1] if parts else ""


def normalize_domain(domain: str) -> str:
    """Return the canonical lookup form of *domain*: lowercase, A-label.

    IDNA is only attempted for a non-ASCII domain, so an ASCII value that
    ``idna`` would refuse (an underscore label, a bare hostname used in
    tests) still round-trips unchanged. A non-ASCII domain with no IDNA
    encoding is returned as-is and simply fails to match anything.
    """
    domain = ascii_lower(domain.strip())
    if domain.isascii():
        return domain
    try:
        return idna.encode(domain, uts46=True).decode("ascii")
    except idna.IDNAError:
        return domain


def normalize_address(address: str) -> str:
    """Return the canonical lookup form of an email address.

    A value with no ``@`` is only ASCII-lowercased; rejecting it is the
    caller's job, so this stays a total function usable in ``clean_fields``.
    """
    address = address.strip()
    local_part, separator, domain = address.rpartition("@")
    if not separator:
        return ascii_lower(address)
    return f"{ascii_lower(local_part)}@{normalize_domain(domain)}"


def needs_smtputf8(address: str) -> bool:
    """True when *address* can only travel an RFC 6531 session.

    Only the local part matters: a non-ASCII domain has an A-label and
    downgrades cleanly, a non-ASCII local part has no ASCII form at all.
    """
    parts = split_address(address)
    return parts is not None and not parts[0].isascii()


@deconstructible
class AddrSpecValidator:
    """Validate a single RFC 5322 / RFC 6532 addr-spec.

    Replaces Django's ``validate_email`` for addresses we merely carry: that
    one rejects ``josé@`` yet accepts ``nicK@`` (its local-part pattern is
    ASCII compiled with IGNORECASE, which under Unicode matches anything
    case-folding into range). We accept the SMTPUTF8 sessions that carry
    such senders, so refusing them here would just discard the address.

    Still enforces what matters: exactly one mailbox (no comma, no unquoted
    whitespace) and no control characters, so a stored value cannot become
    two recipients when joined into a mailbox-list.
    """

    message = "Enter a valid email address."
    code = "invalid"

    def __call__(self, value):
        if not is_valid_addr_spec(value):
            raise ValidationError(self.message, code=self.code, params={"value": value})

    def __eq__(self, other):
        return isinstance(other, AddrSpecValidator)

    def __hash__(self):
        return hash(AddrSpecValidator)


def envelope_address(address: str) -> str | None:
    """Return the SMTP wire form of *address*, or None if it has none.

    The local part is kept exactly as supplied. Its case belongs to the
    destination host, and a non-ASCII one is left intact for an RFC 6531
    session: ask :func:`needs_smtputf8` whether the hop has to advertise
    the extension before sending this.

    Only the domain is rewritten, to a lowercase A-label. None means the
    address has no wire form at all (malformed, or a non-ASCII domain with
    no IDNA encoding), which is a permanent failure for that recipient.
    """
    parts = split_address(address)
    if parts is None:
        return None
    local_part, domain = parts
    domain = normalize_domain(domain)
    if not domain.isascii():
        return None
    return f"{local_part}@{domain}"
