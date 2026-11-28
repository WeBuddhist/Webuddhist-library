# ruff: noqa: ANN001, S101, SLF001
import asyncio

import pytest
from neo4j.exceptions import ClientError
from pydantic.alias_generators import to_snake
from ruamel.yaml.constructor import ConstructorError, DuplicateKeyError

from database.neo4j_triggers import (
    SYSTEM_DATABASE_NAME,
    TRIGGERS,
    _ENDPOINT_TRIGGER_NAMES,
    _RELATIONSHIP_TRIGGER_NAMES,
    _property_triggers,
    _relationship_triggers,
    install_triggers,
    wait_for_triggers,
)
from database.schema import NODES, RELATIONSHIPS, _load_schema, _property_schema, relationships
from tests.conftest import NEO4J_TRIGGER_REFRESH_SECONDS


def test_allow_self_is_scoped_to_the_declaring_source():
    nodes = {
        "Left": {"relationships": {"LINK": [{"target": "Left", "cardinality": "0..* -> 0..1"}]}},
        "Right": {"relationships": {"LINK": [{"target": ["Left", "Right"], "cardinality": "0..* -> 0..1", "allow_self": True}]}},
    }
    rule = next(rule for rule in _relationship_triggers(tuple(relationships(nodes))) if rule.name == "enforce_no_self_link")
    assert "source = target AND source:Left" in rule.query
    assert "source = target AND source:Left" in rule.audit


def _query_with_metadata(setup_query: str, **event_changes: str) -> str:
    overrides = "".join(f", {key}: {value}" for key, value in event_changes.items())
    return f"""
    CALL {{
        {setup_query}
    }}
    WITH *, {{
        createdNodes: createdNodes, deletedNodes: deletedNodes,
        createdRelationships: createdRelationships, deletedRelationships: deletedRelationships,
        assignedNodeProperties: {{}}, removedNodeProperties: {{}},
        assignedRelationshipProperties: {{}}, removedRelationshipProperties: {{}},
        assignedLabels: {{}}, removedLabels: {{}}
    }} AS events
    CALL apoc.cypher.doIt($trigger_query, events {{.*{overrides}}}) YIELD value
    RETURN value
    """


_ALLOWED_DEGREES = {"0": {0}, "1": {1}, "0..1": {0, 1}, "1..*": {1, 2}, "0..*": {0, 1, 2}}


async def _assert_degree_violations(session, rules, ids, expected):
    event = _query_with_metadata("""
        MATCH (n:Sample|Owner) WHERE n.id IN $ids
        RETURN collect(n) AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
    """)
    rejected = set()
    for rule in rules:
        bad = set(await (await session.run(rule.audit)).value("violating_id"))
        assert bad and bad <= ids
        rejected.update(bad)
        await (await session.run(event, ids=sorted(ids - bad), trigger_query=rule.query)).consume()
        with pytest.raises(ClientError, match=rule.name) as error:
            await (await session.run(event, ids=sorted(bad), trigger_query=rule.query)).consume()
        assert {node_id for node_id in ids if node_id in str(error.value)} == bad
    assert rejected == expected


@pytest.mark.parametrize("source_count", _ALLOWED_DEGREES)
@pytest.mark.parametrize("target_count", _ALLOWED_DEGREES)
@pytest.mark.asyncio(loop_scope="session")
async def test_cardinality_matrix(test_database, source_count, target_count):
    """Run each generated constraint and its audit against nodes of degree zero, one and two at each end."""
    nodes = {"Sample": {"relationships": {"REFERS": [{"target": "Owner", "cardinality": f"{source_count} -> {target_count}"}]}},
             "Owner": {}}
    rules = [r for r in _relationship_triggers(tuple(relationships(nodes))) if r.name.startswith("enforce_sample_refers")]
    expected = {f"s{i}" for i in range(3) if i not in _ALLOWED_DEGREES[target_count]}
    expected.update(f"t{i}" for i in range(3) if i not in _ALLOWED_DEGREES[source_count])
    async with test_database.get_session() as session:
        await (await session.run("""
            CREATE (:Sample {id:'s0'}), (s1:Sample {id:'s1'}), (s2:Sample {id:'s2'}),
                   (:Owner {id:'t0'}), (t1:Owner {id:'t1'}), (t2:Owner {id:'t2'}),
                   (s1)-[:REFERS]->(t2), (s2)-[:REFERS]->(t1), (s2)-[:REFERS]->(t2)
        """)).consume()
        ids = {"s0", "s1", "s2", "t0", "t1", "t2"}
        await _assert_degree_violations(session, rules, ids, expected)


