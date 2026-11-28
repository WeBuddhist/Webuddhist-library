# Neo4j graph schema syntax

Edit `database/neo4j_schema.yaml` to declare node properties, relationship endpoints, and how many links each endpoint may have. Neo4j enforces node-property uniqueness through native constraints. The backend generates before-commit APOC triggers for relationship and property checks, together with queries that audit existing data. [Schema declarations](../../database/neo4j_schema.yaml) · [Loader](../../database/schema.py) · [Trigger generator](../../database/neo4j_triggers.py)

## Nodes and properties

The top-level `nodes` map is keyed by Neo4j label. Under a node, `properties` defines its fields and `relationships` defines its outgoing relationship types. This example declares the relevant parts of the Text and Work nodes.

```yaml
nodes:
  Work:
    properties:
      id: {type: string, required: true, unique: true}
  Text:
    properties:
      id: {type: string, required: true, unique: true}
    relationships:
      TEXT_OF:
        - target: Work
          cardinality: "0..* -> 1"
          properties:
            original: {type: boolean, required: true}
        - target: Work
          when: {properties: {original: true}}
          cardinality: "0..1 -> 0..*"
```

Every Text links to exactly one Work. A Work may receive any number of these links, with at most one marked `original: true`.

| Property field | Meaning |
|---|---|
| `type` | One of `string`, `integer`, `boolean`, `datetime` (Neo4j zoned datetime), or `any`. |
| `required` | `true` requires a value. With `false`, a missing value is allowed and a present value still has to satisfy its definition. |
| `unique` | On a node property, `true` creates a uniqueness constraint for that property within the node label. Combine with `required: true` if every node must have a value. |
| `indexed` | On a node property, `true` creates a range index for lookups and allows repeated values. A uniqueness constraint already supplies this index when `unique: true`. |
| `description` | Optional error text for a property with domain-specific meaning. Omit it when the declaration already explains the rule. The default error identifies the field, e.g. `Invalid Edition.content_key`. |

