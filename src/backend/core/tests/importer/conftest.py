"""Shared fixtures for importer tests."""

import socket
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _mock_ssrf_dns():
    """Short-circuit SSRF DNS validation for IMAP tests.

    The IMAP import path validates the server hostname via
    ``core.services.ssrf.validate_hostname`` which calls ``socket.getaddrinfo``.
    Test fixtures use unresolvable hostnames like ``imap.example.com``, so we
    return a valid public IP here to let tests reach the mocked IMAP code.
    """
    with mock.patch(
        "core.services.ssrf.socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    ):
        yield
