"""Elasticsearch index and mapping configuration."""

# Index name constants
MESSAGE_INDEX = "messages"

# Schema definitions
MESSAGE_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "email_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding", "email_ngram"],
                }
            },
            "filter": {
                "email_ngram": {"type": "edge_ngram", "min_gram": 2, "max_gram": 20}
            },
        },
    },
    "mappings": {
        "properties": {
            # Join to allow parent-child relationship between Thread and Message
            "relation": {"type": "join", "relations": {"thread": "message"}},
            # Thread fields
            "thread_id": {"type": "keyword"},
            "mailbox_id": {"type": "keyword"},
            # Message fields
            "message_id": {"type": "keyword"},
            "mime_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "sent_at": {"type": "date"},
            # Subject with text analysis for searching
            "subject": {
                "type": "text",
                "analyzer": "email_analyzer",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
            # Contacts
            "sender": {
                "properties": {
                    "name": {"type": "text", "analyzer": "email_analyzer"},
                    "email": {"type": "text", "analyzer": "email_analyzer"},
                }
            },
            "recipients": {
                "properties": {
                    "type": {"type": "keyword"},
                    "name": {"type": "text", "analyzer": "email_analyzer"},
                    "email": {"type": "text", "analyzer": "email_analyzer"},
                }
            },
            # Content fields
            "text_body": {"type": "text", "analyzer": "email_analyzer"},
            "html_body": {"type": "text", "analyzer": "email_analyzer"},
            # Flags
            "is_draft": {"type": "boolean"},
            "is_trashed": {"type": "boolean"},
            "is_starred": {"type": "boolean"},
            "is_unread": {"type": "boolean"},
        }
    },
}
