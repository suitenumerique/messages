"""Search functionality for finding threads and messages."""

import logging
from typing import Any, Dict, Optional

from django.conf import settings

from core.search.index import get_es_client
from core.search.mapping import MESSAGE_INDEX
from core.search.parse import parse_search_query

logger = logging.getLogger(__name__)


def search_threads(
    query: str,
    mailbox_id: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    from_offset: int = 0,
    size: int = 20,
) -> Dict[str, Any]:
    """
    Search for threads matching the query.

    Args:
        query: The search query
        mailbox_id: Optional mailbox ID to filter results
        filters: Additional filters to apply
        from_offset: Pagination offset
        size: Number of results to return

    Returns:
        Dictionary with thread search results: {"threads": [...], "total": int, "from": int, "size": int}
    """
    # Check if Elasticsearch is enabled
    if not getattr(settings, "ELASTICSEARCH_INDEX_THREADS", True):
        logger.debug("Elasticsearch search is disabled, returning empty results")
        return {"threads": [], "total": 0, "from": from_offset, "size": size}

    try:
        es = get_es_client()

        # Parse the query for modifiers
        parsed_query = parse_search_query(query)

        # Build the search query
        search_body = {
            "query": {"bool": {"must": [], "should": [], "filter": []}},
            "from_": from_offset,
            "size": size,
            "sort": [{"created_at": {"order": "desc"}}],
        }

        # Add text search if query provided
        if parsed_query.get("text"):
            search_body["query"]["bool"]["must"].append(
                {
                    "multi_match": {
                        "query": parsed_query["text"],
                        "fields": [
                            "subject^3",  # Boost subject relevance
                            "sender_name^2",
                            "to_name",
                            "cc_name",
                            "bcc_name",
                            "text_body",
                            "html_body",
                        ],
                        "type": "best_fields",
                        "operator": "and",
                        "fuzziness": "AUTO",
                    }
                }
            )

        # Add exact phrase matches
        if "exact_phrases" in parsed_query:
            for phrase in parsed_query["exact_phrases"]:
                search_body["query"]["bool"]["must"].append(
                    {
                        "multi_match": {
                            "query": phrase,
                            "fields": [
                                "subject",
                                "sender_name",
                                "to_name",
                                "cc_name",
                                "bcc_name",
                                "text_body",
                                "html_body",
                            ],
                            "type": "phrase",
                        }
                    }
                )

        # Add sender filter
        if "from" in parsed_query:
            for sender in parsed_query["from"]:
                if "@" in sender and not sender.startswith("@"):
                    # Exact email match
                    search_body["query"]["bool"]["filter"].append(
                        {"term": {"sender_email": sender.lower()}}
                    )
                else:
                    # Substring match
                    search_body["query"]["bool"]["should"].append(
                        {"wildcard": {"sender_email": f"*{sender.lower()}*"}}
                    )
                    search_body["query"]["bool"]["should"].append(
                        {"wildcard": {"sender_name": f"*{sender}*"}}
                    )

                # At least one of the should clauses must match
                if len(search_body["query"]["bool"]["should"]) > 0:
                    search_body["query"]["bool"]["minimum_should_match"] = 1

        # Add recipient filters (to, cc, bcc) using new mapping fields
        recipient_fields = {
            "to": ("to_email", "to_name"),
            "cc": ("cc_email", "cc_name"),
            "bcc": ("bcc_email", "bcc_name"),
        }
        for recipient_type, (email_field, name_field) in recipient_fields.items():
            if recipient_type in parsed_query:
                for recipient in parsed_query[recipient_type]:
                    if "@" in recipient and not recipient.startswith("@"):
                        # Exact email match
                        search_body["query"]["bool"]["filter"].append(
                            {"term": {email_field: recipient.lower()}}
                        )
                    else:
                        # Substring match on email and name
                        search_body["query"]["bool"]["should"].extend(
                            [
                                {"wildcard": {email_field: f"*{recipient.lower()}*"}},
                                {"wildcard": {name_field: f"*{recipient}*"}},
                            ]
                        )
                        search_body["query"]["bool"]["minimum_should_match"] = 1

        # Add subject filter
        if "subject" in parsed_query:
            for subject_term in parsed_query["subject"]:
                search_body["query"]["bool"]["must"].append(
                    {"match_phrase": {"subject": subject_term}}
                )

        # Add in: filters (trash, sent, draft)
        if parsed_query.get("in_sent"):
            search_body["query"]["bool"]["filter"].append({"term": {"is_sender": True}})
        if parsed_query.get("in_draft"):
            search_body["query"]["bool"]["filter"].append({"term": {"is_draft": True}})
        if parsed_query.get("in_trash"):
            search_body["query"]["bool"]["filter"].append(
                {"term": {"is_trashed": True}}
            )

        # Add is: filters (starred, read, unread)
        if parsed_query.get("is_starred", False):
            search_body["query"]["bool"]["filter"].append(
                {"term": {"is_starred": True}}
            )

        if "is_read" in parsed_query:
            if parsed_query["is_read"]:
                # Read messages have is_unread=False
                search_body["query"]["bool"]["filter"].append(
                    {"term": {"is_unread": False}}
                )
            else:
                # Unread messages have is_unread=True
                search_body["query"]["bool"]["filter"].append(
                    {"term": {"is_unread": True}}
                )

        # Add mailbox filter if provided
        if mailbox_id:
            search_body["query"]["bool"]["filter"].append(
                {"term": {"mailbox_id": mailbox_id}}
            )

        # Add other filters if provided
        if filters:
            for field, value in filters.items():
                search_body["query"]["bool"]["filter"].append({"term": {field: value}})

        # Execute search
        # pylint: disable=unexpected-keyword-arg
        results = es.search(index=MESSAGE_INDEX, **search_body)

        # Process results - extract thread IDs
        thread_items = []
        total = 0

        if results and "hits" in results:
            hits = results["hits"]

            if (
                "total" in hits
                and isinstance(hits["total"], dict)
                and "value" in hits["total"]
            ):
                total = hits["total"]["value"]
            elif "total" in hits and isinstance(hits["total"], int):
                # Handle older Elasticsearch versions
                total = hits["total"]

            if "hits" in hits and isinstance(hits["hits"], list):
                for hit in hits["hits"]:
                    if "_source" in hit and "thread_id" in hit["_source"]:
                        thread_items.append(
                            {
                                "id": hit["_source"]["thread_id"],
                                "score": hit.get("_score", 0),
                                "subject": hit["_source"].get("subject", ""),
                            }
                        )

        return {
            "threads": thread_items,
            "total": total,
            "from": from_offset,
            "size": size,
        }

    # pylint: disable=broad-exception-caught
    except Exception as e:  # noqa: BLE001
        logger.error("Error searching threads: %s", e)
        return {
            "threads": [],
            "total": 0,
            "from": from_offset,
            "size": size,
            "error": str(e),
        }
