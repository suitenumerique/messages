"""Attachment filename mechanics: hygiene and extension inference.

Two public helpers around the same domain knowledge:

- :func:`sanitize_filename` — defang a filename that arrived over the
  wire (path components, control characters, length) while keeping it
  recognizable. The parser applies it to every part name it reports;
  consumers storing client-supplied names can apply it too.
- :func:`guess_mime_extension` — map a MIME ``Content-Type`` to the file
  extension a user would expect, correcting the stdlib ``mimetypes``
  table where mail-borne reality disagrees with it.

Per RFC 8621 a part's ``name`` is ``String | null`` and ``parse_email``
never substitutes a placeholder for a nameless part — synthesizing a
display name is the consumer's decision. These helpers are the building
blocks for that decision, not a policy applied by the parser.
"""

import mimetypes
import re
from ntpath import basename as nt_basename
from posixpath import basename as posix_basename

# Extensions to use instead of what the stdlib ``mimetypes`` table returns,
# for MIME types that routinely travel over mail without a filename. An empty
# value means "no extension at all", which ``mimetypes`` alone cannot express.
_MIME_EXTENSION_OVERRIDES = {
    # The generic "unknown type": ``mimetypes`` maps it to ``.bin``, which
    # looks like a real format while telling the user nothing.
    "application/octet-stream": "",
    # ``mimetypes`` answers ``.xsl`` (an XSLT stylesheet) — plain wrong for a
    # generic XML document, and worse than no extension at all.
    "application/xml": ".xml",
    # Over mail this carries an S/MIME message, not the bare certificate
    # ``mimetypes`` assumes with ``.p7c``.
    "application/pkcs7-mime": ".p7m",
    # Types the stdlib table simply does not know, all common over mail:
    # calendar invites (the most frequent nameless part of all), the
    # Outlook/Windows spelling of ZIP, the standard vCard type (only the
    # legacy ``text/x-vcard`` is mapped), and signature parts.
    "text/calendar": ".ics",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "text/vcard": ".vcf",
    "application/rtf": ".rtf",
    "application/vnd.ms-outlook": ".msg",
    "application/x-apple-diskimage": ".dmg",
    "audio/amr": ".amr",
    "application/pgp-signature": ".asc",
    "application/pkcs7-signature": ".p7s",
    "application/x-pkcs7-signature": ".p7s",
}


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize an attachment filename, preserving the extension when truncating.

    Strips path components (both POSIX and Windows separators), control
    characters, and characters unsafe in filenames, then truncates to
    *max_length* keeping a reasonable-length extension intact. May return
    an empty string when nothing recognizable survives (e.g. ``"..."``);
    callers wanting a guaranteed non-empty name must supply their own
    fallback.
    """

    filename = nt_basename(posix_basename(filename))

    filename = filename.strip('"/.\\')

    # Remove null bytes and control characters
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)

    # Remove dangerous characters
    filename = re.sub(r'[<>:"|?*\\/]', "_", filename)

    # Truncate while preserving extension
    if len(filename) > max_length:
        # Find the last dot for extension (but not at the start like .gitignore)
        last_dot = filename.rfind(".")
        if last_dot > 0:
            name = filename[:last_dot]
            ext = filename[last_dot:]
            # Only preserve extension if it's reasonable length (up to 10 chars including dot)
            if len(ext) <= 10:
                max_name_length = max_length - len(ext)
                if max_name_length > 0:
                    return name[:max_name_length] + ext
        return filename[:max_length]

    return filename


def guess_mime_extension(content_type: str) -> str:
    """Return the file extension (with leading dot) for a MIME type, or ``""``.

    Accepts a full ``Content-Type`` header value — parameters (charset,
    name…) are dropped before lookup. The overrides table above wins over
    the stdlib ``mimetypes`` answer; an unknown type yields ``""``. Note
    the stdlib table is completed at import time from system files
    (``/etc/mime.types``…), so answers for non-overridden types may vary
    across hosts.
    """
    if not content_type:
        return ""

    mime_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    return _MIME_EXTENSION_OVERRIDES.get(
        mime_type, mimetypes.guess_extension(mime_type) or ""
    )
