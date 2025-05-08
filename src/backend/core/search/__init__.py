"""Elasticsearch search functionality for messages."""

from core.search.index import (
    create_index_if_not_exists,
    delete_index,
    get_es_client,
    index_message,
    index_thread,
    reindex_all,
    reindex_mailbox,
    reindex_thread,
)
from core.search.mapping import MESSAGE_INDEX, MESSAGE_MAPPING
from core.search.parse import parse_search_query
from core.search.search import search_threads

__all__ = [
    # Mapping
    "MESSAGE_INDEX",
    "MESSAGE_MAPPING",
    # Client & Index management
    "get_es_client",
    "create_index_if_not_exists",
    "delete_index",
    # Indexing
    "index_message",
    "index_thread",
    "reindex_all",
    "reindex_mailbox",
    "reindex_thread",
    # Parsing
    "parse_search_query",
    # Searching
    "search_threads",
]
