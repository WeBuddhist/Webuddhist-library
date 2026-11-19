# ruff: noqa: T201, INP001 - a standalone script shipped over SSM, not a package module
"""Keep the target environment's own API keys across a sync from prod.

A sync replaces the whole target database with a copy of prod, API keys included. Without this,
every sync would delete the keys made for testing and make prod's keys valid on testing.

    api_keys.py save <file>     before the database is replaced: write the target's keys to <file>
    api_keys.py restore <file>  after: delete the keys that came with the prod copy, put the saved
                                ones back (except copies of prod keys from an earlier sync)

Runs with the target API's virtualenv and working directory, so it reads that environment's .env.
It is shipped with the sync scripts (not the deployed code), so it uses only config and the driver.
Only key hashes are stored, never raw keys.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from neo4j import Driver, GraphDatabase, ManagedTransaction

from config import settings

ALLOWED_ENVIRONMENTS = {"testing", "dev"}

EXPORT_QUERY = """
    MATCH (k:ApiKey)
    OPTIONAL MATCH (k)-[:BOUND_TO]->(a:Application)
    RETURN k.id AS id, k.name AS name, k.email AS email, k.api_key_hash AS api_key_hash,
           k.is_active AS is_active, toString(k.created_at) AS created_at, a.id AS bound_application_id
    ORDER BY k.id
"""

DELETE_ALL_QUERY = "MATCH (k:ApiKey) DETACH DELETE k"

CREATE_QUERY = """
    UNWIND $keys AS key
    CREATE (:ApiKey {
        id: key.id,
        name: key.name,
        email: key.email,
        api_key_hash: key.api_key_hash,
        is_active: key.is_active,
        created_at: datetime(key.created_at)
    })
"""

BIND_QUERY = """
    UNWIND $keys AS key
    MATCH (k:ApiKey {id: key.id}), (a:Application {id: key.bound_application_id})
    CREATE (k)-[:BOUND_TO]->(a)
    RETURN count(*) AS bound
"""


def plan_restore(
    saved: list[dict], prod_key_ids: set[str], application_ids: set[str]
) -> tuple[list[dict], list[str], list[str]]:
    """Split the saved keys into (restore, dropped as prod copies, skipped for a missing application).

    A saved key whose id is also in the new prod copy is a copy of a prod key from an earlier sync,
    so it is dropped. A bound key whose application is missing is skipped rather than restored
    unbound, because an unbound key is a master key.
    """
    restore, dropped, skipped = [], [], []
    for key in saved:
        application = key.get("bound_application_id")
        if key["id"] in prod_key_ids:
            dropped.append(key["id"])
        elif application is not None and application not in application_ids:
            skipped.append(key["id"])
        else:
            restore.append(key)
    return restore, dropped, skipped


def _driver() -> Driver:
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password))


def save(path: str) -> None:
    with _driver() as driver:
        records, _, _ = driver.execute_query(EXPORT_QUERY, database_=settings.neo4j_database)
    keys = [record.data() for record in records]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(keys, f)
    print(f"Saved {len(keys)} API key(s): {', '.join(k['id'] for k in keys) or '-'}")


def restore(path: str) -> None:
    saved = json.loads(Path(path).read_text())
    with _driver() as driver, driver.session(database=settings.neo4j_database) as session:

        def work(tx: ManagedTransaction) -> tuple[list[dict], list[str], list[str], list[str], int]:
            prod_ids = {r["id"] for r in tx.run("MATCH (k:ApiKey) RETURN k.id AS id")}
            application_ids = {r["id"] for r in tx.run("MATCH (a:Application) RETURN a.id AS id")}
            keys, dropped, skipped = plan_restore(saved, prod_ids, application_ids)
            tx.run(DELETE_ALL_QUERY).consume()
            tx.run(CREATE_QUERY, keys=keys).consume()
            bound_keys = [k for k in keys if k.get("bound_application_id") is not None]
            bound = tx.run(BIND_QUERY, keys=bound_keys).single(strict=True)["bound"]
            if bound != len(bound_keys):
                raise RuntimeError(f"bound {bound} of {len(bound_keys)} keys to their application")
            return keys, dropped, skipped, sorted(prod_ids), bound

        keys, dropped, skipped, prod_ids, bound = session.execute_write(work)
    print(f"Removed {len(prod_ids)} key(s) that came with the prod copy: {', '.join(prod_ids) or '-'}")
    print(f"Restored {len(keys)} key(s) ({bound} bound): {', '.join(k['id'] for k in keys) or '-'}")
    if dropped:
        print(f"Dropped {len(dropped)} saved key(s) that were copies of prod keys: {', '.join(dropped)}")
    if skipped:
        print(f"WARNING: skipped {len(skipped)} key(s) whose application is missing: {', '.join(skipped)}")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in {"save", "restore"}:
        sys.exit("usage: api_keys.py save|restore <file>")
    if settings.environment not in ALLOWED_ENVIRONMENTS:
        sys.exit(f"refusing to run with ENVIRONMENT={settings.environment!r}; only testing or dev")
    {"save": save, "restore": restore}[sys.argv[1]](sys.argv[2])


if __name__ == "__main__":
    main()