The installer waits for each declared property index to come online. Neo4j range index keys are limited to about 8 kB, so an oversized stored string can cause index creation to fail. [Neo4j index key limits](https://neo4j.com/docs/cypher-manual/current/indexes/performance-indexes/using-indexes/)

Seeded enum lookup nodes have a required, unique string name.

```yaml
EditionType:
  properties:
    name: *required_unique_string
```

Python enums define the accepted lookup names, recording formats, and segment types. The migration uses these enums to seed lookup nodes and audit stored values, including values written directly in Cypher. [API enums](../../models/enums.py) · [Migration audit](../../database/migrations.py)

The schema uses YAML anchors and aliases for repeated property definitions and rules. The loader uses YAML 1.1 scalar resolution, so quote strings such as `"yes"` when a string is intended. Duplicate explicit keys are rejected. [Schema templates](../../database/neo4j_schema.yaml)

## Relationships and cardinality

Declare a relationship on its source label. Its value is a nonempty list of rules. Each rule has a `target` label or nonempty list of labels and a `cardinality`. A base rule can also define edge `properties` and `allow_self`. Self-links are forbidden by default.

Read `"left -> right"` in the stored direction of the relationship.

| Bound | Counts |
|---|---|
| Left | Incoming links from the declared source label, **per target node**. |
| Right | Outgoing links to the declared target labels, **per source node**. |

Each bound accepts `0` (none), `0..1` (at most one), `1` (exactly one), `1..*` (at least one), or `0..*` (any number). The standalone integer `cardinality: 0` forbids matching links. Write other bounds as a quoted pair.

For one source label and relationship type, each target label appears in exactly one base rule. All base rules must agree on the right bound, edge properties, and self-link policy. For example, the `SPAN_OF` base rules all have a right bound of `1`: a Span has exactly one owner across all allowed owner labels. The left bounds vary because a Page can have several Spans while a Cue must have exactly one. [Span declarations](../../database/neo4j_schema.yaml)

The compiler groups node-property checks by label. A relationship rule checks both bounds for an endpoint after acquiring its locks once. Transaction errors and migration audits identify each violated rule and its affected IDs, including when several rules share a trigger.

A base or conditional rule can supply a `description` for its bounds. Use the same description across base rules when they share an outgoing bound and need the same wording. The API returns the failed check’s description in the HTTP 422 problem response. Trigger names and affected IDs stay in database diagnostics. The trigger generator defines the messages for invalid endpoints, self-links, and hand-written Cypher checks beside those checks.

## Conditional rules

Add a rule with `when` to constrain a subset of the targets in the base rules. The base and all applicable conditional bounds apply together. Rule order gives no rule precedence.

**A condition starts on the declared edge.** Its current position determines the available keys.

| Position | Key | Meaning |
|---|---|---|
| Edge or node | `properties` | Every named property must match. A scalar requires equality. A nonempty list matches any of its values. A missing property fails either match. |
| Edge | `source`, `target` | Apply a node predicate to the corresponding endpoint. |
| Node | `has_incoming`, `has_outgoing` | Require at least one incident edge of the named type in that direction. |

Each predicate is a nonempty map. Sibling keys mean AND. A traversal accepts a relationship name for a simple existence check, or a single relationship name mapped to another edge predicate. Nesting repeats the same rules. `source` and `target` always refer to the stored direction of the current edge, including after an incoming traversal.

Property literals can be strings, booleans, integers, or finite floats. On `Contribution.WITH_ROLE`, the source condition selects Audio credits. Each such credit must have exactly one role whose name matches `[narrator, singer, musician]`. Text credits can still use roles such as `author`.

On `Nomen.ALTERNATIVE_OF`, `when: {source: {has_incoming: ALTERNATIVE_OF}}` with `cardinality: 0` prevents chains while allowing multiple alternatives to point to one Nomen.

### Which nodes owe the bound?

For the **left bound**, select target nodes satisfying `when.target`. Count their incoming links that satisfy the edge and source predicates. For the **right bound**, select source nodes satisfying `when.source`. Count their outgoing links that satisfy the edge and target predicates. An omitted predicate matches everything. A selected node with zero links still owes its minimum. Several paths that satisfy one traversal still qualify the counted link only once.

### Example: diplomatic pagination

```yaml
PAGINATION_OF:
  - target: Edition
    cardinality: "0..1 -> 1"
  - target: Edition
    when:
      target:
        has_outgoing:
          HAS_TYPE:
            target: {properties: {name: diplomatic}}
    cardinality: "1 -> 0..*"
```

The first `target` inside `when` is the Edition. The second is the EditionType reached through `HAS_TYPE`. To match a property on the `HAS_TYPE` edge itself, put `properties` directly under `HAS_TYPE`. Edge and endpoint predicates there must match on the same traversal.

| Edition type | Zero Paginations | One Pagination | Two Paginations |
|---|---|---|---|
| Diplomatic | Rejected by the conditional minimum | Allowed | Rejected by the base maximum |
| Critical | Allowed | Allowed | Rejected by the base maximum |

Every Pagination must still belong to exactly one Edition. The conditional right bound of `0..*` adds no obligation on the Pagination. Renaming a linked EditionType can change the conditional minimum even if the Pagination edges stay the same.

### Example: excerpt editions

An excerpt requires one outgoing `EXCERPT_OF` edge and at least one incoming `SPAN_OF` edge. The converse matters too. On `Edition.HAS_TYPE`, a condition on the source selects editions that have an outgoing `EXCERPT_OF` edge, and a condition on the target selects the type named `excerpt`. A right bound of `1` requires an edition with an excerpt source to have that type. A second rule does the same for editions with incoming spans. The `forbid_excerpt_target` alias rejects links that would use an excerpt as another excerpt’s source or attach annotations directly to it. [Excerpt rules](../../database/neo4j_schema.yaml)

The API assigns a `content_key` to stored editions and omits it for excerpts. The migration audit checks existing editions for inconsistent keys. [Edition creation](../../database/edition.py) · [Migration audit](../../database/migrations.py)

Manual triggers check for cycles, compare nodes reached through different paths, compare span values, and enforce uniqueness of a computed title-and-language key. [Manual checks](../../database/neo4j_triggers.py) · [Proposed extensions](../proposals/graph-schema-constraints.md)

## Verification

Run the cardinality, installed-trigger, and excerpt tests with a local Docker runtime. They create a disposable Neo4j database with APOC and exercise both transaction rejection and full-graph audits.

```sh
python -m pytest tests/test_neo4j_triggers.py tests/test_annotations.py tests/test_excerpt_editions.py
```
