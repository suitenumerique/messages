"""Attachment display names.

A MIME part may legitimately carry no filename — no ``filename`` in
``Content-Disposition``, no ``name`` in ``Content-Type`` — and the JMAP
parser faithfully reports ``None`` for it (RFC 8621: ``name`` is
``String | null``). Every consumer on our side needs a string, so the
placeholder is ours to synthesize; ``jmap_email`` deliberately stops at
sanitizing the names that do exist.

Everything a user-visible attachment name depends on lives here, so the
serializer, the blob download endpoint and the draft builder all
synthesize the *same* name for the same part.
"""

from jmap_email import sanitize_filename

# Filename stem used for attachments whose MIME part carries no filename.
# Matched by the frontend, which swaps in its own name for some types —
# see the calendar-invite download button in
# ``features/layouts/components/thread-view/components/calendar-invite``.
UNNAMED_ATTACHMENT_STEM = "unnamed"

# Must track ``Attachment.name``'s ``max_length``;
# ``tests/services/test_attachments.py`` asserts the two stay equal. Names
# are truncated to it rather than rejected: ``full_clean`` on save would
# otherwise roll back an entire draft over one overlong name.
ATTACHMENT_NAME_MAX_LENGTH = 255

# MIME type → extension for the synthesized name.
#
# Spelled out rather than deferred to the stdlib ``mimetypes``, which
# completes its table at import time from system files (``/etc/mime.types``
# and friends). That makes its answers a property of the host image: the
# same nameless part could be stored as ``unnamed.jpg`` by the web
# container and ``unnamed.jpeg`` by the worker, and a CI base-image change
# could silently rewrite what users download. These names are persisted in
# ``Attachment.name``, so the mapping is pinned here instead.
#
# An unlisted type yields no extension at all — a bare ``unnamed`` is
# honest, whereas guessing wrong hands the OS a file it opens with the
# wrong application. Add entries as real mail brings them in.
_MIME_EXTENSIONS = {
    # Images
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",  # non-standard, but common in the wild
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/svg+xml": ".svg",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/avif": ".avif",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    # Text and data
    "text/plain": ".txt",
    "text/html": ".html",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "text/xml": ".xml",
    "text/rtf": ".rtf",
    "application/json": ".json",
    # ``mimetypes`` answers ``.xsl`` here — an XSLT stylesheet, a different
    # format from a generic XML document.
    "application/xml": ".xml",
    # Calendar invites are the most frequent nameless part of all, and the
    # standard vCard type is the one the stdlib table misses (it knows only
    # the legacy ``text/x-vcard``).
    "text/calendar": ".ics",
    "text/vcard": ".vcf",
    "text/x-vcard": ".vcf",
    # Documents
    "application/pdf": ".pdf",
    "application/rtf": ".rtf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/vnd.ms-outlook": ".msg",
    "application/epub+zip": ".epub",
    # Archives — including the Outlook/Windows spelling of ZIP.
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/gzip": ".gz",
    "application/x-gzip": ".gz",
    "application/x-tar": ".tar",
    "application/x-7z-compressed": ".7z",
    "application/vnd.rar": ".rar",
    "application/x-rar-compressed": ".rar",
    "application/x-bzip2": ".bz2",
    "application/x-apple-diskimage": ".dmg",
    # Mail
    "message/rfc822": ".eml",
    # Signatures and S/MIME. ``application/pkcs7-mime`` over mail carries a
    # signed/encrypted message, not the bare certificate ``mimetypes``
    # assumes with ``.p7c``.
    "application/pgp-signature": ".asc",
    "application/pgp-encrypted": ".asc",
    "application/pkcs7-mime": ".p7m",
    "application/pkcs7-signature": ".p7s",
    "application/x-pkcs7-signature": ".p7s",
    # Audio and video
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "video/mp4": ".mp4",
    "video/mpeg": ".mpeg",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-msvideo": ".avi",
    "video/3gpp": ".3gp",
    # Deliberately no extension: the generic "some bytes" type says nothing
    # about the format, and ``.bin`` would only look like it did. Listed so
    # the intent is explicit rather than a fall-through.
    "application/octet-stream": "",
}


def guess_mime_extension(content_type):
    """Return the file extension (with leading dot) for a MIME type, or ``""``.

    Accepts a full ``Content-Type`` header value — parameters (charset,
    boundary, name…) are dropped before lookup. Unknown types yield
    ``""``. The mapping is a fixed table, so the answer is the same on
    every host; see ``_MIME_EXTENSIONS`` for why that matters.
    """
    if not content_type:
        return ""

    mime_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    return _MIME_EXTENSIONS.get(mime_type, "")


def get_attachment_display_name(name, content_type=None):
    """Return a non-empty, sanitized filename for an attachment part.

    ``name`` is whatever we hold for the part — the parser's ``name``
    (``None`` for a nameless part), or a client-supplied one. When it is
    absent or sanitizes away to nothing, the stem is synthesized and the
    extension inferred from *content_type*, so the recipient's OS can
    still open the file.

    Sanitizing is a near no-op for names the parser reported (it already
    applied the same pass) and a real guard for client-supplied ones,
    which reach us straight off the wire: the draft endpoint takes
    ``attachments[].name`` without serializer validation, so without the
    length cap here one overlong name fails ``full_clean`` and rolls back
    the whole draft save with a 400.
    """
    # ``isinstance`` rather than truthiness: the draft endpoint takes
    # ``attachments[].name`` straight off the wire with no serializer
    # validation, so a client can send a number or a list. Those are
    # truthy but unsubscriptable, and ``sanitize_filename`` would raise
    # on them; treat anything that is not a string as no name at all.
    if isinstance(name, str) and name:
        sanitized = sanitize_filename(name, max_length=ATTACHMENT_NAME_MAX_LENGTH)
        if sanitized:
            return sanitized

    return f"{UNNAMED_ATTACHMENT_STEM}{guess_mime_extension(content_type)}"
