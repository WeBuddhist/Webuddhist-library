from typing import LiteralString


def segment_lines(segment: LiteralString, prefix: LiteralString = "") -> LiteralString:
    """Bind `<prefix>lines`, the segment's spans in reading order, and `<prefix>min_start`."""
    return f"""
    CALL ({segment}) {{
        MATCH (span:Span)-[:SPAN_OF]->({segment})
        WITH span ORDER BY span.start, span.end
        RETURN collect({{start: span.start, end: span.end}}) AS {prefix}lines,
               min(span.start) AS {prefix}min_start
    }}
    """


def application_tag_ids(path: LiteralString) -> LiteralString:
    """Tag IDs at the end of `path`, limited to $application when it is set."""
    return f"""[{path}-[:HAS_TAG]->(tag:Tag)
        WHERE $application IS NULL OR (tag)-[:BELONGS_TO]->(:Application {{id: $application}})
        | tag.id]"""


def has_language(node: LiteralString) -> LiteralString:
    """$language matches the node's language: by base code, or exactly when it carries a region."""
    return f"""($language IS NULL OR EXISTS {{
          ({node})-[lang_rel:HAS_LANGUAGE]->(lang:Language {{code: $language_base}})
          WHERE NOT $language CONTAINS '-' OR toLower(coalesce(lang_rel.bcp47, lang.code)) = $language
      }})"""


def match_in_id_order(node: LiteralString, label: LiteralString) -> LiteralString:
    """Match one node per entry of $ids, in the order of $ids."""
    return f"""
    UNWIND range(0, size($ids) - 1) AS idx
    MATCH ({node}:{label} {{id: $ids[idx]}})
    WITH {node}, idx
    ORDER BY idx
    """


def replace_license(label: LiteralString) -> LiteralString:
    return f"""
    MATCH (n:{label} {{id: $id}})
    OPTIONAL MATCH (n)-[existing:HAS_LICENSE]->()
    DELETE existing
    WITH n
    MATCH (license:LicenseType {{name: $license}})
    MERGE (n)-[:HAS_LICENSE]->(license)
    FINISH
    """


def replace_language(label: LiteralString) -> LiteralString:
    return f"""
    MATCH (n:{label} {{id: $id}})
    OPTIONAL MATCH (n)-[existing:HAS_LANGUAGE]->()
    DELETE existing
    WITH n
    MATCH (lang:Language {{code: $language_code}})
    MERGE (n)-[:HAS_LANGUAGE {{bcp47: $bcp47}}]->(lang)
    FINISH
    """
