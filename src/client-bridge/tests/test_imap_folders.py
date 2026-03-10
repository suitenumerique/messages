"""Tests for IMAP folder operations."""

import re


def test_list_all_folders(imap_client):
    """Test LIST command returns all virtual folders."""
    status, data = imap_client.list()
    assert status == "OK"

    folder_names = []
    for item in data:
        if item:
            decoded = item.decode() if isinstance(item, bytes) else str(item)
            # Parse LIST response: (\\flags) "/" "FolderName" or (\\flags) "/" FolderName
            # Extract the last quoted string or last word
            match = re.search(r'"([^"]+)"$', decoded)
            if match:
                folder_names.append(match.group(1))
            else:
                folder_names.append(decoded.split()[-1])

    # Check that our virtual folders are present
    assert any("INBOX" in f for f in folder_names), f"INBOX not found in {folder_names}"


def test_list_with_wildcard(imap_client):
    """Test LIST with wildcard pattern."""
    status, data = imap_client.list('""', "*")
    assert status == "OK"
    assert len(data) > 0


def test_list_inbox_only(imap_client):
    """Test LIST with specific pattern for INBOX."""
    status, data = imap_client.list('""', "INBOX")
    assert status == "OK"
    assert len(data) >= 1


def test_select_inbox(imap_client):
    """Test SELECT INBOX."""
    status, data = imap_client.select("INBOX")
    assert status == "OK"
    # data[0] should be the message count
    assert int(data[0]) >= 0


def test_select_sent(imap_client):
    """Test SELECT Sent folder."""
    status, data = imap_client.select("Sent")
    assert status == "OK"
    assert int(data[0]) >= 0


def test_select_trash(imap_client):
    """Test SELECT Trash folder."""
    status, data = imap_client.select("Trash")
    assert status == "OK"


def test_select_nonexistent_folder(imap_client):
    """Test SELECT for a folder that doesn't exist."""
    status, data = imap_client.select("NonExistentFolder")
    assert status == "NO"


def test_examine_inbox(imap_client):
    """Test EXAMINE (read-only SELECT) on INBOX."""
    status, data = imap_client.select("INBOX", readonly=True)
    assert status == "OK"
    assert int(data[0]) >= 0


def test_status_inbox(imap_client):
    """Test STATUS command on INBOX."""
    status, data = imap_client.status("INBOX", "(MESSAGES UNSEEN UIDNEXT UIDVALIDITY)")
    assert status == "OK"
    assert data[0] is not None


def test_lsub_all(imap_client):
    """Test LSUB (list subscribed folders)."""
    status, data = imap_client.lsub()
    assert status == "OK"


def test_create_folder_rejected(imap_client):
    """Test CREATE is rejected for API-backed mailboxes."""
    status, data = imap_client.create("NewFolder")
    assert status == "NO"


def test_delete_folder_rejected(imap_client):
    """Test DELETE is rejected for API-backed mailboxes."""
    status, data = imap_client.delete("Sent")
    assert status == "NO"


def test_rename_folder_rejected(imap_client):
    """Test RENAME is rejected for API-backed mailboxes."""
    status, data = imap_client.rename("Sent", "SentMail")
    assert status == "NO"