@pytest.mark.asyncio(loop_scope="session")
async def test_combined_degree_counts_wrong_targets_only_toward_the_maximum(test_database):
    nodes = {"Sample": {"relationships": {"REFERS": [{"target": "Owner", "cardinality": "0..* -> 1"}]}}, "Owner": {}}
    rule = next(t for t in _relationship_triggers(tuple(relationships(nodes))) if t.name == "enforce_sample_refers")
    async with test_database.get_session() as session:
        await (await session.run("""
            CREATE (:Sample {id:'missing'})-[:REFERS]->(:Other),
                   (s:Sample {id:'extra'})-[:REFERS]->(:Owner), (s)-[:REFERS]->(:Other)
        """)).consume()
        assert {(r["violating_id"], r["rule"]) async for r in await session.run(rule.audit)} == {
            ("missing", rule.name), ("extra", rule.name + "_max_one"),
        }


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("base", _ALLOWED_DEGREES)
@pytest.mark.parametrize("conditional", _ALLOWED_DEGREES)
@pytest.mark.asyncio(loop_scope="session")
async def test_conditional_cardinality_matrix(test_database, side, base, conditional):
    """The base counts all links. A selected endpoint also owes the bound on its matching links."""
    def cardinality(bound):
        return f"0..* -> {bound}" if side == "source" else f"{bound} -> 0..*"

    when = {side: {"properties": {"selected": True}}, "properties": {"active": True}}
    boolean = {"type": "boolean"}
    nodes = {"Sample": {"relationships": {"REFERS": [
        {"target": "Owner", "cardinality": cardinality(base), "properties": {"active": boolean}},
        {"target": "Owner", "cardinality": cardinality(conditional), "when": when},
    ]}}, "Owner": {}}
    if side == "source":
        label, pattern = "Sample", "(n)-[r:REFERS]->(:Owner)"
    else:
        label, pattern = "Owner", "(:Sample)-[r:REFERS]->(n)"
    nodes[label]["properties"] = {"selected": boolean}
    rules = [rule for rule in _relationship_triggers(tuple(relationships(nodes)), nodes=nodes)
             if rule.name.startswith("enforce_sample_refers") and not rule.name.endswith("_property")]

    rows = [
        {"id": f"case_{int(selected)}_{total}_{matching}", "selected": selected, "total": total, "matching": matching}
        for selected in (False, True) for total in range(3) for matching in range(total + 1)
    ]
    expected = {
        row["id"] for row in rows if row["total"] not in _ALLOWED_DEGREES[base]
        or (row["selected"] and row["matching"] not in _ALLOWED_DEGREES[conditional])
    }
    async with test_database.get_session() as session:
        await (await session.run(f"""
            UNWIND $rows AS row
            CREATE (n:{label} {{id:row.id, selected:row.selected}})
            FOREACH (i IN range(1, row.total) | CREATE {pattern} SET r.active = (i <= row.matching))
        """, rows=rows)).consume()
        await _assert_degree_violations(session, rules, {row["id"] for row in rows}, expected)


@pytest.mark.parametrize("spec", [
    {"target": "Owner", "cardinality": "0..2 -> 1"},
    {"target": "Owner", "cardinality": "one"},
    {"target": "Owner", "cardinality": 1},
    {"target": "Owner", "cardinality": False},
    {"target": "Owner", "cardinality": ["1", "1"]},
    {"target": "Unknown", "cardinality": "1 -> 1"},
    {"target": "Owner", "cardinality": "1 -> 1", "required": False},
    {"target": [], "cardinality": "1 -> 1"},
    {"target": ["Owner", "Owner"], "cardinality": "0..1 -> 1"},
])
def test_invalid_relationship_declaration(spec):
    with pytest.raises(ValueError, match="Sample.REFERS"):
        tuple(relationships({"Sample": {"relationships": {"REFERS": [spec]}}, "Owner": {}}))


def test_span_rules_share_one_owner_limit():
    span, = (rel for rel in RELATIONSHIPS if rel.source == "Span" and rel.type == "SPAN_OF")
    assert span.per_source == "1"
    assert span.per_target == {
        "Cue": "1", "Note": "1", "Mark": "1", "BibliographicMetadata": "1", "TableOfContentsSection": "1",
        "Segment": "1..*", "Page": "1..*", "Attribute": "0..*", "Edition": "0..*",
    }


def _when_triggers(*rules, outgoing=False):
    base = {"target": ["Owner", "Other"], "cardinality": "0..* -> 0..*",
            "properties": {"active": {"type": "boolean"}, "kind": {"type": "string"}}}
    nodes = {"Sample": {"relationships": {"REFERS": [*rules, base]}}, "Owner": {}, "Other": {}, "Marker": {}}
    if outgoing:
        for label in ("Sample", "Owner"):
            nodes[label].setdefault("relationships", {})["FLAG"] = [{"target": "Marker", "cardinality": "0..* -> 0..*"}]
    else:
        nodes["Marker"]["relationships"] = {
            "FLAG": [{"target": ["Sample", "Owner"], "cardinality": "0..* -> 0..*",
                      "properties": {"active": {"type": "boolean"}}}],
            "MARKS": [{"target": "Kind", "cardinality": "0..* -> 0..*"}],
        }
    nodes["Kind"] = {"properties": {"name": {"type": "string"}}}
    nodes["Sample"]["properties"] = {"enabled": {"type": "boolean"}}
    return {t.name: t for t in _relationship_triggers(tuple(relationships(nodes)), nodes=nodes) if "_when_" in t.name}


@pytest.mark.parametrize("when", [
    {}, {"properties": {}}, {"properties": {"typo": True}}, {"target": {}}, {"source": {"has_incoming": "MISSING"}},
    {"properties": {"kind": {}}}, {"properties": {"kind": {"one_of": [1]}}},
    {"target": {"has_outgoing": "FLAG"}}, {"source": {"has_incoming": "FLAG", "has_outgoing": "FLAG"}},
    {"source": {"has_incoming": {"FLAG": {}}}},
    {"source": {"has_incoming": {"FLAG": {}, "OTHER": {}}}}, {"unknown": {"has_incoming": "FLAG"}},
    {"target": {"has_incoming": {"FLAG": {"properties": {"typo": True}}}}},
    {"target": {"has_incoming": {"FLAG": {"source": {"properties": {"typo": True}}}}}},
])
def test_invalid_when(when):
    with pytest.raises(ValueError):
        _when_triggers({"target": "Owner", "when": when, "cardinality": "1 -> 1"})


