# Editions API Documentation

Editions are concrete versions of a text. The metadata lives in Neo4j and the base text content is stored separately through the storage layer. An edition is always created under a text.

## Concepts

- **Diplomatic edition**: A transcription of a source. Requires `metadata.type = "diplomatic"`, a `bdrc` value, and a `pagination` annotation at creation. May also include `segmentation`.
- **Critical edition**: A scholarly edition. Must not include `bdrc`, and requires a `segmentation` annotation at creation.
- **Collated edition**: Accepted by the metadata enum, but the create request does not enforce initial pagination or segmentation for this type.
- **Excerpt edition** (`metadata.type = "excerpt"`): An edition made of ordered spans (character ranges) of one stored edition, given in `excerpt_of`, instead of its own content. It serves part of a larger text, for example one chapter or chapters 1 and 4, as a text with `part_of`, without copying words. Its content, segments, table of contents, notes, footnotes, marks and bibliographic metadata are read from the source edition, and content edits to it carry over automatically. It has no annotations of its own; recordings can follow it. See [Excerpt editions](#excerpt-editions).
- **Content operations**: Insert, delete, and replace operations update stored base text and adjust stored spans for annotations on the edition, including the cues of the recordings that follow it.
- **Related editions**: Discovered from alignment and text relationship data.

## Authentication

Use `X-API-Key` in deployed environments.

```text
X-API-Key: your_api_key
```

## Get Edition Metadata

```http
GET /v2/editions/{edition_id}
```

Returns metadata for one edition.

```json
{
  "id": "ED123",
  "text_id": "TXT123",
  "type": "diplomatic",
  "source": "Derge Kangyur",
  "bdrc": "W22084",
  "wiki": null,
  "colophon": "Colophon text",
  "incipit_title": {
    "bo": "འདི་སྐད་བདག་གིས།"
  },
  "alt_incipit_titles": null,
  "excerpt_of": null
}
```

`excerpt_of` gives an excerpt edition's source as `{"edition_id", "spans"}`, with the spans in order and in the source edition's positions; it is `null` for other editions. `404` means the edition does not exist.

## Get Edition Content

```http
GET /v2/editions/{edition_id}/content
```

Returns the stored base text as a JSON string; for an excerpt edition, the text of its spans joined in order. If both `span_start` and `span_end` are supplied, the returned string is sliced with Python-style half-open indexing.

| Query | Type | Required | Description |
|-------|------|----------|-------------|
| `span_start` | integer | No | Start character offset, inclusive |
| `span_end` | integer | No | End character offset, exclusive |

```bash
curl "https://dev.library.webuddhist.com/api/v2/editions/ED123/content?span_start=0&span_end=100" \
  -H "X-API-Key: your_api_key"
```

## Get Edition Content URL

```http
GET /v2/editions/{edition_id}/content/url
```

Returns a short-lived presigned URL for downloading the complete base text directly from storage:

```json
{
  "url": "https://bucket.s3.example/base_texts/TEXT123/ED123.txt?...",
  "expires_at": "2026-10-13T15:00:00Z"
}
```

Fetching `url` returns the raw content as `text/plain; charset=utf-8`. The URL points at the content version that
was current when it was issued; after a content edit it keeps returning the earlier version. Use the existing content endpoint for
character-span requests. The URL expires after one hour and acts as a bearer credential until then, so clients
should not log or persist it. Browser clients that fetch the URL in JavaScript require the S3 bucket's CORS policy
to allow `GET` from the frontend origin. An excerpt edition stores no content object, so this endpoint returns `409`
for it; use the content endpoint instead.

## Patch Edition Content

```http
PATCH /v2/editions/{edition_id}/content
```

Applies one text operation and returns `204 No Content`. The request body is the operation object itself.

Insert:

```json
{
  "type": "insert",
  "position": 10,
  "text": "inserted text"
}
```

Delete:

```json
{
  "type": "delete",
  "start": 10,
  "end": 20
}
```

Replace:

```json
{
  "type": "replace",
  "start": 10,
  "end": 20,
  "text": "replacement text"
}
```

Validation rules:

- `start`, `end`, and `position` are character offsets.
- `start` must be less than `end`.
- Insert and replace text must be non-empty.
- Extra fields are rejected.
- An operation reaching past the end of the content is rejected with `422`.

Subsequent annotation spans are validated against the patched text, not the original.

For a correction, submit the range the editor actually changed. The submitted range determines which segment keeps its ID and which annotations survive. A ranged durchen note keeps the replacement wording when the edit is strictly inside the note. When the edit replaces the beginning or end of the note, the note covers only the surviving original wording. Replacing all of its text with different wording removes the note.

The server uploads a new immutable content object, then commits its pointer, length, span adjustments, and internal revision in one transaction. Internal edition revisions detect concurrent content or annotation changes; those edits receive `409 Conflict`. Revisions are internal: existing request and response bodies stay unchanged. Bounds violations and edits that would empty the edition or any page receive `422`; the content and annotations remain unchanged.

An edit to an edition is replayed into every excerpt edition of it: their spans, content length and recording cues move with the text. An edit that would remove a span entirely returns `409`. Patching an excerpt edition itself returns `409`; edit its source edition instead.

A valid offset from an older editor view cannot be distinguished from an intentional edit without client context. Internal concurrency checks cover changes during request processing, not this cross-request ambiguity.

## Delete Edition

```http
DELETE /v2/editions/{edition_id}
```

Deletes the edition metadata and associated annotation data handled by the database layer. Successful deletion returns `204 No Content`.

Delete behavior:

- Deletes the `Edition` node and its incipit title `Nomen` and `LocalizedText` subgraphs.
- Cascade-deletes segmentations, alignments, pagination, table of contents, bibliographic metadata, durchen notes, footnotes, yigchung marks, spans, segments, pages, volumes, and table of contents sections associated with the edition, including annotations added after edition creation.
- Deletes the edition's `HAS_SOURCE` relationship, but preserves the `Source` node.
- Does not delete the parent `Text`, underlying `Work`, categories, tags, contributors, or lookup/type nodes.
- Does not delete stored base text, recording audio files, or other non-database side effects.

Error responses:

- `404 Not Found`: Edition does not exist.
- A recording that follows the edition blocks deletion with `409 Conflict`, even when it has no cues. [Delete the recording](recordings-api.md#delete-recording) first.
- `409 Conflict`: An excerpt edition uses the edition. Delete the excerpt edition first. Deleting an excerpt edition removes only its spans, never its source edition.
- `401 Unauthorized`: Missing or invalid API key in deployed environments.

## List Editions for a Text

```http
GET /v2/texts/{text_id}/editions
```

Optional query:

| Query | Type | Description |
|-------|------|-------------|
| `edition_type` | `diplomatic`, `critical`, `collated`, or `excerpt` | Filters returned editions by type |

```bash
curl "https://dev.library.webuddhist.com/api/v2/texts/TXT123/editions?edition_type=diplomatic" \
  -H "X-API-Key: your_api_key"
```

## Create Edition

```http
POST /v2/texts/{text_id}/editions
```

Creates an edition, stores its content, creates the supplied initial annotations, and requests a best-effort content-search update.

Diplomatic request with optional segmentation:

```json
{
  "content": "Full diplomatic text content",
  "metadata": {
    "type": "diplomatic",
    "bdrc": "W22084",
    "source": "Derge Kangyur"
  },
  "pagination": {
    "volumes": [
      {
        "index": 34,
        "pages": [
          {
            "reference": "1a",
            "lines": [
              {"start": 0, "end": 28}
            ]
          }
        ]
      }
    ]
  },
  "segmentation": {
    "segments": [
      {"lines": [{"start": 0, "end": 16}]},
      {"lines": [{"start": 16, "end": 28}]}
    ]
  }
}
```

Critical request:

```json
{
  "content": "Full critical text content",
  "metadata": {
    "type": "critical",
    "source": "OpenPecha critical edition",
    "incipit_title": {
      "bo": "འདི་སྐད་བདག་གིས།"
    }
  },
  "segmentation": {
    "segments": [
      {
        "lines": [
          {"start": 0, "end": 50}
        ]
      },
      {
        "lines": [
          {"start": 50, "end": 100}
        ]
      }
    ]
  }
}
```

Response:

```json
{
  "id": "ED123"
}
```

Important validation rules:

- `content` is required, except for excerpt editions, and must contain at least one non-whitespace character.
- `content` is stored exactly as submitted, including leading and trailing whitespace.
- Span offsets in both pagination and segmentation must not extend past the end of `content`.
- Diplomatic editions require `metadata.bdrc` and `pagination`; they may also include `segmentation` in the same request.
- Critical editions must not include `metadata.bdrc`; they require `segmentation` and must not include `pagination`.
- `alt_incipit_titles` can only be set when `incipit_title` is set.
- Every span must satisfy `0 <= start <= end`; see [span layout rules](#span-layout-rules).

### Excerpt editions

An excerpt edition sends `excerpt_of` instead of `content`: the source edition and the spans of it that make up the
text. Chapter 10 of a text, as its own text (created with `part_of`), is a single span of the whole text's edition:

```json
{
  "metadata": {"type": "excerpt"},
  "excerpt_of": {"edition_id": "ED123", "spans": [{"start": 80000, "end": 90000}]}
}
```

A text made of passages that are not continuous, such as chapters 1 and 4, lists several spans. The edition's content
is the spans' text joined in their listed order, and its positions start at 0.

Rules:

- `metadata.type` is `excerpt` exactly when `excerpt_of` is sent; `content`, `segmentation` and `pagination` are rejected with it.
- `spans` is non-empty; each span covers at least one character inside the source edition's current content.
- Spans must not overlap; their order is free.
- The source edition must belong to another text, since an edition is a version of its whole text.
- If the text has `part_of`, the source edition must be an edition of that whole text.
- The source edition may itself be an excerpt edition, for a part of a part. The spans are then stored as the
  matching spans of the edition holding the content, so `GET /v2/editions/{id}` reports that edition and its
  positions, and the intermediate edition can be deleted without affecting the new one.
- `excerpt_of` cannot be changed after creation; delete and recreate the edition instead.

Reads and writes:

- `GET .../content` joins the spans; `GET .../content/url` returns `409`.
- `GET .../segmentation/segments` returns the source edition's segments that overlap each span, trimmed to it, in
  the excerpt edition's positions, with their original IDs. Translations align to those same segments through the
  source edition. A segment split across two spans is listed once per span.
- `GET .../segmentation` returns the source edition's segmentation with `id`, `edition_id` and `text_id` set to
  the excerpt edition, or `404` when the source edition has none.
- `GET .../table-of-contents` returns the source edition's tables of contents with sections trimmed and shifted the
  same way; sections outside every span are left out.
- `GET .../yigchungs` and `GET .../bibliographic` return the source edition's annotations trimmed and shifted the
  same way, once per span they overlap. `GET .../durchens` returns only notes that lie wholly inside one span, since
  a note on a cut passage would no longer match its text. `GET .../footnotes` returns footnotes whose position falls
  inside a span; a position at a span's end is kept only for the last span. These annotations keep their original
  IDs, with `edition_id` and `text_id` set to the excerpt edition. Pagination is not served.
- Adding or deleting annotations, alignments or content operations on the excerpt edition returns `409`. Annotate
  and edit the source edition; the changes show through. Recordings can follow an excerpt edition and their cues
  use its positions.
- Content search indexes only editions with their own segmentation, so text served by excerpt editions is never returned twice.

### Span offset semantics

All `start` and `end` values are **Unicode code point offsets** into `content` exactly as
submitted, with `end` exclusive. Getting the unit wrong silently misaligns every annotation,
so note the following:

- Do not send UTF-8 byte offsets. Tibetan code points are three bytes each, so byte offsets
  run roughly 3× long and are rejected.
- Do not send grapheme cluster counts. A Tibetan stack with subjoined consonants is several
  code points but one visual unit.
- Normalize before computing offsets, and send the same form you measured. In romanized Pali,
  `ā` is one code point as NFC but two as NFD, so `sammāsambuddhassa` is 17 or 18 code points
  depending on form. The API stores content byte-for-byte and never normalizes it.
- JavaScript's `String.length` and Python's `len()` both work for Tibetan and romanized Pali,
  since those scripts lie in the Basic Multilingual Plane. They diverge only for characters
  above U+FFFF, such as emoji or Siddham.

### Span layout rules

A span is any range with `0 <= start <= end`. An empty span, where `start` equals `end`, marks a
position rather than covering text, such as a heading with no content of its own.
A span with `start` greater than `end` is rejected with `422`. Each page must cover at least one character.

Wherever spans appear as a list, that list must follow one of three layouts:

| Spans | Layout |
| --- | --- |
| `lines` within a page or segment | contiguous: each line starts exactly where the previous line ended |
| `pages` within a volume | contiguous |
| `volumes` within a pagination | sorted and non-overlapping, in volume index order; gaps are allowed |
| sections at one level of a table of contents | sorted and non-overlapping; gaps are allowed |
| `segments` within a segmentation | contiguous: no overlaps or gaps |

Two rules are not about layout. A table of contents subsection must sit inside its parent's span, and
volume indexes must be unique, with `null` allowed only for a single volume. Content delete and replace operations
also require a nonempty range.

## Edition Annotation Collections

Annotations can be listed and created by type under an edition. Creation returns `{ "id": "..." }`.
Each edition has at most one segmentation. Large segment collections are paginated from the edition segmentation `/segments` endpoint.

Spans follow the [span offset semantics](#span-offset-semantics) above and are checked against the
edition's current content. A span reaching past the end of the content is rejected with `422` and
nothing is written.

### Segmentations

```http
GET /v2/editions/{edition_id}/segmentation
POST /v2/editions/{edition_id}/segmentation
```

GET response:

```json
{
  "id": "SGN123",
  "edition_id": "ED123",
  "text_id": "TXT123"
}
```

Use `GET /v2/editions/{edition_id}/segmentation/segments?limit=500&offset=0` to fetch paginated segment rows.

Request:

```json
{
  "segments": [
    {
      "reference": "1.1",
      "lines": [
        {"start": 0, "end": 50}
      ]
    }
  ]
}
```

### Alignments

```http
GET /v2/editions/{edition_id}/alignments
PUT /v2/editions/{source_edition_id}/alignments/{target_edition_id}
GET /v2/editions/{source_edition_id}/alignments/{target_edition_id}?limit=500&offset=0
DELETE /v2/editions/{source_edition_id}/alignments/{target_edition_id}
```

Alignments are no longer annotation resources. They are direct `ALIGNED_TO` relationships between existing source and target edition segments.

`GET /v2/editions/{edition_id}/alignments` returns directional contexts that include the requested edition:

```json
[
  {
    "aligned_edition_id": "ED_SOURCE",
    "aligned_text_id": "TXT_SOURCE",
    "target_edition_id": "ED_TARGET",
    "target_text_id": "TXT_TARGET"
  }
]
```

The client can infer whether the requested edition is on the source or target side by comparing `edition_id` to `aligned_edition_id` and `target_edition_id`.

PUT request:

```json
{
  "alignments": [
    {
      "source_segment_reference": "1.1",
      "target_segment_reference": "1.1"
    }
  ]
}
```

`PUT` validates all references before changing relationships and returns `204 No Content`. Edition-pair `GET` returns paginated source/target `SegmentWithContextOutput` pairs.

### Pagination

```http
GET /v2/editions/{edition_id}/pagination
POST /v2/editions/{edition_id}/pagination
```

```json
{
  "volumes": [
    {
      "index": 34,
      "pages": [
        {
          "reference": "1a",
          "lines": [
            {"start": 0, "end": 500}
          ]
        }
      ]
    }
  ]
}
```

`index` is the volume number in the source collection, for example `34` for a text in Degé Kangyur
vol. 34. A pagination with a single volume may set `index` to `null` or omit it, meaning the text has
only ever had one volume. With more than one volume, every volume must set `index`, and each index may
appear only once; otherwise the request is rejected with `422`. Indexes do not need to start at `1` or
be continuous, so a text spanning vols. 34–35 uses indexes `34` and `35`.

Page offsets are positions in the edition's single base text, not per-volume positions, so volumes
carve up that text between them. Each volume must therefore start at or after the previous volume
ends, and a pagination whose volumes overlap or run counter to their index order is rejected with
`422`. Adjacent volumes are fine: volume 35 may start exactly where volume 34 ends.

Empty pages are rejected with `422`, including pages with no lines or only zero-width lines.
A page must cover at least one character. Page order follows character offsets; no separate ordering
property is stored. Content edits that would empty any page are also rejected with `422`.

### Table of contents

```http
GET /v2/editions/{edition_id}/table-of-contents
POST /v2/editions/{edition_id}/table-of-contents
```

GET response:

```json
[
  {
    "id": "OUT123",
    "edition_id": "ED123",
    "text_id": "TXT123",
    "metadata": {
      "name": "Main sa bcad"
    },
    "sections": [
      {
        "id": "SEC123",
        "title": {
          "bo": "ལེའུ་དང་པོ།",
          "en": "Chapter 1"
        },
        "summary": {
          "en": "Opening topic"
        },
        "span": {"start": 0, "end": 1200},
        "subsections": []
      }
    ]
  }
]
```

Request:

```json
{
  "metadata": {
    "name": "Main sa bcad"
  },
  "sections": [
    {
      "title": {
        "bo": "ལེའུ་དང་པོ།",
        "en": "Chapter 1"
      },
      "summary": {
        "en": "Opening topic"
      },
      "span": {"start": 0, "end": 1200},
      "subsections": [
        {
          "title": {
            "en": "Section 1.1"
          },
          "span": {"start": 0, "end": 350}
        }
      ]
    }
  ]
}
```

Each section has a required localized `title`, optional localized `summary`, required `span`, and optional recursive `subsections`. Each subsection span must be fully contained inside its parent section span. Multiple table of contents can be attached to the same edition. Returned sections and subsections are ordered by their span start/end positions.

Sections at the same level must be sorted and must not overlap, so a table of contents is submitted in
document order. A heading that carries no content of its own — a title immediately followed by the next
heading — is an empty span at the position where it appears, such as `{"start": 229637, "end": 229637}`.

### Bibliographic Metadata

```http
GET /v2/editions/{edition_id}/bibliographic
POST /v2/editions/{edition_id}/bibliographic
```

```json
{
  "span": {"start": 5000, "end": 5500},
  "type": "colophon",
  "metadata": {}
}
```

Supported types: `colophon`, `incipit`, `alt_incipit`, `alt_title`, `person`, `title`, and `author`.

### Durchen Notes

```http
GET /v2/editions/{edition_id}/durchens
POST /v2/editions/{edition_id}/durchens
```

```json
[
  {
    "span": {"start": 100, "end": 150},
    "text": "Variant reading from witness B",
    "metadata": {}
  }
]
```

The array must contain at least one note. Creation is atomic and returns an array of `{ "id": "..." }` objects in request order.

### Footnotes

```http
GET /v2/editions/{edition_id}/footnotes
POST /v2/editions/{edition_id}/footnotes
DELETE /v2/editions/{edition_id}/footnotes
```

```json
[
  {"position": 450, "text": "Cited from Degé Tengyur, vol. 34", "metadata": {}}
]
```

`position` is a gap index from `0` (before the first character) to the content length (after the last character). The array must contain at least one footnote; creation is atomic, returns `{ "id": "..." }` objects in request order, and rejects a position past the content length with `422`. `GET` lists footnotes ordered by position. `DELETE` removes every footnote on the edition (durchen notes are kept) and returns `204`. See [annotations-api.md](annotations-api.md#create-footnotes).

## Recordings

```http
GET /v2/editions/{edition_id}/recordings
```

Returns the recordings that follow the edition: the audio files whose cues point into its content, used for follow-along playback. Recordings are created under an audio with `POST /v2/audios/{audio_id}/recordings`, not under an edition. See [recordings-api.md](recordings-api.md) and [audios-api.md](audios-api.md).

## Related Editions

```http
GET /v2/editions/{edition_id}/related
```

Returns editions related through alignment or text relationships.

## Developer Notes

- Router: `routers/editions.py`.
- Edition creation route: `routers/texts.py`.
- Models: `models/edition.py`, `models/annotation.py`, `models/content_operation.py`, `models/recording.py`, and `models/requests.py`.
- Storage dependency handles base text and recording audio reads/writes; database dependencies handle metadata, annotations, span adjustment, and related lookups.
