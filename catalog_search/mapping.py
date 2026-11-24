def _tibetan_analyzer(char_filter: str, token_filter: str) -> dict:
    return {"type": "custom", "tokenizer": "tibetan", "char_filter": [char_filter], "filter": [token_filter]}


def _sanskrit_char_filter(input_method: str) -> dict:
    return {
        "type": "iast",
        "input_method": input_method,
        "lenient": True,
        "filter_geminates": True,
        "normalize_anusvara": True,
    }


def catalog_index_body() -> dict:
    return {
        "settings": {
            "number_of_shards": 1,
            "analysis": {
                "analyzer": {
                    "catalog_folded": {
                        "type": "custom",
                        "tokenizer": "icu_tokenizer",
                        "filter": ["lowercase", "icu_folding"],
                    },
                    "catalog_plain": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase"],
                    },
                    "catalog_tibetan": _tibetan_analyzer(
                        "catalog_tibetan_lenient_char", "catalog_tibetan_lenient_filter"
                    ),
                    "catalog_tibetan_ewts": _tibetan_analyzer(
                        "catalog_tibetan_ewts_char", "catalog_tibetan_lenient_filter"
                    ),
                    "catalog_tibetan_phonetic_index": _tibetan_analyzer(
                        "catalog_tibetan_lenient_char", "tibetan-for-english-phonetic"
                    ),
                    "catalog_tibetan_ewts_phonetic_index": _tibetan_analyzer(
                        "catalog_tibetan_ewts_char", "tibetan-for-english-phonetic"
                    ),
                    "catalog_tibetan_phonetic_search": {
                        "type": "custom",
                        "tokenizer": "tibetan-english-phonetic",
                        "char_filter": ["tibetan-english-phonetic"],
                    },
                    "catalog_sanskrit_roman": {
                        "type": "custom",
                        "char_filter": ["catalog_sanskrit_roman_char"],
                        "tokenizer": "iast",
                        "filter": ["lowercase"],
                    },
                    "catalog_sanskrit_deva": {
                        "type": "custom",
                        "char_filter": ["catalog_sanskrit_deva_char"],
                        "tokenizer": "iast",
                        "filter": ["lowercase"],
                    },
                },
                "char_filter": {
                    "catalog_tibetan_lenient_char": {
                        "type": "tibetan",
                        "lenient": True,
                    },
                    "catalog_tibetan_ewts_char": {
                        "type": "tibetan",
                        "lenient": True,
                        "input_method": "ewts",
                    },
                    "catalog_sanskrit_roman_char": _sanskrit_char_filter("roman"),
                    "catalog_sanskrit_deva_char": _sanskrit_char_filter("deva"),
                },
                "filter": {
                    "catalog_tibetan_lenient_filter": {
                        "type": "tibetan",
                        "remove_affixes": True,
                        "normalize_paba": True,
                    },
                },
            },
        },
        "mappings": {
            "_meta": {"projection_version": 2},
            "properties": {
                "id": {"type": "keyword"},
                "document_type": {"type": "keyword"},
                "person_id": {"type": "keyword"},
                "text_id": {"type": "keyword"},
                "bdrc": {"type": "keyword"},
                "wiki": {"type": "keyword"},
                "language": {"type": "keyword"},
                "category_id": {"type": "keyword"},
                "tag_ids": {"type": "keyword"},
                "contributor_ids": {"type": "keyword"},
                "primary_labels": {"type": "text", "analyzer": "catalog_folded"},
                "alt_labels": {"type": "text", "analyzer": "catalog_folded"},
                "search_labels_folded": {"type": "text", "analyzer": "catalog_folded"},
                "search_labels_bo": {
                    "type": "text",
                    "analyzer": "catalog_tibetan",
                    "fields": {
                        "phonetic": {
                            "type": "text",
                            "analyzer": "catalog_tibetan_phonetic_index",
                            "search_analyzer": "catalog_tibetan_phonetic_search",
                        },
                        "ewts": {
                            "type": "text",
                            "analyzer": "catalog_tibetan",
                            "search_analyzer": "catalog_tibetan_ewts",
                        },
                    },
                },
                "search_labels_bo_ewts": {
                    "type": "text",
                    "analyzer": "catalog_tibetan_ewts",
                    "fields": {
                        "phonetic": {
                            "type": "text",
                            "analyzer": "catalog_tibetan_ewts_phonetic_index",
                            "search_analyzer": "catalog_tibetan_phonetic_search",
                        },
                    },
                },
                "search_labels_sa": {"type": "text", "analyzer": "catalog_sanskrit_roman"},
                "search_labels_sa_deva": {"type": "text", "analyzer": "catalog_sanskrit_deva"},
                "search_labels_plain": {"type": "text", "analyzer": "catalog_plain"},
            },
        },
    }
