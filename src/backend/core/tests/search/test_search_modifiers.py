"""Unit tests for the Gmail-style search modifiers parser."""

from unittest import mock

import pytest

from core.search.search import search_threads


@pytest.fixture
def mock_es_client():
    """Mock the Elasticsearch client."""
    with mock.patch("core.search.search.get_es_client") as mock_get_es_client:
        mock_es = mock.MagicMock()
        # Setup standard mock returns
        mock_es.indices.exists.return_value = False
        mock_es.indices.create.return_value = {"acknowledged": True}
        mock_es.indices.delete.return_value = {"acknowledged": True}

        # Setup search mock
        mock_es.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}

        mock_get_es_client.return_value = mock_es
        yield mock_es


def test_search_threads_with_from_modifier(mock_es_client):
    """Test searching threads with 'from:' modifier."""
    # Call the function with the actual query
    search_threads("from:john@example.com some text", mailbox_id=1)

    # Verify the search method was called
    assert mock_es_client.search.called

    # Get the parameters that were passed to ES client search
    call_args = mock_es_client.search.call_args[1]

    # Verify the query includes the sender filter
    assert "query" in call_args
    assert "bool" in call_args["query"]
    assert "filter" in call_args["query"]["bool"]

    sender_query_found = False
    for filter_item in call_args["query"]["bool"]["filter"]:
        if "term" in filter_item and "sender_email" in filter_item["term"]:
            sender_query_found = True
            assert filter_item["term"]["sender_email"] == "john@example.com"
            break

    if not sender_query_found:
        for item in call_args["query"]["bool"]["should"]:
            if "wildcard" in item and "sender_email" in item["wildcard"]:
                sender_query_found = True
                break

    assert sender_query_found, "Sender query was not found in the Elasticsearch query"


def test_search_threads_with_multiple_modifiers(mock_es_client):
    """Test searching threads with multiple modifiers."""
    # Call the function with the actual query containing multiple modifiers
    search_threads(
        "from:john@example.com to:sarah@example.com subject:Meeting is:starred is:unread some text",
        mailbox_id=1,
    )

    # Verify the search method was called
    assert mock_es_client.search.called

    # Get the parameters that were passed to ES client search
    call_args = mock_es_client.search.call_args[1]

    # Verify the query includes all expected filters
    assert "query" in call_args
    assert "bool" in call_args["query"]

    # Check for sender filter
    sender_query_found = False
    for filter_item in call_args["query"]["bool"]["filter"]:
        if "term" in filter_item and "sender_email" in filter_item["term"]:
            sender_query_found = True
            assert filter_item["term"]["sender_email"] == "john@example.com"
            break

    assert sender_query_found, "Sender query was not found in the Elasticsearch query"

    # Check for to filter
    to_query_found = False
    for filter_item in call_args["query"]["bool"]["filter"]:
        if "term" in filter_item and "to_email" in filter_item["term"]:
            to_query_found = True
            assert filter_item["term"]["to_email"] == "sarah@example.com"
            break

    assert to_query_found, "To query was not found in the Elasticsearch query"

    # Check for subject filter
    subject_query_found = False
    for filter_item in call_args["query"]["bool"]["must"]:
        if "match_phrase" in filter_item and "subject" in filter_item["match_phrase"]:
            subject_query_found = True
            assert filter_item["match_phrase"]["subject"] == "Meeting"
            break
    assert subject_query_found, "Subject query was not found in the Elasticsearch query"

    # Check for starred filter
    starred_filter_found = False
    unread_filter_found = False
    for filter_item in call_args["query"]["bool"]["filter"]:
        if "term" in filter_item:
            if "is_starred" in filter_item["term"]:
                starred_filter_found = True
                assert filter_item["term"]["is_starred"] is True
            elif "is_unread" in filter_item["term"]:
                unread_filter_found = True
                assert filter_item["term"]["is_unread"] is True

    assert starred_filter_found, (
        "Starred filter was not found in the Elasticsearch query"
    )
    assert unread_filter_found, "Unread filter was not found in the Elasticsearch query"


def test_search_threads_with_exact_phrase(mock_es_client):
    """Test searching threads with exact phrases."""
    # Call the function with the actual query containing an exact phrase
    search_threads('"exact phrase" some text', mailbox_id=1)

    # Verify the search method was called
    assert mock_es_client.search.called

    # Get the parameters that were passed to ES client search
    call_args = mock_es_client.search.call_args[1]

    # Verify the query includes the exact phrase match
    assert "query" in call_args
    assert "bool" in call_args["query"]
    assert "must" in call_args["query"]["bool"]

    exact_phrase_query_found = False
    for query_item in call_args["query"]["bool"]["must"]:
        if "multi_match" in query_item and "type" in query_item["multi_match"]:
            if (
                query_item["multi_match"]["type"] == "phrase"
                and query_item["multi_match"]["query"] == "exact phrase"
            ):
                exact_phrase_query_found = True
                break

    assert exact_phrase_query_found, (
        "Exact phrase query was not found in the Elasticsearch query"
    )


def test_search_threads_with_folder_filter(mock_es_client):
    """Test searching threads with folder filters."""
    # Call the function with the actual query
    search_threads("in:trash some text", mailbox_id=1)

    # Verify the search method was called
    assert mock_es_client.search.called

    # Get the parameters that were passed to ES client search
    call_args = mock_es_client.search.call_args[1]

    # Verify the query includes the trash filter
    assert "query" in call_args
    assert "bool" in call_args["query"]
    assert "filter" in call_args["query"]["bool"]

    trash_filter_found = False
    for query in call_args["query"]["bool"]["filter"]:
        if "term" in query and "is_trashed" in query["term"]:
            trash_filter_found = True
            assert query["term"]["is_trashed"] is True
            break
    assert trash_filter_found, "Trash filter was not found in the Elasticsearch query"
