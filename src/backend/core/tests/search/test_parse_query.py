from core.search.parse import parse_search_query


def test_basic_query_without_modifiers():
    """Test basic text query with no modifiers."""
    query = "simple search query"
    result = parse_search_query(query)

    # Only text should be present in the result
    assert result == {"text": "simple search query"}


def test_exact_phrase_quotes():
    """Test parsing of exact phrases within quotes."""
    query = '"exact phrase" regular text'
    result = parse_search_query(query)

    # Only text and exact_phrases should be present
    assert result == {"text": "regular text", "exact_phrases": ["exact phrase"]}


def test_multiple_exact_phrases():
    """Test parsing of multiple exact phrases."""
    query = '"first phrase" some text "second phrase" more text'
    result = parse_search_query(query)

    # Only text and exact_phrases should be present
    assert result == {
        "text": "some text more text",
        "exact_phrases": ["first phrase", "second phrase"],
    }


def test_from_modifier_english():
    """Test parsing 'from:' modifier in English."""
    # With full email
    query = "from:john@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "from": ["john@example.com"]}

    # With name only
    query = "from:John some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "from": ["John"]}


def test_from_modifier_french():
    """Test parsing 'de:' modifier in French."""
    query = "de:john@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "from": ["john@example.com"]}


def test_to_modifier_english():
    """Test parsing 'to:' modifier in English."""
    query = "to:sarah@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "to": ["sarah@example.com"]}


def test_to_modifier_french():
    """Test parsing 'à:' modifier in French."""
    query = "à:sarah@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "to": ["sarah@example.com"]}


def test_cc_modifier_english():
    """Test parsing 'cc:' modifier in English."""
    query = "cc:robert@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "cc": ["robert@example.com"]}


def test_cc_modifier_french():
    """Test parsing 'copie:' modifier in French."""
    query = "copie:robert@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "cc": ["robert@example.com"]}


def test_bcc_modifier_english():
    """Test parsing 'bcc:' modifier in English."""
    query = "bcc:maria@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "bcc": ["maria@example.com"]}


def test_bcc_modifier_french():
    """Test parsing 'cci:' modifier in French."""
    query = "cci:maria@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "bcc": ["maria@example.com"]}


def test_subject_modifier_english():
    """Test parsing 'subject:' modifier in English."""
    query = "subject:Meeting some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "subject": ["Meeting"]}


def test_subject_modifier_french():
    """Test parsing 'sujet:' modifier in French."""
    query = "sujet:Réunion some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "subject": ["Réunion"]}


def test_in_trash_modifier_english():
    """Test parsing 'in:trash' modifier in English."""
    query = "in:trash some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "in_folder": "trash"}


def test_in_trash_modifier_french():
    """Test parsing 'dans:corbeille' modifier in French."""
    query = "dans:corbeille some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "in_folder": "trash"}


def test_in_sent_modifier_english():
    """Test parsing 'in:sent' modifier in English."""
    query = "in:sent some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "in_folder": "sent"}


def test_in_sent_modifier_french_with_accent():
    """Test parsing 'dans:envoyés' modifier in French with accent."""
    query = "dans:envoyés some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "in_folder": "sent"}


def test_in_sent_modifier_french_without_accent():
    """Test parsing 'dans:envoyes' modifier in French without accent."""
    query = "dans:envoyes some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "in_folder": "sent"}


def test_in_draft_modifier_english():
    """Test parsing 'in:draft' modifier in English."""
    query = "in:draft some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "in_folder": "draft"}


def test_in_draft_modifier_french():
    """Test parsing 'dans:brouillons' modifier in French."""
    query = "dans:brouillons some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "in_folder": "draft"}


def test_is_starred_modifier_english():
    """Test parsing 'is:starred' modifier in English."""
    query = "is:starred some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "is_starred": True}


def test_is_starred_modifier_french():
    """Test parsing 'est:suivi' modifier in French."""
    query = "est:suivi some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "is_starred": True}


def test_is_read_modifier_english():
    """Test parsing 'is:read' modifier in English."""
    query = "is:read some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "is_read": True}


def test_is_read_modifier_french():
    """Test parsing 'est:lu' modifier in French."""
    query = "est:lu some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "is_read": True}


def test_is_unread_modifier_english():
    """Test parsing 'is:unread' modifier in English."""
    query = "is:unread some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "is_read": False}


def test_is_unread_modifier_french():
    """Test parsing 'est:nonlu' modifier in French."""
    query = "est:nonlu some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "is_read": False}


def test_multiple_modifiers():
    """Test parsing multiple modifiers in a single query."""
    query = (
        "from:john@example.com to:sarah@example.com subject:Meeting is:unread some text"
    )
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "from": ["john@example.com"],
        "to": ["sarah@example.com"],
        "subject": ["Meeting"],
        "is_read": False,
    }


def test_multiple_same_type_modifiers():
    """Test parsing multiple modifiers of the same type."""
    query = "from:john@example.com from:sarah@example.com some text"
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "from": ["john@example.com", "sarah@example.com"],
    }