@pytest.mark.parametrize("values", [[], None, [None], [["nested"]], [float("inf")]])
def test_invalid_property_predicate_values(values):
    with pytest.raises(ValueError):
        _when_triggers({"target": "Owner", "when": {"properties": {"kind": values}}, "cardinality": "0..* -> 1"})


def test_predicate_list_preserves_literal_types():
    rule, = _when_triggers({
        "target": "Owner", "when": {"properties": {"kind": ["1", 1, False, 0.5]}},
        "cardinality": "0..* -> 1",
    }).values()
    for query in (rule.query, rule.audit):
        assert 'r["kind"] IN ["1", 1, false, 0.5]' in query


def test_property_declarations_reject_obsolete_enum_lists(monkeypatch):
    monkeypatch.setitem(NODES, "Example", {"properties": {"kind": {"type": "string", "one_of": ["a"]}}})
    with pytest.raises(ValueError, match="Unknown property options.*one_of"):
        list(_property_triggers())


@pytest.mark.parametrize("on_edge", [False, True])
def test_property_description_must_be_text(monkeypatch, on_edge):
    fields = {"kind": {"type": "string", "description": False}}
    if on_edge:
        nodes = {"Sample": {"relationships": {"REFERS": [
            {"target": "Owner", "cardinality": "0..* -> 1", "properties": fields},
        ]}}, "Owner": {}}
        triggers = _relationship_triggers(tuple(relationships(nodes)), nodes=nodes)
    else:
        monkeypatch.setitem(NODES, "Sample", {"properties": fields})
        triggers = _property_triggers()
    with pytest.raises(TypeError, match=r"kind.*description must be a string"):
        list(triggers)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(("edit", "metadata"), [
    ("REMOVE kind.name", {"removedNodeProperties": "{name:[{node:kind}]}"}),
    ("REMOVE flag.active", {"removedRelationshipProperties": "{active:[{relationship:flag}]}"}),
])
async def test_recursive_when_rechecks_an_owner_after_a_remote_edit(test_database, edit, metadata):
    flag_guard = {
        "properties": {"active": True},
        "source": {"has_outgoing": {"MARKS": {"target": {"properties": {"name": "selected"}}}}},
    }
    checks = _when_triggers({"target": "Owner", "cardinality": "0..* -> 1", "when": {
        "source": {"properties": {"enabled": True}}, "properties": {"active": True},
        "target": {"has_incoming": {"FLAG": flag_guard}},
    }})
    minimum = checks["enforce_sample_refers_when_1"]
    async with test_database.get_session() as session:
        await (await session.run("""
            CREATE (s:Sample {id:'s', enabled:true})-[:REFERS {active:true}]->(o:Owner),
                   (:Sample {enabled:false}), (s)-[:REFERS {active:false}]->(o),
                   (m:Marker {id:'edit'})-[:FLAG {active:true}]->(o),
                   (kind:Kind {id:'edit', name:'selected'}),
                   (m)-[:MARKS]->(kind), (m)-[:MARKS]->(kind),
                   (wrongEdge:Marker)-[:FLAG {active:false}]->(o),
                   (wrongEdge)-[:MARKS]->(:Kind {name:'selected'}),
                   (wrongKind:Marker)-[:FLAG {active:true}]->(o), (wrongKind)-[:MARKS]->(:Kind)
        """)).consume()
        for check in checks.values():
            assert await (await session.run(check.audit)).value() == []
        match = "MATCH (:Marker {id:'edit'})-[flag:FLAG]->(:Owner), (kind:Kind {id:'edit'})"
        await (await session.run(f"{match} {edit}")).consume()
        assert await (await session.run(minimum.audit)).value() == ["s"]
        # Only the remote property event is supplied. The trigger must find s by traversing back.
        event = f"""{match} RETURN flag, kind, [] AS createdNodes, [] AS createdRelationships,
            [] AS deletedRelationships, [] AS deletedNodes"""
        with pytest.raises(ClientError, match=f"{minimum.name}.*IDs: s"):
            await (await session.run(_query_with_metadata(event, **metadata), trigger_query=minimum.query)).consume()


@pytest.mark.parametrize("scope", ["source", "target"])
@pytest.mark.parametrize("direction", ["has_incoming", "has_outgoing"])
@pytest.mark.asyncio(loop_scope="session")
async def test_endpoint_when_counts_and_rechecks_both_ends(test_database, scope, direction):
    rule = {"target": "Owner", "when": {scope: {direction: "FLAG"}}, "cardinality": "1 -> 1"}
    checks = _when_triggers(rule, outgoing=direction == "has_outgoing")
    left, right = "enforce_sample_refers_per_owner_when_1", "enforce_sample_refers_when_1"
    own, opposite = (right, left) if scope == "source" else (left, right)
    label, peer = ("Sample", "Owner") if scope == "source" else ("Owner", "Sample")
    link = "-[:REFERS]->" if scope == "source" else "<-[:REFERS]-"
    flag = "<-[:FLAG]-" if direction == "has_incoming" else "-[:FLAG]->"
    async with test_database.get_session() as session:
        await (await session.run(f"""
            CREATE (m:Marker), (p:{peer} {{id:'peer'}}), (isolated:{label} {{id:'isolated'}}){flag}(m),
                   (a:{label} {{id:'a'}}){link}(p), (b:{label} {{id:'b'}}){link}(p),
                   (a){flag}(m), (b){flag}(m), (unflagged:{label}){link}(p), (m){flag}(unflagged)
        """)).consume()
        # Only the FLAG relationship changed. Check both its endpoint and the owner one hop away.
        for name, violation, changed, bad in [(own, own, "isolated", "isolated"), (opposite, opposite + "_max_one", "b", "peer")]:
            trigger = checks[name]
            assert await (await session.run(trigger.audit)).value() == [bad]
            event = f"""MATCH (n:{label} {{id:$changed}})-[r:FLAG]-(:Marker)
                RETURN [] AS createdNodes, [r] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes"""
            with pytest.raises(ClientError, match=f"{violation}.*{bad}"):
                await (await session.run(_query_with_metadata(event), changed=changed, trigger_query=trigger.query)).consume()

        # Removing the far label deactivates all flags. The peer now has zero qualifying links.
        await (await session.run("MATCH (m:Marker) SET m:FormerMarker REMOVE m:Marker")).consume()
        assert await (await session.run(checks[own].audit)).value() == []
        assert await (await session.run(checks[opposite].audit)).value() == ["peer"]
        event = "MATCH (m:FormerMarker) RETURN [m] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes"
        query = _query_with_metadata(event, createdNodes="[]", removedLabels="{Marker: createdNodes}")
        with pytest.raises(ClientError, match=f"{opposite}.*peer"):
            await (await session.run(query, trigger_query=checks[opposite].query)).consume()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("match_list", [False, True], ids=["equality", "membership"])
