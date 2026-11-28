# Proposed extensions to the graph schema

These examples are design sketches. The [syntax reference](../reference/graph-schema.md) describes the accepted YAML and where the backend checks these rules today.

## Property presence

A predicate could test whether a property exists, without assuming a value.

```yaml
when:
  source:
    properties: {content_key: {exists: true}}
```

A predicate only selects nodes or links. To require the property itself, put the condition on its declaration. For editions, the existing `excerpt_edition` YAML anchor identifies nodes whose type is excerpt.

```yaml
content_key:
  type: string
  presence: {unless: *excerpt_edition}
```

Here `presence` specifies both outcomes. When the Edition is an excerpt, the key must be absent. Otherwise it must exist. Its value must be a string whenever present. A complementary `presence: {when: ...}` would require the property exactly when the node predicate holds. A field would use either `presence` or the existing boolean `required`.

## Cycles

```yaml
SUBSECTION_OF:
  - target: TableOfContentsSection
    cardinality: "0..* -> 0..1"
    acyclic: true
```

A section must never reach itself by following one or more outgoing `SUBSECTION_OF` edges. This allows a valid hierarchy of any depth and rejects a cycle of any length.

## Comparing two paths

A relationship rule could use `compare` to compare values reached from its endpoints. In these sketches, a path starts with `source` or `target`. A step such as `"->EDITION_OF"` follows an outgoing edge to its target. A step such as `"<-SPAN_OF"` follows an incoming edge to its source. An optional final `".start"` reads a property. Each operand below is single-valued under the existing cardinalities.

On `Recording.FOLLOWS`, the Audio and Edition must lead to the same Text node.

```yaml
compare:
  - left: [source, "->RENDITION_OF", "->RECITATION_OF"]
    op: same_node
    right: [target, "->EDITION_OF"]
```

On `SUBSECTION_OF`, the child and parent must belong to the same table of contents. The child's span must also fit inside the parent's span.

```yaml
compare:
  - left: [source, "->SECTION_OF"]
    op: same_node
    right: [target, "->SECTION_OF"]
  - left: [source, "<-SPAN_OF", ".start"]
    op: ">="
    right: [target, "<-SPAN_OF", ".start"]
  - left: [source, "<-SPAN_OF", ".end"]
    op: "<="
    right: [target, "<-SPAN_OF", ".end"]
```

These comparisons would run for each constrained edge. A missing or ambiguous operand would fail the comparison. `same_node` compares Neo4j node identity.

## Uniqueness across owners

Localized title uniqueness groups data from several nodes and an edge. A graph-level constraint could use a Cypher pattern to bind the owner and the values that form the key.

```yaml
constraints:
  text_title_unique:
    unique_across:
      match: |
        (owner:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(title:LocalizedText)
                    -[tag:HAS_LANGUAGE]->(lang:Language),
        (owner)-[:HAS_LANGUAGE]->(lang)
      owner: owner
      key: [title.text, "toLower(coalesce(tag.bcp47, lang.code))"]
```

Each key may belong to at most one distinct Text. Repeated rows for the same Text are allowed. Binding `lang` twice requires the title's language to be a language of its owner. Only the language component is lowercased. Implementing this would require dependency tracking for the entire match and locking competing writes before checking uniqueness.
