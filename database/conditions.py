"""Compile a cardinality rule into its Cypher pattern and the events that can change its count."""

import json
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from database.schema import ConditionalRule, EdgePredicate, NodePredicate, PropertyValue, Relationship

_WRITTEN_EDGES = """$createdRelationships
    + coll.flatten([key IN keys($assignedRelationshipProperties) |
        [entry IN $assignedRelationshipProperties[key] | entry.relationship]])
    + coll.flatten([key IN keys($removedRelationshipProperties) |
        [entry IN $removedRelationshipProperties[key] | entry.relationship]])"""


def _relabeled_edges(labels: str = "keys($assignedLabels) + keys($removedLabels)", pattern: str = "-[r]-") -> str:
    """Only existing nodes can have surviving pre-transaction edges to recheck on relabel."""
    return f"""coll.flatten([n IN apoc.coll.subtract(coll.flatten([label IN {labels} |
        coalesce($assignedLabels[label], []) + coalesce($removedLabels[label], [])]), $createdNodes + $deletedNodes)
        | [(){pattern}(n) | r]])"""


CHANGED_RELATIONSHIPS = f"apoc.coll.subtract({_WRITTEN_EDGES}, $deletedRelationships)"
_AFFECTED_EDGES = f"{_WRITTEN_EDGES} + {_relabeled_edges()}"
ENDPOINT_RELATIONSHIPS = f"apoc.coll.subtract({_AFFECTED_EDGES}, $deletedRelationships)"


def _properties(variable: str, values: dict[str, PropertyValue], declared: set[str]) -> list[str]:
    if unknown := values.keys() - declared:
        raise ValueError(f"Undeclared properties on {variable}: {sorted(unknown)}")
    clauses = []
    for key, value in values.items():
        operator = "IN" if isinstance(value, list) else "="
        clauses.append(f"{variable}[{json.dumps(key)}] {operator} {json.dumps(value, ensure_ascii=False)}")
    return clauses


@dataclass(frozen=True)
class _Hop:
    rel: str
    outgoing: bool

    def orient[T](self, source: T, target: T) -> tuple[T, T]:
        """Return the near and far values in traversal order."""
        return (source, target) if self.outgoing else (target, source)

    def pattern(self, edge: str = "") -> str:
        return f"-[{edge}:{self.rel}]->" if self.outgoing else f"<-[{edge}:{self.rel}]-"

    def endpoints(self, *, far: bool = False) -> str:
        owner, peer = self.orient("startNode", "endNode")
        return f"[r IN edges WHERE type(r) = {json.dumps(self.rel)} | {peer if far else owner}(r)]"


@dataclass
class _Watch:
    """Local transaction events and the neighbors whose predicates affect this node."""

    labels: set[str]
    changes: set[str] = field(default_factory=set)
    children: list[tuple[_Hop, _Watch]] = field(default_factory=list)

    def candidates(self, statements: list[str], parent: _Hop | None = None) -> str:
        changes = sorted(self.changes)
        for hop, child in self.children:
            peers = child.candidates(statements, hop)
            changes.append(f"coll.flatten([p IN {peers} | [(n){hop.pattern()}(p) | n]])")

        name = f"affected{len(statements)}"
        labels = "|".join(sorted(self.labels))
        # Lock dependencies before walking back to their parents. A transaction attaching
        # a new parent must take the same child lock, even if its predicate did not change.
        locks = f"apoc.coll.subtract({name} + {parent.endpoints(far=True)}, $deletedNodes)" if parent else name
        statements.append(f"""
            WITH *, [n IN apoc.coll.subtract({" + ".join(changes) or "[]"}, $deletedNodes) WHERE (n:{labels})] AS {name}
            CALL apoc.lock.nodes(COLLECT {{
                UNWIND {locks} AS n WITH n WHERE n:{labels} RETURN n ORDER BY elementId(n)
            }})""")
        return name


