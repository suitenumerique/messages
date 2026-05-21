"""Shared fixtures for importer tests."""

from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _mock_ssrf_dns():
    """Short-circuit SSRF hostname validation for IMAP tests.

    The IMAP import path validates the server hostname via
    ``core.services.ssrf.validate_hostname``. Test fixtures use unresolvable
    hostnames like ``imap.example.com``, so we bypass validation here to let
    tests reach the mocked IMAP code. We patch the symbol imported into
    ``core.services.importer.imap`` rather than ``socket.getaddrinfo``, which
    would also break real DNS lookups (e.g. boto3 reaching the S3 bucket).
    """
    with mock.patch(
        "core.services.importer.imap.validate_hostname",
        return_value=["93.184.216.34"],
    ):
        yield
