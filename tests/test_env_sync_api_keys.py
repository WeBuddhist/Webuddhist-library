"""A sync from prod keeps the target environment's own API keys and drops prod's (scripts/env_sync/api_keys.py)."""
import importlib.util
import json
from pathlib import Path

import pytest

from config import settings
from database import api_key as keys
from database import application as application_database

_PATH = Path(__file__).parent.parent / "scripts" / "env_sync" / "api_keys.py"
_spec = importlib.util.spec_from_file_location("env_sync_api_keys", _PATH)
assert _spec is not None and _spec.loader is not None
api_keys = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_keys)


def _key(key_id, application=None):
    return {"id": key_id, "bound_application_id": application}


def test_plan_restore_drops_prod_copies_and_never_unbinds():
    saved = [_key("own-bound", "app"), _key("own-master"), _key("prod-copy", "app"), _key("orphan", "gone")]
    restore, dropped, skipped = api_keys.plan_restore(saved, prod_key_ids={"prod-copy", "prod-new"}, application_ids={"app"})
    assert [k["id"] for k in restore] == ["own-bound", "own-master"]
    assert dropped == ["prod-copy"]
    assert skipped == ["orphan"]


@pytest.mark.parametrize("environment", ["prod", "", "staging"])
def test_refuses_outside_testing_and_dev(monkeypatch, environment):
    monkeypatch.setattr(api_keys, "settings", settings.model_copy(update={"environment": environment}))
    monkeypatch.setattr("sys.argv", ["api_keys.py", "save", "/tmp/unused.json"])
    with pytest.raises(SystemExit, match="refusing"):
        api_keys.main()


@pytest.mark.asyncio(loop_scope='session')
async def test_sync_keeps_own_keys_and_drops_prod_keys(test_database, _neo4j_container, monkeypatch, tmp_path):
    monkeypatch.setattr(
        api_keys,
        "settings",
        settings.model_copy(
            update={
                "environment": "testing",
                "neo4j_uri": _neo4j_container["uri"],
                "neo4j_username": "neo4j",
                "neo4j_password": _neo4j_container["password"],
                "neo4j_database": _neo4j_container["database"],
            }
        ),
    )
    await application_database.create(test_database, "gone", "gone")
    _, own_bound = await keys.create(test_database, "own-bound", "Own", "own@example.com", "test_application")
    _, own_master = await keys.create(test_database, "own-master", "Own master", "own@example.com")
    await keys.create(test_database, "orphan", "Orphan", "own@example.com", "gone")
    await keys.create(test_database, "prod-copy", "Prod", "prod@example.com", "test_application")
    saved = tmp_path / 'api-keys.json'
    api_keys.save(str(saved))
    assert saved.stat().st_mode & 511 == 384
    assert 'own-bound' in saved.read_text() and 'api_key_hash' in json.loads(saved.read_text())[0]
    async with test_database.get_session() as session:
        await session.run('MATCH (k:ApiKey) DETACH DELETE k')
        await session.run("MATCH (a:Application {id: 'gone'}) DETACH DELETE a")
    _, prod_copy = await keys.create(test_database, "prod-copy", "Prod", "prod@example.com", "test_application")
    _, prod_new = await keys.create(test_database, 'prod-new', 'Prod new', 'prod@example.com')
    api_keys.restore(str(saved))
    assert await keys.validate_key(test_database, own_bound) == {
        "id": "own-bound",
        "bound_application_id": "test_application",
    }
    assert await keys.validate_key(test_database, own_master) == {"id": "own-master", "bound_application_id": None}
    assert await keys.validate_key(test_database, prod_copy) is None
    assert await keys.validate_key(test_database, prod_new) is None
    assert sorted((k['id'] for k in await keys.list_all(test_database))) == ['own-bound', 'own-master']
