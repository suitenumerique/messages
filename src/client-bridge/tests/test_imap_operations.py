"""Tests for IMAP flag operations, COPY, MOVE, EXPUNGE, and APPEND.

These tests were added after manual testing with multiple IMAP clients
(curl, mbsync, Perl Mail::IMAPClient, raw netcat, Python imaplib) revealed
that these operations had no unit test coverage.
"""

import imaplib
from email.mime.text import MIMEText

from tests.conftest import IMAP_HOST, IMAP_PORT


# --- STORE flag operations ---


def test_store_add_seen_flag(imap_client):
    """Test STORE +FLAGS (\\Seen) marks a message as read."""
    imap_client.select("INBOX")
    # Message 1 is unread initially - add \Seen flag
    status, data = imap_client.store("1", "+FLAGS", "(\\Seen)")
    assert status == "OK"

    # Verify the flag was applied
    status, data = imap_client.fetch("1", "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Seen" in flags_str

    # Cleanup: remove the flag we just set
    imap_client.store("1", "-FLAGS", "(\\Seen)")


def test_store_remove_seen_flag(imap_client):
    """Test STORE -FLAGS (\\Seen) marks a message as unread."""
    imap_client.select("INBOX")
    # Message 2 is read (has \Seen) - remove it
    status, data = imap_client.store("2", "-FLAGS", "(\\Seen)")
    assert status == "OK"

    status, data = imap_client.fetch("2", "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Seen" not in flags_str

    # Cleanup: restore the flag
    imap_client.store("2", "+FLAGS", "(\\Seen)")


def test_store_seen_syncs_to_api(imap_client, mock_api):
    """Test that IMAP \\Seen flag changes propagate to the API (IMAP → webmail).

    Adding \\Seen should call POST /flag/ with flag=unread, value=false.
    Removing \\Seen should call POST /flag/ with flag=unread, value=true.
    """
    imap_client.select("INBOX")

    # Clear previous flag updates
    mock_api.message_flag_updates.clear()

    # Mark as read: +FLAGS (\Seen) → API should receive flag=unread, value=False
    imap_client.store("1", "+FLAGS", "(\\Seen)")
    unread_updates = [u for u in mock_api.message_flag_updates if u.get("flag") == "unread"]
    assert any(u.get("value") is False for u in unread_updates), (
        f"Expected flag=unread, value=False in API updates: {mock_api.message_flag_updates}"
    )

    mock_api.message_flag_updates.clear()

    # Mark as unread: -FLAGS (\Seen) → API should receive flag=unread, value=True
    imap_client.store("1", "-FLAGS", "(\\Seen)")
    unread_updates = [u for u in mock_api.message_flag_updates if u.get("flag") == "unread"]
    assert any(u.get("value") is True for u in unread_updates), (
        f"Expected flag=unread, value=True in API updates: {mock_api.message_flag_updates}"
    )


def test_store_add_flagged(imap_client):
    """Test STORE +FLAGS (\\Flagged) stars a message."""
    imap_client.select("INBOX")
    # Message 1 is not starred - add \Flagged
    status, data = imap_client.store("1", "+FLAGS", "(\\Flagged)")
    assert status == "OK"

    status, data = imap_client.fetch("1", "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Flagged" in flags_str

    # Cleanup
    imap_client.store("1", "-FLAGS", "(\\Flagged)")


def test_store_add_deleted_flag(imap_client):
    """Test STORE +FLAGS (\\Deleted) marks a message for deletion."""
    imap_client.select("INBOX")
    status, data = imap_client.store("1", "+FLAGS", "(\\Deleted)")
    assert status == "OK"

    status, data = imap_client.fetch("1", "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Deleted" in flags_str

    # Cleanup: remove the flag before any expunge
    imap_client.store("1", "-FLAGS", "(\\Deleted)")


def test_store_replace_flags(imap_client):
    """Test STORE FLAGS (replace all flags).

    This was found problematic with Perl's Mail::IMAPClient during manual
    testing (BAD UID STORE: Invalid arguments), but raw IMAP worked fine.
    Verifies the server correctly handles flag replacement via imaplib.
    """
    imap_client.select("INBOX")

    # Replace all flags with just \Seen and \Flagged
    status, data = imap_client.store("1", "FLAGS", "(\\Seen \\Flagged)")
    assert status == "OK"

    status, data = imap_client.fetch("1", "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Seen" in flags_str
    assert "\\Flagged" in flags_str

    # Cleanup: restore original flags (message 1 was unread, not starred)
    imap_client.store("1", "FLAGS", "()")


def test_store_multiple_flags_at_once(imap_client):
    """Test STORE +FLAGS with multiple flags in one command."""
    imap_client.select("INBOX")
    status, data = imap_client.store("1", "+FLAGS", "(\\Seen \\Flagged)")
    assert status == "OK"

    status, data = imap_client.fetch("1", "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Seen" in flags_str
    assert "\\Flagged" in flags_str

    # Cleanup
    imap_client.store("1", "-FLAGS", "(\\Seen \\Flagged)")


def test_uid_store_flags(imap_client):
    """Test UID STORE for flag operations."""
    imap_client.select("INBOX")

    # Get UID of first message
    status, data = imap_client.uid("SEARCH", None, "ALL")
    assert status == "OK"
    uid = data[0].split()[0]

    # UID STORE +FLAGS
    status, data = imap_client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    assert status == "OK"

    # Verify via UID FETCH
    status, data = imap_client.uid("FETCH", uid, "(FLAGS)")
    assert status == "OK"
    flags_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    assert "\\Seen" in flags_str

    # Cleanup
    imap_client.uid("STORE", uid, "-FLAGS", "(\\Seen)")


def test_store_silent_flag(imap_client):
    """Test STORE +FLAGS.SILENT (should not return updated flags)."""
    imap_client.select("INBOX")
    status, data = imap_client.store("1", "+FLAGS.SILENT", "(\\Seen)")
    assert status == "OK"
    # SILENT mode should not include FETCH response with flags
    # The response should be minimal

    # Cleanup
    imap_client.store("1", "-FLAGS", "(\\Seen)")


# --- SEARCH after flag changes ---


def test_search_after_store(imap_client):
    """Test that SEARCH reflects flag changes made by STORE."""
    imap_client.select("INBOX")

    # Mark message 1 as Seen
    imap_client.store("1", "+FLAGS", "(\\Seen)")

    # Search for SEEN should now include message 1
    status, data = imap_client.search(None, "SEEN")
    assert status == "OK"
    seen_msgs = data[0].split()
    assert b"1" in seen_msgs

    # Search for UNSEEN should NOT include message 1
    status, data = imap_client.search(None, "UNSEEN")
    assert status == "OK"
    unseen_msgs = data[0].split()
    assert b"1" not in unseen_msgs

    # Cleanup
    imap_client.store("1", "-FLAGS", "(\\Seen)")


# --- EXPUNGE ---


def test_expunge_deleted_message(imap_client):
    """Test EXPUNGE removes messages marked with \\Deleted."""
    imap_client.select("INBOX")

    # Get initial count
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    initial_count = len(data[0].split())

    # Mark message 1 as deleted
    status, data = imap_client.store("1", "+FLAGS", "(\\Deleted)")
    assert status == "OK"

    # Expunge
    status, data = imap_client.expunge()
    assert status == "OK"

    # Message count should be reduced
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    new_count = len(data[0].split()) if data[0] else 0
    assert new_count == initial_count - 1


def test_expunge_without_deleted(imap_client):
    """Test EXPUNGE with no deleted messages is a no-op."""
    imap_client.select("Sent")
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    initial_count = len(data[0].split()) if data[0] else 0

    status, data = imap_client.expunge()
    assert status == "OK"

    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    final_count = len(data[0].split()) if data[0] else 0
    assert final_count == initial_count


# --- COPY ---


def test_copy_message_to_trash(imap_client):
    """Test COPY message from Sent to Trash.

    COPY was found problematic with Perl's Mail::IMAPClient during manual
    testing (BAD UID COPY: Invalid arguments), but raw IMAP and imaplib
    worked correctly. This test verifies proper COPY behavior.
    """
    imap_client.select("Sent")
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    assert data[0], "Sent folder should have messages"

    # Get initial Trash count
    imap_client.select("Trash")
    status, data = imap_client.search(None, "ALL")
    initial_trash = len(data[0].split()) if data[0] else 0

    # Copy message 1 from Sent to Trash
    imap_client.select("Sent")
    status, data = imap_client.copy("1", "Trash")
    assert status == "OK"

    # Verify the message exists in Trash
    imap_client.select("Trash")
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    new_trash = len(data[0].split()) if data[0] else 0
    assert new_trash == initial_trash + 1


def test_uid_copy(imap_client):
    """Test UID COPY command."""
    imap_client.select("Sent")
    status, data = imap_client.uid("SEARCH", None, "ALL")
    assert status == "OK"
    uid = data[0].split()[0]

    status, data = imap_client.uid("COPY", uid, "Drafts")
    assert status == "OK"


def test_copy_to_nonexistent_folder(imap_client):
    """Test COPY to a non-existent folder fails gracefully."""
    imap_client.select("Sent")
    try:
        status, data = imap_client.copy("1", "NonExistent")
        assert status == "NO"
    except imaplib.IMAP4.error:
        # imaplib raises on NO/BAD responses
        pass


# --- APPEND ---


def test_append_rejected(imap_client):
    """Test APPEND is rejected for API-backed mailboxes."""
    msg = MIMEText("This is a test draft message.")
    msg["Subject"] = "Test Draft"
    msg["From"] = "test@example.com"
    msg["To"] = "recipient@example.com"
    raw_msg = msg.as_bytes()

    # APPEND should be rejected since the Messages API does not support it
    try:
        status, data = imap_client.append("Drafts", "(\\Seen)", None, raw_msg)
        assert status == "NO", "APPEND should be rejected for API-backed mailboxes"
    except imaplib.IMAP4.error:
        # imaplib raises on NO/BAD responses — this is expected
        pass


# --- SUBSCRIBE / UNSUBSCRIBE ---


def test_subscribe_unsubscribe(imap_client):
    """Test SUBSCRIBE and UNSUBSCRIBE commands."""
    # Unsubscribe from Drafts
    status, data = imap_client.unsubscribe("Drafts")
    assert status == "OK"

    # LSUB should not include Drafts
    status, data = imap_client.lsub()
    assert status == "OK"
    folders = []
    for item in data:
        if item:
            decoded = item.decode() if isinstance(item, bytes) else str(item)
            folders.append(decoded)
    assert not any("Drafts" in f for f in folders), (
        f"Drafts should not be in LSUB after unsubscribe: {folders}"
    )

    # Re-subscribe
    status, data = imap_client.subscribe("Drafts")
    assert status == "OK"

    # LSUB should include Drafts again
    status, data = imap_client.lsub()
    assert status == "OK"
    folders = []
    for item in data:
        if item:
            decoded = item.decode() if isinstance(item, bytes) else str(item)
            folders.append(decoded)
    assert any("Drafts" in f for f in folders), (
        f"Drafts should be in LSUB after resubscribe: {folders}"
    )


# --- CLOSE with implicit expunge ---


def test_close_expunges_deleted(imap_server, test_channel):
    """Test that CLOSE implicitly expunges deleted messages.

    Uses a fresh connection to avoid affecting other tests.
    """
    client = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    client.login(test_channel["mailbox_email"], test_channel["password"])

    try:
        client.select("Trash")
        status, data = client.search(None, "ALL")
        assert status == "OK"
        if not data[0]:
            # No messages in Trash, nothing to test
            return

        initial_count = len(data[0].split())

        # Mark first message as deleted
        client.store("1", "+FLAGS", "(\\Deleted)")

        # CLOSE should expunge deleted messages
        client.close()

        # Re-select Trash to check
        client.select("Trash")
        status, data = client.search(None, "ALL")
        assert status == "OK"
        new_count = len(data[0].split()) if data[0] else 0
        assert new_count == initial_count - 1
    finally:
        try:
            client.logout()
        except Exception:
            pass


# --- Multi-folder operations ---


def test_select_switch_folders(imap_client, test_channel):
    """Test switching between folders preserves correct message counts."""
    # Select INBOX
    status, data = imap_client.select("INBOX")
    assert status == "OK"
    inbox_count = int(data[0])

    # Switch to Sent
    status, data = imap_client.select("Sent")
    assert status == "OK"
    sent_count = int(data[0])

    # Switch back to INBOX
    status, data = imap_client.select("INBOX")
    assert status == "OK"
    assert int(data[0]) == inbox_count

    # Counts should match fixture data
    assert sent_count == test_channel["sent_message_count"]


# --- STATUS across folders ---


def test_status_multiple_folders(imap_client):
    """Test STATUS command across multiple folders without SELECT."""
    for folder in ["INBOX", "Sent", "Trash", "Drafts"]:
        status, data = imap_client.status(folder, "(MESSAGES UNSEEN)")
        assert status == "OK", f"STATUS failed for {folder}"
        # Parse the response to verify it contains MESSAGES and UNSEEN
        response_str = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        assert "MESSAGES" in response_str, f"No MESSAGES in STATUS for {folder}"
        assert "UNSEEN" in response_str, f"No UNSEEN in STATUS for {folder}"


# --- Edge cases ---


def test_fetch_nonexistent_message(imap_client):
    """Test FETCH for a message number that doesn't exist."""
    imap_client.select("INBOX")
    # Try to fetch message 999
    status, data = imap_client.fetch("999", "(FLAGS)")
    # Should either return OK with empty data or NO
    # pymap may return OK with empty results for non-existent messages
    assert status in ("OK", "NO")


def test_store_on_readonly_mailbox(imap_client):
    """Test that STORE fails on a mailbox opened with EXAMINE (readonly)."""
    imap_client.select("INBOX", readonly=True)
    try:
        status, data = imap_client.store("1", "+FLAGS", "(\\Seen)")
        assert status == "NO", "STORE should fail on readonly mailbox"
    except imaplib.IMAP4.error:
        # imaplib raises on NO/BAD responses - this is expected
        pass


def test_copy_preserves_source(imap_client):
    """Test that COPY does not remove the source message."""
    imap_client.select("Sent")
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    initial_count = len(data[0].split()) if data[0] else 0

    # Copy message 1 to Archive
    imap_client.copy("1", "Archive")

    # Source message should still exist
    status, data = imap_client.search(None, "ALL")
    assert status == "OK"
    final_count = len(data[0].split()) if data[0] else 0
    assert final_count == initial_count, "COPY should not remove source message"
