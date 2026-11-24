"""Tests for app password scopes checked by the bridge at login."""

import imaplib
import smtplib

import pytest

from .conftest import IMAP_HOST, IMAP_PORT, SENDER_SCOPES, SMTP_HOST, SMTP_PORT

READER_SCOPES = ["threads:read", "messages:read", "messages:read.body"]
SENDER_ONLY_SCOPES = ["messages:send"]


def test_imap_login_needs_read_scope(imap_server, make_scoped_channel):
    channel = make_scoped_channel(SENDER_ONLY_SCOPES)
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    with pytest.raises(imaplib.IMAP4.error):
        client.login(channel["mailbox_email"], channel["password"])


def test_imap_readonly_without_write_scope(imap_server, make_scoped_channel):
    channel = make_scoped_channel(READER_SCOPES)
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    client.login(channel["mailbox_email"], channel["password"])
    # The server answers SELECT with [READ-ONLY]; imaplib refuses a writable select.
    with pytest.raises(imaplib.IMAP4.readonly):
        client.select("INBOX")
    client.shutdown()


def test_imap_writable_with_write_scope(imap_server, make_scoped_channel, mock_api):
    channel = make_scoped_channel(SENDER_SCOPES)
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    client.login(channel["mailbox_email"], channel["password"])
    client.select("INBOX")
    status, _ = client.store("1", "+FLAGS", "(\\Seen)")
    assert status == "OK"
    client.logout()


def test_smtp_login_needs_send_scope(smtp_server, make_scoped_channel):
    channel = make_scoped_channel(READER_SCOPES)
    client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    client.ehlo()
    with pytest.raises(smtplib.SMTPAuthenticationError):
        client.login(channel["mailbox_email"], channel["password"])


def test_smtp_login_sender_only(smtp_server, make_scoped_channel):
    channel = make_scoped_channel(SENDER_ONLY_SCOPES)
    client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    client.ehlo()
    client.login(channel["mailbox_email"], channel["password"])
    client.quit()