def test_case_insensitivity():
    """Test case insensitivity of modifier keys."""
    query = "FROM:john@example.com SUBJECT:Meeting some text"
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "from": ["john@example.com"],
        "subject": ["Meeting"],
    }


def test_modifier_value_with_spaces():
    """Test parsing modifier values that contain spaces."""
    query = 'subject:"Meeting Agenda" some text'
    result = parse_search_query(query)

    assert result == {"text": "some text", "subject": ["Meeting Agenda"]}


def test_modifier_at_end_of_query():
    """Test parsing modifiers that appear at the end of the query."""
    query = "some text from:john@example.com"
    result = parse_search_query(query)

    assert result == {"text": "some text", "from": ["john@example.com"]}


def test_multiple_to_modifiers():
    """Test handling multiple to: modifiers."""
    query = "to:john@example.com to:sarah@example.com to:david@example.com some text"
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "to": ["john@example.com", "sarah@example.com", "david@example.com"],
    }


def test_quoted_values_with_special_chars():
    """Test parsing quoted values with special characters."""
    query = 'subject:"Meeting: Q3 (2023) - $5M target" some text'
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "subject": ["Meeting: Q3 (2023) - $5M target"],
    }


def test_empty_quoted_values():
    """Test handling empty quoted values."""
    query = 'subject:"" some text'
    result = parse_search_query(query)

    assert result == {"text": "some text", "subject": [""]}


def test_modifier_without_value():
    """Test handling a modifier without a value."""
    query = "from: some text"
    result = parse_search_query(query)

    # Should treat "some" as the value for the from: modifier
    assert result == {"text": "text", "from": ["some"]}

    query = "bcc: some text"
    result = parse_search_query(query)

    # Should treat "some" as the value for the from: modifier
    assert result == {"text": "text", "bcc": ["some"]}


def test_modifier_with_space():
    """Test parsing modifiers with spaces before the value."""
    query = "from: john@example.com some text"
    result = parse_search_query(query)

    assert result == {"text": "some text", "from": ["john@example.com"]}


def test_multiple_quoted_modifiers():
    """Test handling multiple modifiers with quoted values."""
    query = 'subject:"Meeting Notes" from:"John Smith" to:"Team" some text'
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "subject": ["Meeting Notes"],
        "from": ["John Smith"],
        "to": ["Team"],
    }


def test_quoted_text_containing_modifiers():
    """Test that modifiers inside quotes are not parsed as modifiers."""
    query = '"from:john@example.com" some text'
    result = parse_search_query(query)

    assert result == {"text": "some text", "exact_phrases": ["from:john@example.com"]}

    query = '"bcc: john@example.com" some text'
    result = parse_search_query(query)

    assert result == {"text": "some text", "exact_phrases": ["bcc: john@example.com"]}

    query = 'nice: "test bcc: john@example.com" some text'
    result = parse_search_query(query)

    assert result == {
        "text": "nice: some text",
        "exact_phrases": ["test bcc: john@example.com"],
    }


def test_unterminated_quotes():
    """Test handling unterminated quotes gracefully."""
    query = 'subject:"Meeting Notes some text'
    result = parse_search_query(query)

    # Should extract incomplete phrase and add appropriate modifiers
    assert result == {"text": "Notes some text", "subject": ['"Meeting']}


def test_unicode_in_modifier_values():
    """Test handling unicode characters in modifier values."""
    query = 'from:"José Martínez" to:"François Dupont" subject:"会議の議題" some text'
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "from": ["José Martínez"],
        "to": ["François Dupont"],
        "subject": ["会議の議題"],
    }


def test_conflicting_folder_modifiers():
    """Test handling conflicting folder modifiers (last one wins)."""
    query = "in:trash in:sent some text"
    result = parse_search_query(query)

    # Last folder modifier should win
    assert result == {"text": "some text", "in_folder": "sent"}


def test_conflicting_read_flags():
    """Test handling conflicting read flag modifiers (last one wins)."""
    query = "is:read is:unread some text"
    result = parse_search_query(query)

    # Last read flag should win
    assert result == {"text": "some text", "is_read": False}


def test_combined_modifiers_and_exact_phrases():
    """Test complex query with modifiers and exact phrases."""
    query = 'from:john@example.com "exact phrase" subject:"Meeting Notes" is:unread in:sent "another phrase" some text'
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "from": ["john@example.com"],
        "subject": ["Meeting Notes"],
        "is_read": False,
        "in_folder": "sent",
        "exact_phrases": ["exact phrase", "another phrase"],
    }


def test_modifier_at_beginning_middle_and_end():
    """Test modifiers appearing at different positions in the query."""
    query = "from:john@example.com some text subject:Meeting to:team@example.com"
    result = parse_search_query(query)

    assert result == {
        "text": "some text",
        "from": ["john@example.com"],
        "subject": ["Meeting"],
        "to": ["team@example.com"],
    }
