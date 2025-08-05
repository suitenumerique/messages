"""OpenSearch search functionality for messages."""

from core.services.search.index import (
    create_index_if_not_exists,
    delete_index,
    get_opensearch_client,
    index_message,
    index_thread,
    reindex_all,
    reindex_mailbox,
    reindex_thread,
)
from core.services.search.mapping import MESSAGE_INDEX, MESSAGE_MAPPING
from core.services.search.parse import parse_search_query
from core.services.search.search import search_threads

__all__ = [
    # Mapping
    "MESSAGE_INDEX",
    "MESSAGE_MAPPING",
    # Client & Index management
    "get_opensearch_client",
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
