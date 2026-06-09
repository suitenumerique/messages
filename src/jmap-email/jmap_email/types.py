"""JMAP RFC 8621 Email-object type shapes.

These ``TypedDict`` definitions are the public contract for the data
flowing in and out of :func:`parse_email` and :func:`compose_email`.
They mirror the JMAP Email Object spec (RFC 8621 §4) one-for-one,
plus a project-extension namespace (``_ext``) the parser exposes when
``extensions=True``.

Importing these in downstream code makes the parser/composer contract
visible to static type checkers (mypy, pyright). The package ships a
``py.typed`` marker (PEP 561) so type information is picked up
automatically.

The TypedDicts use ``total=True`` with explicit
:class:`typing.NotRequired` markers (PEP 655) so that "required" vs
"optional" is part of the wire contract rather than an implicit
default. Where a property is required by RFC 8621 but its value may
legitimately be ``null``, the type is ``T | None``. Where a property
is independently optional (may be absent from the dict entirely), it
is wrapped in :class:`typing.NotRequired`.

Project extensions — keys we emit beyond what RFC 8621 specifies — are
flagged in the docstring of the carrying class. They are marked with
``NotRequired`` so consumers writing strict-JMAP code can omit them
without type-check failure.

The top-level :class:`JmapEmail` uses the functional ``TypedDict``
form because the JMAP Email object has a property named ``from`` —
a Python reserved word that can't appear as a class-level key.
"""

from typing import NotRequired, Required, TypedDict


class EmailAddress(TypedDict, total=False):
    """JMAP ``EmailAddress`` object (RFC 8621 §4.1.2.3).

    Represents one mailbox in a ``From`` / ``To`` / ``Cc`` / ``Bcc`` /
    ``Reply-To`` / ``Sender`` address list. ``email`` is required;
    ``name`` is optional. The composer rejects entries missing
    ``email``.
    """

    email: Required[str]
    name: NotRequired[str | None]


class EmailHeader(TypedDict):
    """JMAP ``EmailHeader`` object (RFC 8621 §4.1.1).

    One entry in a ``headers`` list-of-objects. Both fields are
    mandatory: the parser always emits both, and the composer rejects
    entries missing either. ``value`` is the RFC 8621 Raw form: byte-
    faithful aside from CRLF+WSP unfolding and outer-CRLF stripping;
    NOT RFC 2047-decoded.
    """

    name: str
    value: str


class EmailBodyValue(TypedDict, total=False):
    """JMAP ``EmailBodyValue`` object (RFC 8621 §4.1.4).

    The value side of the per-``partId`` body table emitted under
    ``parsed['bodyValues']``. All three fields are always populated
    by :func:`parse_email` when ``body_values=True``.
    """

    value: Required[str]
    isEncodingProblem: Required[bool]
    isTruncated: Required[bool]


class EmailBodyPart(TypedDict, total=False):
    """JMAP ``EmailBodyPart`` object (RFC 8621 §4.1.4).

    Represents one MIME part in the body tree. The same shape carries
    ``textBody``, ``htmlBody``, ``attachments`` (all ``EmailBodyPart[]``
    per the spec) and the recursive ``bodyStructure`` tree.

    Per RFC 8621 §4.1.4:

    - ``partId`` is ``null`` if and only if ``type`` is ``multipart/*``.
    - ``blobId`` is ``null`` if and only if ``type`` is ``multipart/*``
      (in a real JMAP server it identifies the blob; the library does
      not have a blob store and emits ``None`` — caller assigns).
    - ``subParts`` is populated if and only if ``type`` is
      ``multipart/*``.

    Project extensions (NOT part of RFC 8621):

    - ``content`` — inline part body. ``str`` for ``text/*`` parts,
      ``bytes`` for binary parts. Present unless
      ``parse_email(body_values=True)`` was used (in which case the
      text parts' content moves to ``bodyValues`` per spec).
    - ``sha256`` — hex digest of the part's decoded bytes. Present on
      attachment-class parts only. Useful for dedup / blob storage.
    """

    partId: Required[str | None]
    blobId: Required[str | None]
    size: Required[int]
    headers: Required[list[EmailHeader]]
    name: Required[str | None]
    type: Required[str]
    charset: Required[str | None]
    disposition: Required[str | None]
    cid: Required[str | None]
    language: Required[list[str] | None]
    location: Required[str | None]
    subParts: Required[list["EmailBodyPart"] | None]
    # Project extensions — see class docstring.
    content: NotRequired[str | bytes]
    sha256: NotRequired[str]


