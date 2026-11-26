# Database package reference

This document provides an overview of the Neo4j-backed data access layer in [database](../../database).

---

## Table of Contents

1. [Overview](#overview)
2. [Connection](#connection)
3. [Domain modules](#domain-modules)
4. [Annotation subpackage](#annotation-subpackage)
5. [Supporting modules](#supporting-modules)

---

## Overview

The **Database** class in [database/database.py](../../database/database.py) holds an async Neo4j driver. Domain operations live in modules such as `text` and `person`. The graph schema supplies the unique constraints and required relationship rules. Each required relationship generates an APOC existence check, plus an at-most-one check when its cardinality is `one`. The trigger module also defines domain-specific rules. [Graph schema](../../database/neo4j_schema.yaml) · [Lookup indexes](../../database/schema.py) · [APOC triggers](../../database/neo4j_triggers.py)

---

## Connection

**Constructor:** `Database(neo4j_uri: str, neo4j_auth: tuple[str, str], neo4j_database: str = "neo4j")`

The caller supplies connection settings. Construction creates the driver; connectivity is checked separately.

**Methods:**

- `await verify_connectivity()` – Checks the connection.
- `get_session() -> AsyncSession` – Returns a session for the configured database; use it with `async with`.
- `await read(fn, *args)` / `await write(fn, *args)` – Runs `fn(tx, *args)` in a managed read or write transaction, retried on transient errors, in one trace span.
- `await query(cypher, *, write=False, **params)` – Runs one statement in its own managed transaction and returns its records.
- `await close()` – Closes the driver.
- `async with Database(uri, (username, password)) as db` – Checks connectivity on entry and closes the driver on exit.

---

## Domain modules

Import a module and alias it when the short name would shadow a local (e.g. `from database import text as text_database`). Functions accept either `Database` or an open transaction via `@reading` / `@writing` where dual-mode is needed.

| Module | Responsibility |
|--------|-----------------|
| [api_key.py](../../database/api_key.py) | API key creation, validation, and optional binding to an application. |
| [application.py](../../database/application.py) | Applications that own categories and tags; texts and editions are shared. |
| [text.py](../../database/text.py) | Text/work CRUD, contributions, commentary/translation relationships, and filtered listing. |
| [edition.py](../../database/edition.py) | Edition CRUD, content, related editions, and text relationships. |
| [alignment.py](../../database/alignment.py) | Direct edition-pair segment alignment relationships. |
| [segment.py](../../database/segment.py) | Segment retrieval, related segments, tag context, and span lookup; segments are created through segmentation. |
| [audio.py](../../database/audio.py) | Audio (one performance of a text): recited text, Work, title, language, license and performers. |
| [recording.py](../../database/recording.py) | Recording files of an audio, the edition each follows, and their cues. |
| [person.py](../../database/person.py) | Person CRUD and listing with BDRC/wiki filters; name search uses the catalog search service. |
| [language.py](../../database/language.py) | Language code creation, deletion, and listing. |
| [category.py](../../database/category.py) | Application-owned categories and parent/child hierarchy. |
| [tag.py](../../database/tag.py) | Application-owned tags and their attachment to texts and segments. |

---

## Annotation subpackage

[database/annotation/](../../database/annotation/) — import as `from database.annotation import note as note_database`, etc.

| Module | Responsibility |
|--------|-----------------|
| [segmentation.py](../../database/annotation/segmentation.py) | Segmentation annotations and their segments. |
| [pagination.py](../../database/annotation/pagination.py) | Pagination (volume/page) annotations. |
| [table_of_contents.py](../../database/annotation/table_of_contents.py) | Table of contents annotations with localized section titles, summaries, spans, and recursive subsection hierarchy. |
| [note.py](../../database/annotation/note.py) | Note annotations: durchen (span) and footnote (point, stored as an empty span). |
| [mark.py](../../database/annotation/mark.py) | Span-only mark annotations (currently yigchung). |
| [bibliographic.py](../../database/annotation/bibliographic.py) | Bibliographic metadata annotations. |

---

## Supporting modules

| Module | Responsibility |
|--------|----------------|
| [nomen.py](../../database/nomen.py) | Localized names/titles and alternative-name links. |
| [contribution.py](../../database/contribution.py) | Shared contribution creation and updates. |
| [content_state.py](../../database/content_state.py) | Internal edition revision guards and validated reads. |
| [span.py](../../database/span.py) | Span adjustments within the content service's validated edit transaction. |
| [schema.py](../../database/schema.py) | Loads neo4j_schema.yaml for unique constraints; holds the explicit lookup indexes. |
| [neo4j_triggers.py](../../database/neo4j_triggers.py) | APOC triggers: required/at-most-one relationships and the custom rules. |
| [annotation/single_span.py](../../database/annotation/single_span.py) | Shared persistence for bibliographic metadata, notes, and marks. |
| [repository.py](../../database/repository.py) | `SessionSource`, `@reading`/`@writing`, fetch helpers, constraint wrapping. |

For method-level detail and Cypher usage, see the linked source files.

### Manual schema upgrades

Run `python -m scripts.migrate` for a read-only audit of the configured
`NEO4J_DATABASE`. With application writes paused, run
`python -m scripts.migrate --apply`. Run only one migration command at a time.
Each invocation repeats the idempotent steps, so interrupted runs can be retried.
Apply never guesses repairs for malformed
annotations or duplicate titles: resolve audit failures first, from source data
where necessary.

The command rejects empty pagination structures, installs constraints and lookup
indexes, and updates APOC triggers.
Existing metadata discarded by old writers cannot be recovered by
this migration. Allow the configured APOC trigger refresh interval to elapse
and verify the audit before resuming writes.

Title-language uniqueness uses the existing APOC trigger and title/language
relationships. The trigger locks the affected Language nodes in code order before
checking duplicates, serializing title checks that share a language. No duplicate
language property or additional node label is stored.

Content backfill records existing S3 keys and fills missing lengths and revisions without a separate migration marker or copying files. For existing deployments, use `python -m scripts.migrate --content` to audit actual objects and `--apply --content` during the write pause. Search reindexing is an independent manual command. See the [maintenance runbook](../design/cutover.md). Internal revisions do not add requirements to editor requests.