@dataclass
class ConditionCompiler:
    definitions: tuple[Relationship, ...]
    nodes: dict[str, dict[str, Any]]

    def _node(
        self,
        predicate: NodePredicate,
        var: str,
        watch: _Watch,
        labels: set[str],
        variables: count[int],
    ) -> list[str]:
        values = predicate.get("properties", {})
        declared = {key for label in labels for key in self.nodes.get(label, {}).get("properties", {})}
        terms = _properties(var, values, declared)
        watch.changes.update(
            f"[entry IN coalesce(${event}[{json.dumps(key)}], []) | entry.node]"
            for key in values
            for event in ("assignedNodeProperties", "removedNodeProperties")
        )
        for outgoing, traversal in ((False, predicate.get("has_incoming")), (True, predicate.get("has_outgoing"))):
            if traversal is None:
                continue
            nested: EdgePredicate = {}
            if isinstance(traversal, str):
                rel = traversal
            else:
                ((rel, nested),) = traversal.items()
            hop = _Hop(rel, outgoing)
            watch.changes.add(hop.endpoints())
            pairs = [
                (definition.source, target, definition.properties)
                for definition in self.definitions
                if definition.type == rel
                for target in definition.per_target
                if (definition.source if outgoing else target) in labels
            ]
            if not pairs:
                raise ValueError(f"No declared {rel} in this direction on {sorted(labels)}")
            index = next(variables)
            edge, peer = f"edge{index}", f"neighbor{index}"
            src, dst = hop.orient(var, peer)
            allowed = " OR ".join(f"({src}:{s} AND {dst}:{t})" for s, t, _ in pairs)
            fields = {key for _, _, props in pairs for key in props}
            filters = [f"({allowed})", *_properties(edge, nested.get("properties", {}), fields)]
            near, far = hop.orient(nested.get("source"), nested.get("target"))
            near_labels, far_labels = hop.orient({s for s, _, _ in pairs}, {t for _, t, _ in pairs})
            if near:
                filters.extend(self._node(near, var, watch, near_labels, variables))
            if far:
                child = _Watch(far_labels)
                watch.children.append((hop, child))
                filters.extend(self._node(far, peer, child, far_labels, variables))
            terms.append(f"EXISTS {{ ({var}){hop.pattern(edge)}({peer}) WHERE {' AND '.join(filters)} }}")
        return terms

    def degree(
        self,
        definition: Relationship,
        rule: ConditionalRule | None,
        label: str,
        peers: list[str],
        *,
        reverse: bool,
    ) -> tuple[str, str, list[str]]:
        when: EdgePredicate = rule.when if rule else {}
        hop = _Hop(definition.type, outgoing=not reverse)
        assigned = f"coalesce($assignedLabels[{json.dumps(label)}], [])"
        owner = _Watch({label}, {"$createdNodes", assigned, hop.endpoints()})
        own, other = hop.orient(when.get("source"), when.get("target"))
        variables = count()
        scope = set(rule.target) if reverse and rule else owner.labels
        guards = self._node(own, "node", owner, scope, variables) if own else []
        filters = _properties("r", when.get("properties", {}), set(definition.properties))
        if other:
            peer = _Watch(set(peers))
            owner.children.append((hop, peer))
            filters.extend(self._node(other, "peer", peer, peer.labels, variables))

        pattern = f"(node){hop.pattern('r')}(peer:{'|'.join(peers)})"
        if filters:
            pattern += " WHERE " + " AND ".join(filters)
        # Plain counts only depend on creation, deletion, or relabeling one of the allowed peers.
        edges = _AFFECTED_EDGES
        if not rule:
            edges = f"$createdRelationships + {_relabeled_edges(json.dumps(peers), hop.pattern('r'))}"
        statements = [f"WITH {edges} + $deletedRelationships AS edges"]
        owners = owner.candidates(statements)
        return "".join(statements) + f" UNWIND {owners} AS node WITH node", pattern, guards
