"""Tests for IMAP authentication using channel app-specific passwords."""

import imaplib

import pytest

from .conftest import IMAP_HOST, IMAP_PORT


def test_login_success(imap_connection, test_channel):
    """Test successful IMAP LOGIN with valid channel credentials."""
    status, data = imap_connection.login(test_channel["mailbox_email"], test_channel["password"])
    assert status == "OK"


def test_login_wrong_password(imap_server, test_channel):
    """Test IMAP LOGIN fails with wrong password."""
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    with pytest.raises(imaplib.IMAP4.error):
        client.login(test_channel["mailbox_email"], "wrong-password")
    try:
        client.logout()
    except Exception:
        pass


def test_login_nonexistent_mailbox(imap_server):
    """Test IMAP LOGIN fails with nonexistent email address."""
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    with pytest.raises(imaplib.IMAP4.error):
        client.login("nobody@nonexistent.example", "some-password")
    try:
        client.logout()
    except Exception:
        pass


def test_login_empty_password(imap_server, test_channel):
    """Test IMAP LOGIN fails with empty password."""
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    with pytest.raises(imaplib.IMAP4.error):
        client.login(test_channel["mailbox_email"], "")
    try:
        client.logout()
    except Exception:
        pass


def test_capability_before_login(imap_connection):
    """Test CAPABILITY command before authentication."""
    status, caps = imap_connection.capability()
    assert status == "OK"
    # Should include IMAP4rev1
    cap_str = b" ".join(caps[0].split() if caps[0] else []).upper()
    assert b"IMAP4REV1" in cap_str


def test_logout(imap_client):
    """Test LOGOUT command."""
    status, data = imap_client.logout()
    assert status == "BYE"


def test_noop_after_login(imap_client):
    """Test NOOP command after authentication."""
    status, data = imap_client.noop()
    assert status == "OK"
