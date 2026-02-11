"""Utility functions for RFC5322 email processing."""

import base64
import hashlib
import logging
import re
import typing
import uuid

logger = logging.getLogger(__name__)

# Matches src="data:<mime>;base64,<data>" in HTML img tags
_BASE64_IMG_RE = re.compile(
    r'(<img\b[^>]*\bsrc=")data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/\n\r =]+)("[^>]*>)'
)

# Matches ![alt](data:<mime>;base64,<data>) in markdown text
_MD_BASE64_IMG_RE = re.compile(
    r"(!\[[^\]]*\]\()data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/\n\r =]+)(\))"
)

# Map common image MIME types to file extensions
_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


def _resolve_image(
    content: bytes,
    content_type: str,
    images: list[dict],
    known_images: dict[str, str] | None,
) -> str:
    """Return the CID for *content*, reusing an existing one when possible.

    If *known_images* is provided and already contains an entry whose SHA-256
    digest matches *content*, the existing CID is returned without creating a
    duplicate.  Otherwise a new image dict is appended to *images* (and
    registered in *known_images* if supplied).
    """
    digest = hashlib.sha256(content).hexdigest()

    if known_images is not None and digest in known_images:
        return known_images[digest]

    cid = str(uuid.uuid4())
    ext = _MIME_TO_EXT.get(content_type, "bin")

    images.append(
        {
            "cid": cid,
            "content": content,
            "content_type": content_type,
            "name": f"image-{cid}.{ext}",
            "size": len(content),
        }
    )

    if known_images is not None:
        known_images[digest] = cid

    return cid


def _make_replacer(
    images: list[dict],
    known_images: dict[str, str] | None,
) -> typing.Callable[[re.Match], str]:
    """Build a regex replacement callback shared by both extract functions."""

    def _replace(match: re.Match) -> str:
        prefix = match.group(1)
        content_type = match.group(2)
        b64_data = match.group(3)
        suffix = match.group(4)

        try:
            content = base64.b64decode(b64_data)
        # pylint: disable=broad-exception-caught
        except Exception:
            logger.warning("Failed to decode base64 image, leaving as-is")
            return match.group(0)

        cid = _resolve_image(content, content_type, images, known_images)
        return f"{prefix}cid:{cid}{suffix}"

    return _replace


def extract_base64_images_from_text(
    text: str,
    known_images: dict[str, str] | None = None,
) -> tuple[str, list[dict]]:
    """Extract base64 images from plain text and replace them with CID references.

    Handles both markdown image syntax `![...](data:image/...;base64,...)`
    and any residual HTML `<img src="data:image/...;base64,...">` tags.

    Args:
        text: The plain text string potentially containing base64 images.
        known_images: Optional dict mapping SHA-256 hex digests to CIDs.
            When provided, duplicate images are de-duplicated across calls
            by reusing the same CID.

    Returns:
        A tuple of (modified_text, images) where *images* is a list of dicts
        with keys `cid`, `content` (bytes), `content_type`, `name`,
        and `size`.
    """
    images: list[dict] = []
    replace = _make_replacer(images, known_images)

    text = _MD_BASE64_IMG_RE.sub(replace, text)
    text = _BASE64_IMG_RE.sub(replace, text)

    return text, images


def extract_base64_images_from_html(
    html: str,
    known_images: dict[str, str] | None = None,
) -> tuple[str, list[dict]]:
    """Extract base64-encoded images from HTML and replace them with CID references.

    For each `<img src="data:image/...;base64,...">` found in *html*, a unique
    CID is generated, the `src` attribute is replaced with `cid:<cid>`, and
    the decoded binary content is collected.

    Args:
        html: The HTML string potentially containing base64 images.
        known_images: Optional dict mapping SHA-256 hex digests to CIDs.
            When provided, duplicate images are de-duplicated across calls
            by reusing the same CID.

    Returns:
        A tuple of (modified_html, images) where *images* is a list of dicts
        with keys `cid`, `content` (bytes), `content_type`, and `name` and `size`.
    """
    images: list[dict] = []
    modified_html = _BASE64_IMG_RE.sub(_make_replacer(images, known_images), html)
    return modified_html, images
