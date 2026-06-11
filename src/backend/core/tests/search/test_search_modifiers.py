"""Unit tests for the Gmail-style search modifiers parser."""

from unittest import mock

import pytest

from core.services.search.search import search_threads


@pytest.fixture(name="mock_es_client")
def fixture_mock_es_client():
    """Mock the OpenSearch client."""
    with mock.patch(
        "core.services.search.search.get_opensearch_client"
    ) as mock_get_opensearch_client:
        mock_es = mock.MagicMock()
        # Setup standard mock returns
        mock_es.indices.exists.return_value = False
        mock_es.indices.create.return_value = {"acknowledged": True}
        mock_es.indices.delete.return_value = {"acknowledged": True}

        # Setup search mock
        mock_es.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}

        mock_get_opensearch_client.return_value = mock_es
        yield mock_es


def test_search_threads_with_from_modifier(mock_es_client):
    """Test searching threads with 'from:' modifier."""
    # Call the function with the actual query
    search_threads("from:john@example.com some text", mailbox_ids=[1])

    # Verify the search method was called
    assert mock_es_client.search.called

    # Get the parameters that were passed to ES client search
    call_args = mock_es_client.search.call_args[1]

    # Verify the query includes the sender filter
    assert "query" in call_args["body"]
    assert "bool" in call_args["body"]["query"]
    assert "filter" in call_args["body"]["query"]["bool"]

    sender_query_found = False
    for filter_item in call_args["body"]["query"]["bool"]["filter"]:
        if "term" in filter_item and "sender_email" in filter_item["term"]:
            sender_query_found = True
            assert filter_item["term"]["sender_email"] == "john@example.com"
            break

    if not sender_query_found:
        for item in call_args["body"]["query"]["bool"]["should"]:
            if "wildcard" in item and "sender_email" in item["wildcard"]:
                sender_query_found = True
                break

    assert sender_query_found, "Sender query was not found in the OpenSearch query"


def test_search_threads_with_multiple_modifiers(mock_es_client):
    """Test searching threads with multiple modifiers."""
    # Call the function with the actual query containing multiple modifiers
    search_threads(
        "from:john@example.com to:sarah@example.com subject:Meeting is:starred is:unread some text",
        mailbox_ids=[1],
    )

    # Verify the search method was called
    assert mock_es_client.search.called

    # Get the parameters that were passed to ES client search
    call_args = mock_es_client.search.call_args[1]

    # Verify the query includes all expected filters
    assert "query" in call_args["body"]
    assert "bool" in call_args["body"]["query"]

    # Check for sender filter
    sender_query_found = False
    for filter_item in call_args["body"]["query"]["bool"]["filter"]:
        if "term" in filter_item and "sender_email" in filter_item["term"]:
            sender_query_found = True
            assert filter_item["term"]["sender_email"] == "john@example.com"
            break

    assert sender_query_found, "Sender query was not found in the OpenSearch query"

    # Check for to filter
    to_query_found = 0
    for filter_item in call_args["body"]["query"]["bool"]["should"]:
        if "term" in filter_item:
            if "to_email" in filter_item["term"]:
                to_query_found += 1
                assert filter_item["term"]["to_email"] == "sarah@example.com"
            elif "cc_email" in filter_item["term"]:
                to_query_found += 1
                assert filter_item["term"]["cc_email"] == "sarah@example.com"
            elif "bcc_email" in filter_item["term"]:
                to_query_found += 1
                assert filter_item["term"]["bcc_email"] == "sarah@example.com"
        if to_query_found == 3:
            break

    assert to_query_found == 3, (
        "Not all expected queries were found in the OpenSearch query (3 expected - to, cc, bcc)"
    )

    # Check for subject filter
    subject_query_found = False
    for filter_item in call_args["body"]["query"]["bool"]["must"]:
        if "match_phrase" in filter_item and "subject" in filter_item["match_phrase"]:
            subject_query_found = True
            assert filter_item["match_phrase"]["subject"] == "Meeting"
            break
    assert subject_query_found, "Subject query was not found in the OpenSearch query"

    # Check for starred filter (now uses has_parent + starred_mailboxes)
    starred_filter_found = False
    for filter_item in call_args["body"]["query"]["bool"]["filter"]:
        if "has_parent" in filter_item:
            hp = filter_item["has_parent"]
            if hp.get("parent_type") == "thread" and "terms" in hp.get("query", {}):
                if "starred_mailboxes" in hp["query"]["terms"]:
                    starred_filter_found = True

    assert starred_filter_found, "Starred filter was not found in the OpenSearch query"

    # Check for has_parent unread filter
    unread_filter_found = False
    for filter_item in call_args["body"]["query"]["bool"]["filter"]:
        if "has_parent" in filter_item:
            hp = filter_item["has_parent"]
            if hp.get("parent_type") == "thread" and "terms" in hp.get("query", {}):
                if "unread_mailboxes" in hp["query"]["terms"]:
                    unread_filter_found = True

    assert unread_filter_found, (
        "has_parent unread filter was not found in the OpenSearch query"
    )