async def test_edge_when_applies_all_matching_rules_to_edits(test_database, match_list):
    kind = 'a "quoted" value'
    other_kind = "another value" if match_list else kind
    kind_match = [kind, other_kind] if match_list else kind
    rules = [
        {"target": "Owner", "when": {"properties": {"active": False}}, "cardinality": "0..1 -> 0..*"},
        {"target": "Owner", "when": {"properties": {"kind": kind_match, "active": False}},
         "cardinality": "0..* -> 0..1"},
    ]
    async with test_database.get_session() as session:
        await (await session.run("""
            CREATE (s:Sample {id:'s'}), (t:Owner {id:'t'}), (s)-[:REFERS {active:false, kind:$kind}]->(t),
                   (:Sample)-[:REFERS {active:false}]->(t), (s)-[:REFERS {active:false, kind:$other_kind}]->(:Owner),
                   (s)-[:REFERS {active:false}]->(:Owner), (s)-[:REFERS {active:false, kind:'unlisted'}]->(:Owner),
                   (s)-[:REFERS {active:false, kind:$kind}]->(:Other), (s)-[:REFERS {active:true, kind:$kind}]->(t)
        """, kind=kind, other_kind=other_kind)).consume()
        event = """MATCH (:Sample {id:'s'})-[r:REFERS {active:false}]->(:Owner {id:'t'})
            RETURN [] AS createdNodes, [r] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes"""
        for order in (rules, reversed(rules)):
            checks = _when_triggers(*order)
            assert len(checks) == 2
            for name, trigger in checks.items():
                bad = "t" if "_per_owner" in name else "s"
                assert await (await session.run(trigger.audit)).value() == [bad]
                query = _query_with_metadata(event, createdRelationships="[]",
                                             assignedRelationshipProperties="{active:[{relationship:createdRelationships[0]}]}")
                with pytest.raises(ClientError, match=f"{name}.*{bad}"):
                    await (await session.run(query, trigger_query=trigger.query)).consume()
        await (await session.run("""
            MATCH (:Sample {id:'s'})-[r:REFERS {active:false}]->(:Owner {id:'t'}) REMOVE r.active
        """)).consume()
        for trigger in _when_triggers(*rules).values():
            assert await (await session.run(trigger.audit)).value() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_installed_original_when_rechecks_property_edits(client, test_database):
    root = await client.post("/v2/texts", json={"title": {"en": "Original"}, "language": "en", "category_id": "category"})
    assert root.status_code == 201
    translation = await client.post("/v2/texts", json={
        "title": {"bo": "Translation"}, "language": "bo", "translation_of": root.json()["id"],
    })
    assert translation.status_code == 201
    async with test_database.get_session() as session:
        with pytest.raises(ClientError, match="enforce_text_has_text_of_per_work_when_1_max_one"):
            await (await session.run("MATCH ()-[r:TEXT_OF {original:false}]->() SET r.original = true")).consume()
        # Swapping which text is original in a single commit keeps the work valid.
        await (await session.run("MATCH ()-[r:TEXT_OF]->() SET r.original = NOT r.original")).consume()


@pytest.mark.parametrize(("second", "message"), [
    ({"target": ["Other", "Owner"], "cardinality": "0..* -> 1"}, "duplicate target Owner"),
    ({"target": "Other", "cardinality": "0..* -> 0..1"}, "number of targets per source"),
    ({"target": "Other", "cardinality": "0..* -> 1", "allow_self": True}, "properties and allow_self"),
    ({"target": "Other", "cardinality": "0..* -> 1", "properties": {"index": {"type": "integer"}}},
     "properties and allow_self"),
])
def test_inconsistent_relationship_rules(second, message):
    rules = [{"target": "Owner", "cardinality": "1 -> 1"}, second]
    with pytest.raises(ValueError, match=message):
        tuple(relationships({"Sample": {"relationships": {"REFERS": rules}}, "Owner": {}, "Other": {}}))


@pytest.mark.parametrize("rules", [[], {"target": "Sample", "cardinality": "1 -> 1"}])
def test_relationship_requires_a_nonempty_list_of_rules(rules):
    with pytest.raises(ValueError, match=r"Sample\.REFERS\b"):
        tuple(relationships({"Sample": {"relationships": {"REFERS": rules}}}))


