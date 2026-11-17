"""A PEM file holding several private keys, each with its certificate chain.

``ssl.load_cert_chain`` reads the first key of a file and the first
certificate, and takes every later certificate for chain, so a second key and
certificate in the same file are never served. This module cuts such a file
into one group per key, which the caller loads one by one.

The file is the operator's, not a peer's. Nothing here parses ASN.1: a block
is recognised by its ``BEGIN``/``END`` lines and handed to OpenSSL as it is.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Iterator

_BLOCK = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----.*?-----END \1-----", re.DOTALL)
_CERTIFICATE_LABELS = ("CERTIFICATE", "TRUSTED CERTIFICATE")


def split(text: str, name: str = "PEM") -> list[str] | None:
    """The key + chain groups of ``text`` when it holds several keys, else None.

    A group starts at a private key and runs to the next one: the key first,
    then its certificate and chain, as in a Postfix chain file. None for text
    with at most one key, which OpenSSL reads as it is. Blocks that are neither
    keys nor certificates (DH parameters, for one) are dropped.

    Raises ``ValueError``, naming ``name``, for a multi-key text that cannot be
    cut: a certificate before the first key, or a key without a certificate.
    """
    groups: list[list[str]] = []
    leading_certificate = False
    for match in _BLOCK.finditer(text):
        label = match.group(1)
        if label.endswith("PRIVATE KEY"):
            groups.append([match.group(0)])
        elif label in _CERTIFICATE_LABELS:
            if groups:
                groups[-1].append(match.group(0))
            else:
                leading_certificate = True
    if len(groups) < 2:
        return None
    if leading_certificate:
        raise ValueError(f"{name}: a certificate comes before the first private key")
    if any(len(group) < 2 for group in groups):
        raise ValueError(f"{name}: a private key is not followed by its certificate")
    return ["\n".join(group) + "\n" for group in groups]


def split_file(path: str) -> list[str] | None:
    """:func:`split` for the file at ``path``. Raises ``OSError`` if unreadable."""
    with open(path, encoding="ascii", errors="replace") as handle:
        return split(handle.read(), path)


@contextlib.contextmanager
def readable_path(text: str) -> Iterator[str]:
    """A path that reads as ``text``, for the calls that only take paths.

    An anonymous in-memory file where Linux has one and ``/proc`` is mounted
    to name it, so a private key is not written to disk; a temporary file
    elsewhere.
    """
    # A non-ASCII byte inside a block is corruption; OpenSSL names the file.
    data = text.encode("ascii", errors="replace")
    if hasattr(os, "memfd_create") and os.path.isdir("/proc/self/fd"):
        # Close-on-exec by default, so a child process does not inherit it.
        fd = os.memfd_create("pymta-tls")
        try:
            with open(fd, "wb", closefd=False) as handle:
                handle.write(data)
            yield f"/proc/self/fd/{fd}"
        finally:
            os.close(fd)
    else:
        with tempfile.NamedTemporaryFile("wb", suffix=".pem") as handle:
            handle.write(data)
            handle.flush()
            yield handle.name