class Attachment(TypedDict, total=False):
    """Composer attachment input shape.

    Distinct from :class:`EmailBodyPart` because the composer takes
    raw content bytes (or a base64 string) rather than a ``partId``
    reference, and the JMAP spec does not standardize this input
    shape. The parser emits :class:`EmailBodyPart` on its
    ``attachments`` output.
    """

    # base64-encoded str or raw bytes
    content: Required[str | bytes]
    type: Required[str]
    name: NotRequired[str | None]
    # ``"attachment"`` (default) or ``"inline"``.
    disposition: NotRequired[str]
    # Required for ``inline`` parts; ignored otherwise.
    cid: NotRequired[str | None]


# Resent-* typed projection (project extension, NOT in RFC 8621 §4.1.3).
#
# RFC 8621 §4.1.3 names exactly 11 header convenience properties — from,
# sender, to, cc, bcc, replyTo, subject, sentAt, messageId, inReplyTo,
# references — and provides §4.1.2 as the generic mechanism to request
# any other header as a typed projection (``header:Resent-From:asAddresses``
# etc.). The library pre-computes the Resent-* group and surfaces it under
# ``parsed["_ext"]["resent"]`` so callers handling forwarded/resent mail
# don't have to walk the raw ``headers`` list.
JmapResentProjection = TypedDict(
    "JmapResentProjection",
    {
        "from": list[EmailAddress] | None,
        "sender": list[EmailAddress] | None,
        "replyTo": list[EmailAddress] | None,
        "to": list[EmailAddress] | None,
        "cc": list[EmailAddress] | None,
        "bcc": list[EmailAddress] | None,
        "messageId": list[str] | None,
        "date": str | None,
    },
    total=False,
)


class JmapEmailExt(TypedDict, total=False):
    """Project-extension namespace surfaced under ``parsed['_ext']``
    when :func:`parse_email` is called with ``extensions=True``.

    These fields are NOT part of the JMAP spec — they expose
    information the parser already computes so downstream consumers
    don't have to re-walk the message.
    """

    # Class names of stdlib email parser defects collected from every
    # subpart (e.g. ``"InvalidBase64PaddingDefect"``).
    defects: Required[list[str]]
    # Resent-* typed projection — see :class:`JmapResentProjection`.
    resent: NotRequired[JmapResentProjection]


# Top-level JMAP Email object (RFC 8621 §4.1). Declared via the
# functional ``TypedDict`` syntax because the JMAP spec uses the key
# ``from`` — a Python keyword that can't appear in a class-level
# ``TypedDict`` body. Consumers should subscribe (``email["from"]``)
# rather than attribute-access.
#
# Per RFC 8621 §4.1.2.2, every address field (from, sender, to, cc,
# bcc, replyTo) is ``EmailAddress[] | None``; ``None`` specifically
# when the header is absent (vs. an empty list when the header is
# present but lists no valid address).
#
# ``messageId`` / ``inReplyTo`` / ``references`` are ``String[] | None``
# per RFC 8621 §4.1.2.1; ``sentAt`` is an ISO-8601 UTCDate string or
# ``None``.
JmapEmail = TypedDict(
    "JmapEmail",
    {
        # ─── Header convenience properties (RFC 8621 §4.1.3) ───
        "subject": str | None,
        "from": list[EmailAddress] | None,
        "sender": list[EmailAddress] | None,
        "replyTo": list[EmailAddress] | None,
        "to": list[EmailAddress] | None,
        "cc": list[EmailAddress] | None,
        "bcc": list[EmailAddress] | None,
        "messageId": list[str] | None,
        "inReplyTo": list[str] | None,
        "references": list[str] | None,
        "sentAt": str | None,
        # ─── Raw header projection (RFC 8621 §4.1.1) ───
        "headers": list[EmailHeader],
        # ─── Body parts (RFC 8621 §4.1.4) ───
        "textBody": list[EmailBodyPart],
        "htmlBody": list[EmailBodyPart],
        "attachments": list[EmailBodyPart],
        "hasAttachment": bool,
        "preview": str,
        "bodyValues": dict[str, EmailBodyValue],
        "bodyStructure": EmailBodyPart | None,
        # Project-extension namespace (``extensions=True``).
        "_ext": JmapEmailExt,
    },
    total=False,
)


__all__ = [
    "Attachment",
    "EmailAddress",
    "EmailBodyPart",
    "EmailBodyValue",
    "EmailHeader",
    "JmapEmail",
    "JmapEmailExt",
    "JmapResentProjection",
]