def test_duplicate_relationship_key_is_rejected_before_yaml_can_overwrite_rules():
    with pytest.raises(DuplicateKeyError, match='duplicate key "SPAN_OF"'):
        _load_schema("""
            relationships:
              SPAN_OF: [{target: Cue, cardinality: "1 -> 1"}]
              SPAN_OF: [{target: Page, cardinality: "1..* -> 1"}]
        """)


def test_schema_allows_a_merge_override_but_rejects_two_explicit_keys():
    template = "defaults: &defaults {required: true}\nfield: {<<: *defaults, required: false"
    assert _load_schema(template + "}")["field"] == {"required": False}
    with pytest.raises(DuplicateKeyError, match='duplicate key "required"'):
        _load_schema(template + ", required: true}")
    with pytest.raises(DuplicateKeyError, match='duplicate key "required"'):
        _load_schema("field: {<<: {required: false, required: true}}")


def test_schema_rejects_unknown_yaml_tags():
    with pytest.raises(ConstructorError, match="!unknown"):
        _load_schema("field: !unknown value")


def test_property_indexes_accept_yaml_aliases_and_skip_constraint_backed_indexes():
    nodes = _load_schema("""
        Sample:
          properties:
            id: {unique: &enabled true, indexed: *enabled}
            reference: {indexed: *enabled}
            description: {indexed: false}
    """)
    unique, indexes = _property_schema(nodes)
    assert unique == {"sample_id_unique": ("Sample", "id")}
    assert indexes == {"sample_reference": ("Sample", "reference")}


def test_boolean_aliases_keep_their_type_in_rules_and_predicates():
    nodes = _load_schema("""
        Sample:
          relationships:
            LINK:
              - target: Sample
                allow_self: &enabled true
                cardinality: "0..* -> 0..*"
                properties: {active: {type: boolean}}
              - target: Sample
                when: {properties: {active: *enabled}}
                cardinality: 0
    """)
    rule, = relationships(nodes)
    assert rule.allow_self is True and rule.conditions[0].when["properties"]["active"] is True


def test_index_cannot_shadow_a_constraint_name():
    nodes = {"Sample": {"properties": {"id": {"unique": True}, "id_unique": {"indexed": True}}}}
    with pytest.raises(ValueError, match="Duplicate index or constraint name: sample_id_unique"):
        _property_schema(nodes)


@pytest.mark.parametrize("flag", ["unique", "indexed"])
def test_index_flags_require_booleans(flag):
    with pytest.raises(TypeError, match=rf"Sample\.name: {flag} must be a boolean"):
        _property_schema({"Sample": {"properties": {"name": {flag: "false"}}}})


@pytest.mark.parametrize(("option", "value", "error"), [
    ("required", "false", TypeError), ("unique", True, ValueError),
])
def test_invalid_edge_property_flags(option, value, error):
    nodes = {"Sample": {"relationships": {"LINK": [{
        "target": "Owner", "cardinality": "1 -> 1", "properties": {"flag": {"type": "string", option: value}},
    }]}}, "Owner": {}}
    with pytest.raises(error, match=option):
        list(_relationship_triggers(tuple(relationships(nodes)), nodes=nodes))


def test_relationship_must_be_declared_on_only_one_side():
    nodes = {a: {"relationships": {"REFERS": [{"target": b, "cardinality": "1 -> 1"}]}}
             for a, b in [("Sample", "Owner"), ("Owner", "Sample")]}
    with pytest.raises(ValueError, match="reverse declaration"):
        tuple(relationships(nodes))


@pytest.mark.asyncio(loop_scope="session")
async def test_cardinality_rechecks_neighbors_when_peer_label_changes(test_database):
    # Both labels can create this edge, but only one counts toward the Owner's required link.
    nodes = {"Counted": {"relationships": {"REFERS": [{"target": "Owner", "cardinality": "1 -> 0..*"}]}},
             "Other": {"relationships": {"REFERS": [{"target": "Owner", "cardinality": "0..* -> 0..*"}]}},
             "Owner": {}}
    rules = {r.name: r for r in _relationship_triggers(tuple(relationships(nodes)))}
    changes = {"createdNodes": [], "deletedNodes": [], "createdRelationships": [], "deletedRelationships": [],
               "assignedLabels": {}, "removedLabels": {}}
    async with test_database.get_session() as session:
        await (await session.run("CREATE (:Counted:Other {id:'a'})-[:REFERS]->(o:Owner)<-[:REFERS]-(:Other {id:'b'})")).consume()
        for peer, edit, event, suffix in [
            ("b", "SET p:Counted", "assignedLabels", "_max_one"),
            ("a", "REMOVE p:Counted", "removedLabels", ""),
        ]:
            rule = rules["enforce_counted_refers_per_owner"]
            with pytest.raises(ClientError, match=rule.name + suffix):
                await (await session.run(f"""
                    MATCH (p:Other {{id:$peer}}) {edit}
                    WITH p, $changes AS changes
                    CALL apoc.cypher.doIt($statement, changes {{.*, {event}: {{Counted:[p]}}}})
                    YIELD value RETURN value
                """, peer=peer, statement=rule.query, changes=changes)).consume()


def _creation(pattern: str, *, nodes: str = "[]", edges: str = "[]") -> str:
    return f"""{pattern}
        RETURN {nodes} AS createdNodes, {edges} AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
    """


