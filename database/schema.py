"""The graph schema declared in neo4j_schema.yaml, and the unique constraints it implies.

Lookup indexes are listed here explicitly; they are not derived from the YAML.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic.alias_generators import to_snake

NODES: dict[str, dict[str, Any]] = yaml.safe_load(Path(__file__).with_name("neo4j_schema.yaml").read_text())["nodes"]

# Non-unique property indexes kept for lookup performance (names must match deployed DBs).
INDEX_STATEMENTS: list[str] = [
    "CREATE INDEX source_name_index IF NOT EXISTS FOR (n:Source) ON (n.name)",
    "CREATE INDEX localized_text_text IF NOT EXISTS FOR (n:LocalizedText) ON (n.text)",
]


def schema_statements() -> list[str]:
    """Unique constraints plus the explicit lookup indexes, in install order."""
    statements = []
    for label, node in NODES.items():
        prefix = to_snake(label)
        for prop, spec in node.get("properties", {}).items():
            if spec.get("unique"):
                name = f"{prefix}_{prop}_unique"
                statements.append(f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE")
    return [*statements, *INDEX_STATEMENTS]