def test_search_threads_is_read_filter(mock_es_client):
    """Test that is:read generates a has_parent must_not filter."""
    search_threads("is:read", mailbox_ids=["mbx-1"])

    call_args = mock_es_client.search.call_args[1]
    has_parent_found = False
    for filter_item in call_args["body"]["query"]["bool"]["filter"]:
        if "has_parent" in filter_item:
            hp = filter_item["has_parent"]
            query = hp.get("query", {})
            if "bool" in query and "must_not" in query["bool"]:
                must_not = query["bool"]["must_not"]
                if "terms" in must_not and "unread_mailboxes" in must_not["terms"]:
                    has_parent_found = True
                    assert must_not["terms"]["unread_mailboxes"] == ["mbx-1"]

    assert has_parent_found, "has_parent must_not filter for is:read was not found"


def test_search_threads_is_unread_filter(mock_es_client):
    """Test that is:unread generates a has_parent terms filter."""
    search_threads("is:unread", mailbox_ids=["mbx-1"])

    call_args = mock_es_client.search.call_args[1]
    has_parent_found = False
    for filter_item in call_args["body"]["query"]["bool"]["filter"]:
        if "has_parent" in filter_item:
            hp = filter_item["has_parent"]
            query = hp.get("query", {})
            if "terms" in query and "unread_mailboxes" in query["terms"]:
                has_parent_found = True
                assert query["terms"]["unread_mailboxes"] == ["mbx-1"]

    assert has_parent_found, "has_parent terms filter for is:unread was not found"


def test_search_threads_filters_is_starred_true(mock_es_client):
    """Test that filters={'is_starred': True} uses has_parent on starred_mailboxes."""
    search_threads("some text", mailbox_ids=["mbx-1"], filters={"is_starred": True})

    call_args = mock_es_client.search.call_args[1]
    filters = call_args["body"]["query"]["bool"]["filter"]

    # Should use has_parent with starred_mailboxes
    has_parent_found = False
    for filter_item in filters:
        if "has_parent" in filter_item:
            hp = filter_item["has_parent"]
            query = hp.get("query", {})
            if "terms" in query and "starred_mailboxes" in query["terms"]:
                has_parent_found = True
                assert query["terms"]["starred_mailboxes"] == ["mbx-1"]

    assert has_parent_found, "has_parent starred_mailboxes filter was not found"

    # Should NOT emit a legacy {"term": {"is_starred": ...}} filter
    for filter_item in filters:
        if "term" in filter_item:
            assert "is_starred" not in filter_item["term"], (
                "Legacy is_starred term filter should not be emitted"
            )


def test_search_threads_filters_is_starred_false(mock_es_client):
    """Test that filters={'is_starred': False} uses has_parent must_not on starred_mailboxes."""
    search_threads("some text", mailbox_ids=["mbx-1"], filters={"is_starred": False})

    call_args = mock_es_client.search.call_args[1]
    filters = call_args["body"]["query"]["bool"]["filter"]

    has_parent_found = False
    for filter_item in filters:
        if "has_parent" in filter_item:
            hp = filter_item["has_parent"]
            query = hp.get("query", {})
            if "bool" in query and "must_not" in query["bool"]:
                must_not = query["bool"]["must_not"]
                if "terms" in must_not and "starred_mailboxes" in must_not["terms"]:
                    has_parent_found = True
                    assert must_not["terms"]["starred_mailboxes"] == ["mbx-1"]

    assert has_parent_found, (
        "has_parent must_not starred_mailboxes filter was not found"
    )