def _created_node_case(label: str, properties: str = "id: randomUUID()") -> str:
    return _creation(f"CREATE (n:{label} {{{properties}}})", nodes="[n]")


def _required_max_one_case(label: str, rel: str, peer: str, *, reverse: bool = False) -> str:
    edge = f"<-[r:{rel}]-" if reverse else f"-[r:{rel}]->"
    return _creation(f"""
        CREATE (owner:{label})
        WITH owner UNWIND [1, 2] AS copy CREATE (owner){edge}(:{peer})
        WITH owner, collect(r) AS edges
    """, nodes="[owner]", edges="edges")


def _edge_case(source: str, rel: str, target: str, props: str = "") -> str:
    return _creation(f"CREATE (:{source})-[r:{rel} {{{props}}}]->(:{target})", edges="[r]")


def _excerpt_edition_case(change: str, *, source: bool = True, spans: bool = True) -> str:
    excerpt_of = "CREATE (edition)-[:EXCERPT_OF]->(stored)" if source else ""
    span = "CREATE (:Span {start: 0, end: 1, index: 0})-[:SPAN_OF]->(edition)" if spans else ""
    return _creation(f"""
    MERGE (etype:EditionType {{name: 'excerpt'}})
    MERGE (critical:EditionType {{name: 'critical'}})
    CREATE (stored:Edition {{id: randomUUID(), content_key: 'objects/stored'}})-[:HAS_TYPE]->(critical)
    CREATE (edition:Edition {{id: randomUUID()}})-[:HAS_TYPE]->(etype)
    {excerpt_of}
    {span}
    {change}
    WITH edition, stored
    MATCH (edition)-[edge]-(neighbor)
    WITH edition, stored, collect(DISTINCT neighbor) AS neighbors, collect(DISTINCT edge) AS edges
    """, nodes="[edition, stored] + neighbors", edges="edges")


EXCERPT_EDITION_CASES = {
    "enforce_edition_excerpt_of_when_1": _excerpt_edition_case("", source=False),
    "enforce_span_span_of_per_edition_when_1": _excerpt_edition_case("", spans=False),
    "enforce_edition_excerpt_of_when_2_max_zero": _excerpt_edition_case("""
        CREATE (other:Edition {id: randomUUID()})-[:HAS_TYPE]->(etype), (other)-[:EXCERPT_OF]->(edition),
               (:Span {start: 0, end: 1, index: 0})-[:SPAN_OF]->(other)
    """),
    "enforce_edition_has_type_when_1": _excerpt_edition_case("""
        CREATE (stored)-[:EXCERPT_OF]->(:Edition {id: randomUUID(), content_key: 'objects/other'})-[:HAS_TYPE]->(critical)
    """),
    "enforce_edition_has_type_when_2": _excerpt_edition_case("CREATE (:Span {start: 0, end: 1})-[:SPAN_OF]->(stored)"),
    "enforce_edition_has_segmentation_when_1_max_zero": _excerpt_edition_case(
        "CREATE (edition)-[:HAS_SEGMENTATION]->(:Segmentation {id: randomUUID()})"
    ),
}
for trigger_name, label, rel in [
    ("enforce_note_note_of_when_1_max_zero", "Note", "NOTE_OF"),
    ("enforce_mark_mark_of_when_1_max_zero", "Mark", "MARK_OF"),
    ("enforce_attribute_attribute_of_when_1_max_zero", "Attribute", "ATTRIBUTE_OF"),
    ("enforce_bibmeta_bibliography_of_when_1_max_zero", "BibliographicMetadata", "BIBLIOGRAPHY_OF"),
    ("enforce_pagination_pagination_of_when_2_max_zero", "Pagination", "PAGINATION_OF"),
    ("enforce_toc_edition_when_1_max_zero", "TableOfContents", "TOC_OF"),
]:
    EXCERPT_EDITION_CASES[trigger_name] = _excerpt_edition_case(
        f"CREATE (:{label} {{id: randomUUID()}})-[:{rel}]->(edition)"
    )


def _catalog_integrity_cases() -> dict[str, str]:
    """Minimal violating graphs for the conditional rules. Base degree checks are generated below."""
    cases = {
        "enforce_edition_edition_of_per_text_when_1_max_one": _creation("""
            MERGE (kind:EditionType {name:'critical'})
            CREATE (owner:Text), (a:Edition)-[:EDITION_OF]->(owner)<-[:EDITION_OF]-(b:Edition),
                   (a)-[:HAS_TYPE]->(kind)<-[:HAS_TYPE]-(b)
        """, nodes="[owner]"),
        "enforce_text_has_contribution_when_1_max_zero": _creation("""
            CREATE (owner:Text)-[:HAS_CONTRIBUTION]->(:Contribution)<-[:HAS_CONTRIBUTION]-(a:Audio),
                   (a)-[:RECITATION_OF]->(owner)
        """, nodes="[owner]"),
        "enforce_contribution_with_role_when_1": _creation("""
            CREATE (a:Audio)-[:RECITATION_OF]->(:Text),
                   (a)-[:HAS_CONTRIBUTION]->(owner:Contribution)-[:WITH_ROLE]->(:RoleType {name:'future-role'})
        """, nodes="[owner]"),
    }
    for edge, other, index in [
        ("TRANSLATION_OF", "COMMENTARY_OF", 1), ("PART_OF", "TRANSLATION_OF", 1), ("PART_OF", "COMMENTARY_OF", 2),
    ]:
        cases[f"enforce_text_{edge.lower()}_when_{index}_max_zero"] = _creation(
            f"CREATE (owner:Text)-[:{edge}]->(:Text), (owner)-[:{other}]->(:Text)", nodes="[owner]"
        )
    for index, edge in [(2, "PART_OF"), (3, "COMMENTARY_OF")]:
        cases[f"enforce_text_has_text_of_when_{index}"] = _creation(
            f"CREATE (t:Text)-[:TEXT_OF {{original: false}}]->(:Work), (t)-[r:{edge}]->(:Text)", edges="[r]",
        )
    for definition in RELATIONSHIPS:
        if "Nomen" in definition.per_target and definition.type != "ALTERNATIVE_OF":
            label, rel = definition.source, definition.type
            base = _RELATIONSHIP_TRIGGER_NAMES.get((label, rel), f"enforce_{label.lower()}_{rel.lower()}")
            cases[f"{base}_when_1_max_zero"] = _creation(
                f"CREATE (owner:{label})-[:{rel}]->(:Nomen)-[:ALTERNATIVE_OF]->(:Nomen)", nodes="[owner]"
            )
    return cases


