"""Search functionality for finding threads and messages."""

import json
import logging
import re
from typing import Any, Dict, Optional

from django.conf import settings

from core.search.index import get_es_client
from core.search.mapping import MESSAGE_INDEX
from core.search.parse import parse_search_query

logger = logging.getLogger(__name__)


def search_threads(
    query: str,
    mailbox_ids: Optional[list] = None,
    filters: Optional[Dict[str, Any]] = None,
    from_offset: int = 0,
    size: int = 20,
    profile: bool = False,
) -> Dict[str, Any]:
    """
    Search for threads matching the query.

    Args:
        query: The search query
        mailbox_ids: Optional list of mailbox IDs to filter results
        filters: Additional filters to apply
        from_offset: Pagination offset
        size: Number of results to return
        profile: Whether to profile the search in logs

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

        exact_phrases = parsed_query.get("exact_phrases") or []

        # Add text search if query provided
        if parsed_query.get("text"):
            # To avoid using cross_fields, which has limitations if all the fields don't use the
            # same analyzer, we break down the search into tokens and then use best_fields on each.
            # TODO: this tokenization is very simple and could probably be improved.
            # Another alternative would be to use the _analyze ES endpoint to get the tokens.
            tokens = re.split(r"\s+", parsed_query["text"])

            # For now, to simplify, we consider these tokens as exact matches.
            exact_phrases.extend(tokens)

        # Add exact phrase matches
        for phrase in exact_phrases:
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
                            "sender_email.text",
                            "to_email.text",
                            "cc_email.text",
                            "bcc_email.text",
                            "text_body",
                            "html_body",
                        ],
                        "type": "phrase",
                        "operator": "and",
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
                                {"match": {email_field + ".text": recipient.lower()}},
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
        if mailbox_ids:
            search_body["query"]["bool"]["filter"].append(
                {"terms": {"mailbox_ids": mailbox_ids}}
            )

        # Add other filters if provided
        if filters:
            for field, value in filters.items():
                search_body["query"]["bool"]["filter"].append({"term": {field: value}})

        if profile:
            search_body["profile"] = True

        # Execute search
        # pylint: disable=unexpected-keyword-arg
        results = es.search(index=MESSAGE_INDEX, **search_body)

        if profile:
            logger.debug("Search body: %s", json.dumps(search_body, indent=2))
            logger.debug("Results: %s", json.dumps(results, indent=2))

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

            thread_ids = set()
            if "hits" in hits and isinstance(hits["hits"], list):
                for hit in hits["hits"]:
                    if hit["_source"]["thread_id"] not in thread_ids:
                        thread_items.append(
                            {
                                "id": hit["_source"]["thread_id"],
                                "score": hit.get("_score", 0),
                            }
                        )
                        thread_ids.add(hit["_source"]["thread_id"])

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
