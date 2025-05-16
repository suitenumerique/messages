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
        "_source": {
            "includes": [
                "thread_id",
            ]
        },
        "properties": {
            # Join to allow parent-child relationship between Thread and Message
            "relation": {"type": "join", "relations": {"thread": "message"}},
            # Thread fields
            "thread_id": {"type": "keyword"},
            "mailbox_ids": {"type": "keyword"},
            # Message fields
            "message_id": {"type": "keyword"},
            "mime_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "sent_at": {"type": "date"},
            # Subject with text analysis for searching
            "subject": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
            # Contacts
            "sender_name": {
                "type": "text",
                "analyzer": "email_analyzer",
                "search_analyzer": "standard",
            },
            "sender_email": {
                "type": "keyword",
                "fields": {
                    "text": {
                        "type": "text",
                        "analyzer": "email_analyzer",
                        "search_analyzer": "standard",
                    }
                },
            },
            "to_name": {
                "type": "text",
                "analyzer": "email_analyzer",
                "search_analyzer": "standard",
            },
            "to_email": {
                "type": "keyword",
                "fields": {
                    "text": {
                        "type": "text",
                        "analyzer": "email_analyzer",
                        "search_analyzer": "standard",
                    }
                },
            },
            "cc_name": {
                "type": "text",
                "analyzer": "email_analyzer",
                "search_analyzer": "standard",
            },
            "cc_email": {
                "type": "keyword",
                "fields": {
                    "text": {
                        "type": "text",
                        "analyzer": "email_analyzer",
                        "search_analyzer": "standard",
                    }
                },
            },
            "bcc_name": {
                "type": "text",
                "analyzer": "email_analyzer",
                "search_analyzer": "standard",
            },
            "bcc_email": {
                "type": "keyword",
                "fields": {
                    "text": {
                        "type": "text",
                        "analyzer": "email_analyzer",
                        "search_analyzer": "standard",
                    }
                },
            },
            # Content fields
            "text_body": {"type": "text"},
            "html_body": {"type": "text"},
            # Flags
            "is_draft": {"type": "boolean"},
            "is_trashed": {"type": "boolean"},
            "is_starred": {"type": "boolean"},
            "is_unread": {"type": "boolean"},
            "is_sender": {"type": "boolean"},
        },
    },
}
