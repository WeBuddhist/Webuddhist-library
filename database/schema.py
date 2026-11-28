"""Load the graph schema, resolve relationship cardinalities and build Neo4j constraints and indexes."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, FiniteFloat, TypeAdapter
from pydantic.alias_generators import to_snake
from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor
from typing_extensions import TypedDict


def _load_schema(source: Path | str) -> dict[str, Any]:
    """Round-trip mode checks explicit keys even in mappings that also use a YAML merge."""
    parser = YAML(typ="rt")
    parser.version = (1, 1)
    # Preserve boolean aliases as Python booleans for strict validation and Cypher literals.
    constructor = parser.constructor
    constructor.yaml_constructors = constructor.yaml_constructors | {
        "tag:yaml.org,2002:bool": SafeConstructor.construct_yaml_bool,
        None: SafeConstructor.construct_undefined,
    }
    return parser.load(source)


SCHEMA = _load_schema(Path(__file__).with_name("neo4j_schema.yaml"))
NODES: dict[str, dict[str, Any]] = SCHEMA["nodes"]


type Multiplicity = Literal["0", "1", "0..1", "1..*", "0..*"]


def _parse_cardinality(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split("->"))
    if isinstance(value, int) and not isinstance(value, bool) and value == 0:
        return "0..*", "0"
    raise ValueError("expected a pair of bounds or the standalone cardinality 0")


type _NonEmpty[T] = Annotated[T, Field(min_length=1)]
type _PropertyLiteral = str | bool | int | FiniteFloat
type PropertyValue = _PropertyLiteral | _NonEmpty[list[_PropertyLiteral]]
type PropertyMatch = _NonEmpty[dict[str, PropertyValue]]
type Traversal = str | Annotated[dict[str, _NonEmpty[EdgePredicate]], Field(min_length=1, max_length=1)]


class NodePredicate(TypedDict, total=False, closed=True):
    properties: PropertyMatch
    has_incoming: Traversal
    has_outgoing: Traversal


class EdgePredicate(TypedDict, total=False, closed=True):
    properties: PropertyMatch
    source: _NonEmpty[NodePredicate]
    target: _NonEmpty[NodePredicate]


class _CardinalityRule(BaseModel, extra="forbid"):
    target: Annotated[_NonEmpty[list[str]], BeforeValidator(lambda value: [value] if isinstance(value, str) else value)]
    cardinality: Annotated[tuple[Multiplicity, Multiplicity], BeforeValidator(_parse_cardinality)]
    description: str | None = None


class ConditionalRule(_CardinalityRule):
    when: _NonEmpty[EdgePredicate]

    def validate_scope(self, name: str, per_target: dict[str, Multiplicity], properties: dict[str, Any]) -> None:
        targets = set(self.target)
        if len(targets) != len(self.target) or not targets <= per_target.keys():
            raise ValueError(f"{name}: conditional targets must be distinct targets of a base rule")
        if unknown := self.when.get("properties", {}).keys() - properties.keys():
            raise ValueError(f"{name}: undeclared edge properties {sorted(unknown)}")


class _Rule(_CardinalityRule):
    properties: dict[str, Any] = Field(default_factory=dict)
    allow_self: bool = False


_RULES = TypeAdapter(dict[str, dict[str, _NonEmpty[list[_Rule | ConditionalRule]]]])


@dataclass(frozen=True)
class Relationship:
    source: str
    type: str
    per_source: Multiplicity
    per_target: dict[str, Multiplicity]
    properties: dict[str, Any]
    allow_self: bool
    conditions: tuple[ConditionalRule, ...] = ()
    descriptions: dict[str, str | None] = field(default_factory=dict)


def relationships(nodes: dict[str, dict[str, Any]]) -> Iterator[Relationship]:
    """Resolve both ends of each declaration, including a shared bound across alternative targets."""
    seen: set[tuple[str, str, str]] = set()
    declarations = {source: node.get("relationships", {}) for source, node in nodes.items()}
    for source, entries in _RULES.validate_python(declarations, strict=True).items():
        for rel, entries_for_rel in entries.items():
            name = f"{source}.{rel}"
            rules = [r for r in entries_for_rel if isinstance(r, _Rule)]
            if not rules:
                raise ValueError(f"{name}: a conditional rule needs a base rule")
            first = rules[0]
            per_source = first.cardinality[1]
            per_target = {}
            descriptions = {}
            for rule in rules:
                left, right = rule.cardinality
                if right != per_source:
                    raise ValueError(f"{name}: rules must agree on the total number of targets per source")
                if rule.properties != first.properties or rule.allow_self != first.allow_self:
                    raise ValueError(f"{name}: rules must agree on properties and allow_self")
                for target in rule.target:
                    if target not in nodes:
                        raise ValueError(f"{name}: unknown target {target}")
                    if target in per_target:
                        raise ValueError(f"{name}: duplicate target {target}")
                    if (target, rel, source) in seen:
                        raise ValueError(f"{name}: reverse declaration already exists on {target}.{rel}")
                    per_target[target] = left
                    descriptions[target] = rule.description
                    seen.add((source, rel, target))
            conditions = tuple(r for r in entries_for_rel if isinstance(r, ConditionalRule))
            for condition in conditions:
                condition.validate_scope(name, per_target, first.properties)
            yield Relationship(
                source,
                rel,
                per_source,
                per_target,
                first.properties,
                first.allow_self,
                conditions,
                descriptions=descriptions,
            )


RELATIONSHIPS = tuple(relationships(NODES))


def property_flag(field: str, spec: dict[str, Any], flag: str) -> bool:
    value = spec.get(flag, False)
    if not isinstance(value, bool):
        raise TypeError(f"{field}: {flag} must be a boolean")
    return value


def _property_schema(nodes: dict[str, dict[str, Any]]) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    unique, indexes = {}, {}
    for label, node in nodes.items():
        for prop, spec in node.get("properties", {}).items():
            for flag, suffix, group in (("unique", "_unique", unique), ("indexed", "", indexes)):
                if property_flag(f"{label}.{prop}", spec, flag) and (flag == "unique" or not spec.get("unique")):
                    name = f"{to_snake(label)}_{prop}{suffix}"
                    if name in unique or name in indexes:
                        raise ValueError(f"Duplicate index or constraint name: {name}")
                    group[name] = (label, prop)
    return unique, indexes


UNIQUE_PROPERTIES, PROPERTY_INDEXES = _property_schema(NODES)


def schema_statements() -> list[str]:
    return [
        f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        for name, (label, prop) in UNIQUE_PROPERTIES.items()
    ] + [
        f"CREATE RANGE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        for name, (label, prop) in PROPERTY_INDEXES.items()
    ]
