# Content state and search: manual maintenance

Ordinary deployment installs the release dependencies and the Neo4j schema (constraints and indexes first, then triggers). It restarts the API and checks `/__/health`. Complete any required data upgrade before starting a release that depends on it. Rebuild search separately when a release changes its mappings. Content edits keep their existing request and response formats, and edition revisions stay internal.

The [environment sync from prod](../../README.md#refresh-testing-or-dev-from-prod) automates the audit, data upgrade with content backfill, and search rebuild on the restored target while its API is stopped.

## What needs migration

| Data | Change and reason |
| --- | --- |
| Neo4j schema | Install constraints, indexes, enum values, and corrected triggers. Title-language uniqueness uses the existing title/language relationships, with temporary locks on Language nodes during the duplicate check. No title-data backfill is needed. |
| Pagination | Audit for empty pages, volumes, and paginations. Correct invalid records before upgrading. Nonempty pages are ordered by offsets; no ordering field or backfill is needed. |
| Segmentation | Audit line continuity within and between segments. Gaps and overlaps require an explicit correction before upgrade; migration does not guess new boundaries. |
| Editions | Record each existing S3 key, verified content length, and internal revision. Existing files stay at their current locations; no copy is needed. Future edits always write fresh keys. |
| Recordings | Convert each recording attached to an edition (`RECORDING_OF`) into an `Audio` plus its `Recording`. Title, language, license, date, and contributions move to the new audio, which recites the edition's text and belongs to its Work. The recording follows the same edition (`FOLLOWS`), keeps its cues unchanged, and records its existing S3 key in `audio_key`. No file is copied. |
| Search | Rebuild the environment's configured indexes when changing mappings or repairing missed updates. Content projection version 3 changes the chunk mapping and adds immutable-content references. |

Schema upgrades repeat idempotent steps without a version marker. Content backfill sets missing fields; reindexing reads current canonical records. Application ownership rules do not require rewriting valid Work relationships.

### Retire the objects/ prefix

New editions use `base_texts/{text_id}/{edition_id}.txt`. Each edit writes a fresh
`base_texts/{text_id}/{edition_id}/{version_id}.txt`. Audio uses `recordings/{recording_id}.{format}`,
including recordings without an edition. Existing files remain readable from their stored keys during rollout.
For edition text under `objects/`, relocation uses the old key's suffix as the version ID, preserving any
earlier file at `base_texts/{text_id}/{edition_id}.txt`.

1. Preview the configured environment with `python -m scripts.relocate_objects`. Confirm the database owns all
   content in that bucket's `objects/` prefix. Review the proposed key changes and total keys to delete. That total includes
   unreferenced files from failed uploads, old revisions, and deleted records. If the preview reports an edition
   or recording without an ID, repair that record before applying the migration.
2. Deploy the new writers and pause API writes, imports and environment syncs. Keep a paired Neo4j/S3 backup
   for any pre-migration snapshots you need to restore. Old signed links into `objects/` stop working after deletion.
3. Run `python -m scripts.relocate_objects --apply` with an operator role that can list the bucket,
   read and copy the files, and delete from `objects/`. The command verifies bytes and content type before
   switching each pointer. Once it verifies that no edition or recording references the old prefix, it
   deletes all current keys there and checks that the prefix lists empty. In versioned buckets, historical
   versions remain subject to the bucket's retention settings. An interrupted run can be resumed with the same command.
4. If content search is enabled, run `python -m scripts.reindex --writes-paused` before resuming writers.
   Search chunks identify the edition by its stored key. A final relocation preview should report zero
   references and zero current keys.

Retire the relocation command and the `objects/` sync entry together once prod, testing and dev all report
zero and the restore procedure covers any supported backups that still reference the old S3 keys.

### Recording conversion

`python -m scripts.migrate --apply` converts every recording still attached to an edition in one transaction, after the new triggers are live; either all convert or none do. Later runs find nothing to convert. The read-only `python -m scripts.migrate` reports pending recordings as a count, and recordings that cannot be converted as `legacy_recording_unconvertible`; repair those first.

The release that introduces `Audio` depends on this conversion: its API neither lists unconverted recordings nor moves their cues on content edits, and the previous API does the same for converted ones. So keep the API stopped from the conversion until the new release starts:

1. Run `python -m scripts.migrate` (read-only) against the environment and repair what it reports.
2. Stop the API (`sudo systemctl stop openpecha-api`).
3. From the release checkout, run `python -m scripts.migrate --apply`.
4. Merge the release; deploy installs it and starts the API.

Retire the recording converter once all supported databases have zero `RECORDING_OF` relationships and
the restore procedure covers any snapshots that still contain them.

## Prepare

1. Back up Neo4j and retain existing S3 objects and search indexes. Rehearse with representative data, including missing content, invalid spans, legacy empty pages, and recordings.
2. Prepare the intended release checkout with Python 3.14, its dependencies, and target-environment configuration. Set `NEO4J_DATABASE` explicitly when it is not `neo4j`.
3. If search is used, install the required ICU, Tibetan, and BDRC OpenSearch plugins. Use the environment's `OPENSEARCH_INDEX` and `OPENSEARCH_CATALOG_INDEX`. The defaults are `content-search` and `catalog-search`. Testing requires both names to start with `testing-`. Startup accepts indexes or aliases with the required projection version.
4. Run `python -m scripts.migrate --content` for a read-only audit. Correct reported data problems before applying changes.

## Upgrade data

Stop all API/import writers and drain in-flight requests. Run only one maintenance command at a time. Measure the required pause during rehearsal; it is not established by local tests. Old mutable-content writers must remain stopped after cutover.

```bash
python -m scripts.migrate --apply --content
```

The content backfill verifies actual S3 lengths and annotation bounds, then fills missing pointers and revisions. It never uploads, copies, or deletes S3 objects. After an interruption, resolve the failure and rerun the command before starting the API.

Search rebuilding is separate and optional for core CRUD. With API/import writers still stopped, rebuild when needed:

```bash
python -m scripts.reindex --writes-paused
```

The command builds fresh indexes from Neo4j/S3, checks document counts, and switches both configured names together. When a name already refers to an alias, its previous backing indexes remain available. On the first rebuild of concrete indexes, the final atomic switch deletes those indexes and creates aliases under the same names. Take a snapshot before this first conversion if the old search data must be retained. Each invocation rebuilds the indexes.

Start the API and check the liveness endpoint, `GET /__/health`.

If search initialization fails, the API starts with search disabled. Repair its configuration or connection and restart the API to enable it.

## Routine operation

Search updates run as best-effort background callbacks after API mutations. Failures are logged and can leave missing or stale results until a later update or manual reindex. There is no separate worker service, persistent job queue, or upload journal. Direct database imports should be followed by a manual reindex when search freshness is needed.

Normal API uploads use fresh S3 keys. Deleting a recording or edition leaves stored files in place. Only the explicit one-time relocation needs permission to delete from the retired prefix.

## Rollback

Use a previous application version compatible with immutable content pointers. Returning to an older mutable-path writer requires a coordinated restoration or reverse migration; a code-only rollback is insufficient once new edits exist. The first concrete-index conversion needs its snapshot to recover the old search indexes; later alias rebuilds retain their previous backing indexes. If rolling search aliases back after further content changes, rebuild first when current results are required.

Production-data rehearsal, migration, and deployment have not been executed from this workspace.
