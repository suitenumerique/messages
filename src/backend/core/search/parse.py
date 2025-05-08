"""Parsing functionality for search queries."""

import re
from typing import Any, Dict


def parse_search_query(query: str) -> Dict[str, Any]:
    """
    Parse a search query string and extract modifiers.

    Supports Gmail-style modifiers:
    - from: (de:) - sender email/name
    - to: (a:) - recipient
    - cc: (copie:) - carbon copy
    - bcc: (cci:) - blind carbon copy
    - subject: (sujet:) - subject text
    - "exact phrase" - quoted text for exact matching
    - in:trash (dans:corbeille) - in trash
    - in:sent (dans:envoyes or dans:envoyés) - sent items
    - in:draft (dans:brouillons) - drafts
    - is:starred (est:suivi) - starred
    - is:read (est:lu) - read
    - is:unread (est:nonlu) - unread

    Args:
        query: The search query string

    Returns:
        Dict with extracted modifiers and remaining text. Only includes keys that
        are found in the query, with the exception of "text" which is always included.
    """
    # Initialize result with empty text
    result = {"text": ""}
    if not query:
        return result

    # Define modifiers and their keywords
    modifiers = {
        # Value-taking modifiers
        "from": ["from:", "de:"],
        "to": ["to:", "a:", "à:"],
        "cc": ["cc:", "copie:"],
        "bcc": ["bcc:", "cci:"],
        "subject": ["subject:", "sujet:"],
        # Flag modifiers
        "in_trash": ["in:trash", "dans:corbeille"],
        "in_sent": ["in:sent", "dans:envoyes", "dans:envoyés"],
        "in_draft": ["in:draft", "dans:brouillons"],
        "is_starred": ["is:starred", "est:suivi"],
        "is_read_true": ["is:read", "est:lu"],
        "is_read_false": ["is:unread", "est:nonlu"],
    }

    # Split modifiers into value-taking and flag modifiers
    value_modifiers = ["from", "to", "cc", "bcc", "subject"]
    flag_modifiers = {
        "in_trash": "in_folder",
        "in_sent": "in_folder",
        "in_draft": "in_folder",
        "is_starred": "is_starred",
        "is_read_true": "is_read",
        "is_read_false": "is_read",
    }
    flag_values = {
        "in_trash": "trash",
        "in_sent": "sent",
        "in_draft": "draft",
        "is_starred": True,
        "is_read_true": True,
        "is_read_false": False,
    }

    value_prefixes = {
        prefix: mod_key for mod_key in value_modifiers for prefix in modifiers[mod_key]
    }
    value_prefixes_items = sorted(
        value_prefixes.items(), key=lambda x: len(x[0]), reverse=True
    )

    # 1. Extract exact phrases in quotes
    processed_query = query
    exact_phrases = []

    # 1.1 First, extract quoted values following modifiers (like subject:"Meeting")
    for prefix, mod_key in value_prefixes_items:
        pattern = rf'{re.escape(prefix)}\s*"([^"]*)"'
        for match in re.finditer(pattern, processed_query, re.IGNORECASE):
            full_match = match.group(0)
            value = match.group(1)

            # Add to result
            if mod_key not in result:
                result[mod_key] = []
            result[mod_key].append(value)

            # Remove from query
            processed_query = processed_query.replace(full_match, " ", 1)

    # 1.2 Extract remaining quoted phrases as exact phrases
    quote_pattern = r'"([^"]*)"'
    for match in re.finditer(quote_pattern, processed_query):
        exact_phrases.append(match.group(1))
        processed_query = processed_query.replace(match.group(0), " ", 1)

    if exact_phrases:
        result["exact_phrases"] = exact_phrases

    # 2. Extract value modifiers (both with and without spaces)
    for prefix, mod_key in value_prefixes_items:
        # Match both patterns: from:value and from: value
        pattern = rf"{re.escape(prefix)}\s*(\S+)"
        for match in re.finditer(pattern, processed_query, re.IGNORECASE):
            full_match = match.group(0)
            value = match.group(1)

            # Add to result
            if mod_key not in result:
                result[mod_key] = []
            result[mod_key].append(value)

            # Remove from query
            processed_query = processed_query.replace(full_match, " ", 1)

    # 3. Extract flag modifiers and add remaining text
    tokens = processed_query.split()
    remaining_tokens = []

    for token in tokens:
        flag_found = False

        # Extract flag modifiers (in:trash, is:starred)
        for mod_key, prefixes in modifiers.items():
            if mod_key not in value_modifiers:  # Only check flag modifiers
                if any(token.lower() == prefix.lower() for prefix in prefixes):
                    # Set the appropriate flag
                    result_key = flag_modifiers[mod_key]
                    result[result_key] = flag_values[mod_key]
                    flag_found = True
                    break

        # 4. Add remaining tokens to text field
        if not flag_found:
            remaining_tokens.append(token)

    result["text"] = " ".join(remaining_tokens)

    return result
