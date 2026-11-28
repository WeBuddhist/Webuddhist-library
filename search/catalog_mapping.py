from opensearchpy import Index, Keyword, Text, analyzer, char_filter, token_filter
from opensearchpy.helpers.analysis import Analyzer, CharFilter, TokenFilter


def _tibetan_analyzer(name: str, chars: CharFilter, terms: TokenFilter | str) -> Analyzer:
    return analyzer(name, tokenizer="tibetan", char_filter=[chars], filter=[terms])


def _sanskrit_analyzer(script: str) -> Analyzer:
    name = f"catalog_sanskrit_{script}"
    chars = char_filter(
        f"{name}_char", type="iast", input_method=script, lenient=True, filter_geminates=True, normalize_anusvara=True
    )
    return analyzer(name, tokenizer="iast", char_filter=[chars], filter=["lowercase"])


def catalog_index_body() -> dict:
    folded = analyzer("catalog_folded", tokenizer="icu_tokenizer", filter=["lowercase", "icu_folding"])
    plain = analyzer("catalog_plain", tokenizer="standard", filter=["lowercase"])
    tibetan_chars = char_filter("catalog_tibetan_lenient_char", type="tibetan", lenient=True)
    ewts_chars = char_filter("catalog_tibetan_ewts_char", type="tibetan", lenient=True, input_method="ewts")
    tibetan_terms = token_filter(
        "catalog_tibetan_lenient_filter", type="tibetan", remove_affixes=True, normalize_paba=True
    )
    tibetan = _tibetan_analyzer("catalog_tibetan", tibetan_chars, tibetan_terms)
    ewts = _tibetan_analyzer("catalog_tibetan_ewts", ewts_chars, tibetan_terms)
    tibetan_phonetic = _tibetan_analyzer(
        "catalog_tibetan_phonetic_index", tibetan_chars, "tibetan-for-english-phonetic"
    )
    ewts_phonetic = _tibetan_analyzer("catalog_tibetan_ewts_phonetic_index", ewts_chars, "tibetan-for-english-phonetic")
    phonetic_search = analyzer(
        "catalog_tibetan_phonetic_search",
        tokenizer="tibetan-english-phonetic",
        char_filter=["tibetan-english-phonetic"],
    )

    index = Index(name=None).settings(number_of_shards=1)
    mapping = index.get_or_create_mapping().meta("_meta", projection_version=2)
    for name in (
        "id",
        "document_type",
        "person_id",
        "text_id",
        "bdrc",
        "wiki",
        "language",
        "category_id",
        "tag_ids",
        "contributor_ids",
    ):
        mapping.field(name, Keyword())
    for name in ("primary_labels", "alt_labels", "search_labels_folded"):
        mapping.field(name, Text(analyzer=folded))
    mapping.field(
        "search_labels_bo",
        Text(
            analyzer=tibetan,
            fields={
                "phonetic": Text(analyzer=tibetan_phonetic, search_analyzer=phonetic_search),
                "ewts": Text(analyzer=tibetan, search_analyzer=ewts),
            },
        ),
    )
    mapping.field(
        "search_labels_bo_ewts",
        Text(analyzer=ewts, fields={"phonetic": Text(analyzer=ewts_phonetic, search_analyzer=phonetic_search)}),
    )
    mapping.field("search_labels_sa", Text(analyzer=_sanskrit_analyzer("roman")))
    mapping.field("search_labels_sa_deva", Text(analyzer=_sanskrit_analyzer("deva")))
    mapping.field("search_labels_plain", Text(analyzer=plain))
    return index.to_dict()