def _build_trigger_cases() -> dict[str, str]:
    cases = {
        **EXCERPT_EDITION_CASES,
        **_catalog_integrity_cases(),
        "enforce_toc_hierarchy": _creation("""
            CREATE (a:TableOfContentsSection)-[:SUBSECTION_OF]->(b:TableOfContentsSection)-[:SUBSECTION_OF]->(a)
        """, nodes="[a,b]"),
        "enforce_pagination_pagination_of_per_edition_when_1": _creation("""
            MERGE (etype:EditionType {name:'diplomatic'}) CREATE (e:Edition)-[:HAS_TYPE]->(etype)
        """, nodes="[e]"),
        "enforce_edition_excerpt_of_max_one": _excerpt_edition_case(
            "CREATE (edition)-[:EXCERPT_OF]->(:Edition {content_key:'objects/other'})"
        ),
        # Count distinct target labels together, including owners with different kinds.
        "enforce_span_span_of_max_one": _creation("""
            CREATE (:Segment)<-[a:SPAN_OF]-(span:Span)-[b:SPAN_OF]->(:Page)
        """, nodes="[span]", edges="[a,b]"),
        "enforce_contribution_by_max_one": _creation("""
            CREATE (:Person)<-[a:BY]-(c:Contribution)-[b:BY]->(:AI)
        """, nodes="[c]", edges="[a,b]"),
        "enforce_recording_follows_recited_text": _creation("""
            CREATE (r:Recording)-[:RENDITION_OF]->(:Audio)-[:RECITATION_OF]->(:Text),
                   (r)-[:FOLLOWS]->(:Edition)-[:EDITION_OF]->(:Text)
        """, nodes="[r]"),
        "enforce_nomen_alternative_of_when_1_max_zero": _creation("""
            CREATE (:Nomen)-[a:ALTERNATIVE_OF]->(:Nomen)-[b:ALTERNATIVE_OF]->(:Nomen)
        """, edges="[a,b]"),
        "enforce_text_has_text_of_per_work_when_1_max_one": _creation("""
            CREATE (:Text)-[a:TEXT_OF {original:true}]->(:Work)<-[b:TEXT_OF {original:true}]-(:Text)
        """, edges="[a,b]"),
        "enforce_text_title_unique": _creation("""
            CREATE (lang:Language {code:randomUUID()})
            WITH lang UNWIND [1, 2] AS copy
            CREATE (t:Text)-[:HAS_LANGUAGE]->(lang),
                   (t)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText {text:'Duplicate title'}),
                   (lt)-[tag:HAS_LANGUAGE {bcp47:'bo'}]->(lang)
            WITH last(collect(lt)) AS title, last(collect(tag)) AS tag
        """, nodes="[title]", edges="[tag]"),
    }
    for label, node in NODES.items():
        for prop, spec in node.get("properties", {}).items():
            name = f"enforce_{to_snake(label)}_{prop}_property"
            if spec["type"] != "any" or spec.get("required"):
                sample = "" if spec["type"] == "any" else f"{prop}: ['wrong type']"
                cases[name] = _created_node_case(label, sample)
    for definition in RELATIONSHIPS:
        label, rel = definition.source, definition.type
        base = _RELATIONSHIP_TRIGGER_NAMES.get((label, rel), f"enforce_{label.lower()}_{rel.lower()}")
        target = next(iter(definition.per_target))
        if definition.per_source in {"1", "1..*"}:
            cases.setdefault(base, _created_node_case(label))
        if definition.per_source in {"1", "0..1"}:
            cases.setdefault(f"{base}_max_one", _required_max_one_case(label, rel, target))
        for owner, bound in definition.per_target.items():
            reverse = f"{base}_per_{to_snake(owner)}"
            if bound in {"1", "1..*"}:
                cases[reverse] = _created_node_case(owner)
            if bound in {"1", "0..1"}:
                cases[f"{reverse}_max_one"] = _required_max_one_case(owner, rel, label, reverse=True)
        if not definition.allow_self:
            cases[f"enforce_no_self_{rel.lower()}"] = _creation(f"CREATE (n:{label})-[r:{rel}]->(n)", edges="[r]")
        cases[_ENDPOINT_TRIGGER_NAMES.get(rel, f"enforce_{rel.lower()}_target")] = _edge_case(label, rel, "WrongTarget")
        for prop, spec in definition.properties.items():
            if spec["type"] != "any":
                cases[f"{base}_{prop}_property"] = _edge_case(label, rel, target, f"{prop}: ['wrong type']")
            elif spec.get("required"):
                cases[f"{base}_{prop}_property"] = _edge_case(label, rel, target)
    assert set(cases) == set(TRIGGER_QUERY_BY_NAME)
    return cases


