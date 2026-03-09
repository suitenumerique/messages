"""Tests for IMAP message operations using Python's imaplib."""

import email


def test_fetch_message_count(imap_client, test_channel):
    """Test that INBOX has the expected number of messages."""
    status, data = imap_client.select("INBOX")
    assert status == "OK"
    count = int(data[0])
    assert count == test_channel["inbox_message_count"]


def test_fetch_all_messages(imap_client, test_channel):
    """Test FETCH all messages in INBOX."""
    imap_client.select("INBOX")
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"

    message_nums = data[0].split()
    assert len(message_nums) == test_channel["inbox_message_count"]


def test_fetch_message_envelope(imap_client):
    """Test FETCH message envelope (headers)."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(ENVELOPE)")
    assert status == "OK"
    assert data[0] is not None


def test_fetch_message_body(imap_client):
    """Test FETCH full message body."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(BODY[])")
    assert status == "OK"

    # Parse the raw email
    raw_email = data[0][1]
    msg = email.message_from_bytes(raw_email)
    assert msg["Subject"] == "Welcome to Messages"
    assert "sender" in msg["From"] or "admin" in msg["From"]


def test_fetch_message_headers_only(imap_client):
    """Test FETCH message headers only."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(BODY[HEADER])")
    assert status == "OK"
    raw = data[0][1]
    assert b"Subject:" in raw


def test_fetch_specific_headers(imap_client):
    """Test FETCH specific headers (Subject, From, To)."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(BODY[HEADER.FIELDS (SUBJECT FROM TO)])")
    assert status == "OK"
    raw = data[0][1]
    assert b"Subject:" in raw or b"From:" in raw


def test_fetch_message_text(imap_client):
    """Test FETCH message text body."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(BODY[TEXT])")
    assert status == "OK"
    assert len(data[0][1]) > 0


def test_fetch_flags(imap_client):
    """Test FETCH flags reflects API read state (webmail → IMAP).

    Message 1 has is_unread=True in the API → should NOT have \\Seen.
    Message 2 has is_unread=False in the API → should have \\Seen.
    """
    imap_client.select("INBOX")

    # Message 1: is_unread=True → no \Seen flag
    status, data = imap_client.fetch("1", "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Seen" not in flags_str, "Unread message should not have \\Seen"

    # Message 2: is_unread=False → \Seen flag present
    status, data = imap_client.fetch("2", "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Seen" in flags_str, "Read message should have \\Seen"


def test_fetch_uid(imap_client):
    """Test UID FETCH command."""
    imap_client.select("INBOX")
    # First get UIDs
    status, data = imap_client.uid("SEARCH", None, "ALL")
    assert status == "OK"
    uids = data[0].split()
    assert len(uids) > 0

    # Fetch by UID
    uid = uids[0]
    status, data = imap_client.uid("FETCH", uid, "(BODY[])")
    assert status == "OK"


def test_fetch_rfc822(imap_client):
    """Test FETCH RFC822 (full message)."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(RFC822)")
    assert status == "OK"
    raw = data[0][1]
    msg = email.message_from_bytes(raw)
    assert msg["Subject"] is not None


def test_fetch_rfc822_size(imap_client):
    """Test FETCH RFC822.SIZE."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(RFC822.SIZE)")
    assert status == "OK"
    assert data[0] is not None


def test_fetch_bodystructure(imap_client):
    """Test FETCH BODYSTRUCTURE."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(BODYSTRUCTURE)")
    assert status == "OK"
    assert data[0] is not None


def test_fetch_internaldate(imap_client):
    """Test FETCH INTERNALDATE."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1", "(INTERNALDATE)")
    assert status == "OK"
    assert data[0] is not None


def test_search_all(imap_client, test_channel):
    """Test SEARCH ALL messages."""
    imap_client.select("INBOX")
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    nums = data[0].split()
    assert len(nums) == test_channel["inbox_message_count"]


def test_search_unseen(imap_client):
    """Test SEARCH UNSEEN messages."""
    imap_client.select("INBOX")
    status, data = imap_client.search(None, "UNSEEN")
    assert status == "OK"
    # At least one unseen message (the first one)
    nums = data[0].split()
    assert len(nums) >= 1


def test_search_seen(imap_client):
    """Test SEARCH SEEN messages."""
    imap_client.select("INBOX")
    status, data = imap_client.search(None, "SEEN")
    assert status == "OK"
    # The second message is read
    nums = data[0].split()
    assert len(nums) >= 1


def test_search_flagged(imap_client):
    """Test SEARCH FLAGGED (starred) messages."""
    imap_client.select("INBOX")
    status, data = imap_client.search(None, "FLAGGED")
    assert status == "OK"
    nums = data[0].split()
    assert len(nums) >= 1


def test_uid_search(imap_client):
    """Test UID SEARCH command."""
    imap_client.select("INBOX")
    status, data = imap_client.uid("SEARCH", None, "ALL")
    assert status == "OK"
    uids = data[0].split()
    assert len(uids) > 0
    # UIDs should be numeric
    for uid in uids:
        assert uid.isdigit()


def test_sent_folder_messages(imap_client, test_channel):
    """Test messages in the Sent folder."""
    status, data = imap_client.select("Sent")
    assert status == "OK"
    count = int(data[0])
    assert count == test_channel["sent_message_count"]


def test_trash_folder_messages(imap_client, test_channel):
    """Test messages in the Trash folder."""
    status, data = imap_client.select("Trash")
    assert status == "OK"
    count = int(data[0])
    assert count == test_channel["trash_message_count"]


def test_fetch_multiple_messages(imap_client):
    """Test FETCH multiple messages at once."""
    imap_client.select("INBOX")
    status, data = imap_client.fetch("1:*", "(FLAGS ENVELOPE)")
    assert status == "OK"
    # Should have data for each message
    assert len([d for d in data if d and d != b")"]) >= 1


def test_close_mailbox(imap_client):
    """Test CLOSE command after selecting a mailbox."""
    imap_client.select("INBOX")
    status, data = imap_client.close()
    assert status == "OK"


def test_noop_in_selected_state(imap_client):
    """Test NOOP while a mailbox is selected."""
    imap_client.select("INBOX")
    status, data = imap_client.noop()
    assert status == "OK"


def test_check_command(imap_client):
    """Test CHECK command."""
    imap_client.select("INBOX")
    status, data = imap_client.check()
    assert status == "OK"
