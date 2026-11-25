def content_index_body() -> dict:
    return {
        "settings": {
            "number_of_shards": 1,
            "analysis": {
                "analyzer": {
                    "content_search_default": {"type": "custom", "tokenizer": "icu_tokenizer", "filter": ["lowercase"]},
                }
            },
        },
        "mappings": {
            "_meta": {"projection_version": 3},
            "dynamic": "strict",
            "properties": {
                "id": {"type": "keyword"},
                "edition_id": {"type": "keyword"},
                "text_id": {"type": "keyword"},
                "object_key": {"type": "keyword"},
                "offset": {"type": "long"},
                "content": {
                    "type": "wildcard",
                    "fields": {
                        "analyzed": {"type": "text", "analyzer": "content_search_default", "index_options": "offsets"},
                    },
                },
            },
        },
    }