TRIGGER_QUERY_BY_NAME = {
    name: trigger.query for trigger in TRIGGERS for name in trigger.rules
}
TRIGGER_CASES = _build_trigger_cases()


@pytest.mark.asyncio(loop_scope="session")
async def test_nomen_alternatives_share_a_target_but_cannot_extend_either_end(test_database):
    rule = next(rule for rule in TRIGGERS if rule.name == "enforce_nomen_alternative_of_when_1_max_zero")
    async with test_database.get_session() as session:
        await (await session.run("""
            MATCH (lang:Language {code:'en'})
            UNWIND ['first', 'second', 'main', 'other'] AS id
            CREATE (:Nomen {id:id})-[:HAS_LOCALIZATION]->(:LocalizedText {text:id})-[:HAS_LANGUAGE]->(lang)
        """)).consume()
        await (await session.run("""
            MATCH (alt:Nomen), (main:Nomen {id:'main'}) WHERE alt.id IN ['first', 'second']
            CREATE (alt)-[:ALTERNATIVE_OF]->(main)
        """)).consume()
        assert await (await session.run(rule.audit)).value() == []
        for source, target, middle in [('main', 'other', 'main'), ('other', 'first', 'first')]:
            async with await session.begin_transaction() as tx:
                await (await tx.run("""
                    MATCH (s:Nomen {id:$source}), (t:Nomen {id:$target})
                    CREATE (s)-[:ALTERNATIVE_OF]->(t)
                """, source=source, target=target)).consume()
                assert await (await tx.run(rule.audit)).value() == [middle]
                with pytest.raises(ClientError, match=rf"{rule.name}.*IDs: {middle}"):
                    await tx.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_lookup_name_remains_a_required_string_on_edits_and_relabeling(test_database):
    async with test_database.get_session() as session:
        await (await session.run("CREATE (:EditionType {name: 'critical'}), (:Candidate {name: 42})")).consume()
        for query in (
            "MATCH (n:EditionType {name: 'critical'}) SET n.name = 42",
            "MATCH (n:EditionType {name: 'critical'}) REMOVE n.name",
            "MATCH (n:Candidate) SET n:EditionType",
        ):
            with pytest.raises(ClientError, match="enforce_edition_type_name_property"):
                await (await session.run(query)).consume()
        result = await session.run("MATCH (n:EditionType {name: 'critical'}) SET n.name = 'unlisted' RETURN n.name")
        assert await result.value() == ["unlisted"]


@pytest.mark.asyncio(loop_scope="session")
async def test_grouped_properties_report_each_field_and_skip_nodes_losing_the_label(test_database):
    async with test_database.get_session() as session:
        result = await session.run("CREATE (a:Application {id:'grouped', name:'Test'}) RETURN elementId(a)")
        element_id, = await result.value()
        with pytest.raises(ClientError) as error:
            await (await session.run("MATCH (a:Application {id:'grouped'}) REMOVE a.id, a.name")).consume()
        for field in ("id", "name"):
            assert f"enforce_application_{field}_property: Invalid Application.{field} IDs: {element_id}" in str(error.value)
        result = await session.run("""
            MATCH (a:Application {id:'grouped', name:'Test'}) REMOVE a:Application, a.name RETURN a.id
        """)
        assert await result.value() == ["grouped"]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("name", TRIGGER_CASES)
async def test_each_trigger_rejects_intentional_invalid_data(test_database, name: str) -> None:
    query = _query_with_metadata(TRIGGER_CASES[name])
    async with test_database.get_session() as session:
        with pytest.raises(ClientError, match=name):
            result = await session.run(query, trigger_query=TRIGGER_QUERY_BY_NAME[name])
            await result.consume()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("name", EXCERPT_EDITION_CASES)
async def test_excerpt_rule_audits_its_invalid_shape(test_database, name):
    rule = next(t for t in TRIGGERS if t.name == name)
    async with test_database.get_session() as session, await session.begin_transaction() as tx:
        await (await tx.run(EXCERPT_EDITION_CASES[name])).consume()
        assert len(await (await tx.run(rule.audit)).value("violating_id")) == 1
        await tx.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_install_triggers_replaces_stale_triggers(test_database) -> None:
    driver, database = test_database._driver, test_database._database
    async with driver.session(database=SYSTEM_DATABASE_NAME) as session:
        for old_name in ("enforce_work_has_category_max_one", "enforce_application_name_property"):
            result = await session.run(
                "CALL apoc.trigger.install($database, $name, 'RETURN 1', {phase: 'before'})",
                database=database, name=old_name,
            )
            await result.consume()

    try:
        with pytest.raises(TimeoutError, match="enforce_application_name_property"):
            await wait_for_triggers(driver, database, wait_seconds=0.3, interval=0.1)
    finally:
        await install_triggers(driver, database)
    await asyncio.sleep(NEO4J_TRIGGER_REFRESH_SECONDS)

    async with driver.session(database=SYSTEM_DATABASE_NAME) as session:
        result = await session.run("CALL apoc.trigger.show($database) YIELD name, query, selector, paused", database=database)
        stored = {record["name"]: record async for record in result}

    assert set(stored) == {trigger.name for trigger in TRIGGERS}
    assert all(rule["query"].startswith("CYPHER 25 ") for rule in stored.values())
    assert all(rule["selector"]["phase"] == "before" and not rule["paused"] for rule in stored.values())