def test_search_threads_filters_is_unread_true(mock_es_client):
    """Test that filters={'is_unread': True} uses has_parent on unread_mailboxes."""
    search_threads("some text", mailbox_ids=["mbx-1"], filters={"is_unread": True})

    call_args = mock_es_client.search.call_args[1]
    filters = call_args["body"]["query"]["bool"]["filter"]

    has_parent_found = False
    for filter_item in filters:
        if "has_parent" in filter_item:
            hp = filter_item["has_parent"]
            query = hp.get("query", {})
            if "terms" in query and "unread_mailboxes" in query["terms"]:
                has_parent_found = True
                assert query["terms"]["unread_mailboxes"] == ["mbx-1"]

    assert has_parent_found, "has_parent unread_mailboxes filter was not found"

    for filter_item in filters:
        if "term" in filter_item:
            assert "is_unread" not in filter_item["term"], (
                "Legacy is_unread term filter should not be emitted"
            )


def test_search_threads_filters_is_unread_false(mock_es_client):
    """Test that filters={'is_unread': False} uses has_parent must_not on unread_mailboxes."""
    search_threads("some text", mailbox_ids=["mbx-1"], filters={"is_unread": False})

    call_args = mock_es_client.search.call_args[1]
    filters = call_args["body"]["query"]["bool"]["filter"]

    has_parent_found = False
    for filter_item in filters:
        if "has_parent" in filter_item:
            hp = filter_item["has_parent"]
            query = hp.get("query", {})
            if "bool" in query and "must_not" in query["bool"]:
                must_not = query["bool"]["must_not"]
                if "terms" in must_not and "unread_mailboxes" in must_not["terms"]:
                    has_parent_found = True
                    assert must_not["terms"]["unread_mailboxes"] == ["mbx-1"]

    assert has_parent_found, "has_parent must_not unread_mailboxes filter was not found"


def test_search_threads_filters_other_fields_still_use_term(mock_es_client):
    """Test that non-mailbox-scoped filters still use the generic term filter."""
    search_threads(
        "some text",
        mailbox_ids=["mbx-1"],
        filters={"is_starred": True, "is_sender": True},
    )

    call_args = mock_es_client.search.call_args[1]
    filters = call_args["body"]["query"]["bool"]["filter"]

    sender_term_found = False
    for filter_item in filters:
        if "term" in filter_item and "is_sender" in filter_item["term"]:
            sender_term_found = True
            assert filter_item["term"]["is_sender"] is True

    assert sender_term_found, "is_sender should still use the generic term filter"


def test_search_threads_with_exact_phrase(mock_es_client):
    """Test searching threads with exact phrases."""
    # Call the function with the actual query containing an exact phrase
    search_threads('"exact phrase" some text', mailbox_ids=[1])

    # Verify the search method was called
    assert mock_es_client.search.called

    # Get the parameters that were passed to ES client search
    call_args = mock_es_client.search.call_args[1]

    # Verify the query includes the exact phrase match
    assert "query" in call_args["body"]
    assert "bool" in call_args["body"]["query"]
    assert "must" in call_args["body"]["query"]["bool"]

    exact_phrase_query_found = False
    for query_item in call_args["body"]["query"]["bool"]["must"]:
        if "multi_match" in query_item and "type" in query_item["multi_match"]:
            if (
                query_item["multi_match"]["type"] == "phrase"
                and query_item["multi_match"]["query"] == "exact phrase"
            ):
                exact_phrase_query_found = True
                break

    assert exact_phrase_query_found, (
        "Exact phrase query was not found in the OpenSearch query"
    )


def test_search_threads_with_folder_filter(mock_es_client):
    """Test searching threads with folder filters."""
    # Call the function with the actual query
    search_threads("in:trash some text", mailbox_ids=[1])

    # Verify the search method was called
    assert mock_es_client.search.called

    # Get the parameters that were passed to ES client search
    call_args = mock_es_client.search.call_args[1]

    # Verify the query includes the trash filter
    assert "query" in call_args["body"]
    assert "bool" in call_args["body"]["query"]
    assert "filter" in call_args["body"]["query"]["bool"]

    trash_filter_found = False
    for query in call_args["body"]["query"]["bool"]["filter"]:
        if "term" in query and "is_trashed" in query["term"]:
            trash_filter_found = True
            assert query["term"]["is_trashed"] is True
            break
    assert trash_filter_found, "Trash filter was not found in the OpenSearch query"
