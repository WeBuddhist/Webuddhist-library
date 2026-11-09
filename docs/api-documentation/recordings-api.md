# Recordings API Documentation

A recording is one audio file of an [audio](audios-api.md), for example the version "without music" of a chant. The audio holds what describes the performance: the recited text, title, language, license, date, and the performers. A recording holds the file and what is specific to that file: its format, size, duration, an optional version name, and its cues.

## Concepts

- **Audio and recording**: An audio is an Expression of a text's Work, like a translation. A recording is its Manifestation, like an edition is of a text. Create the audio first with `POST /v2/texts/{text_id}/audios`, then upload recordings under it.
- **Upload**: Audio is submitted as `multipart/form-data` in the same request that creates the metadata, so a recording never exists without its file.
- **Format**: Derived from the upload's content type, not from the filename. Supported formats are `mp3`, `wav`, `m4a`, `ogg`, and `flac`.
- **Follows**: A recording can follow one edition. The edition must be an edition of the text the audio recites. Cues point into that edition, so a recording needs to follow an edition before it can have cues.
- **Playback**: The audio endpoint redirects to a short-lived presigned storage URL rather than proxying bytes, so players get range requests and seeking directly from storage.
- **Cues**: Optional time ranges of the audio, each mapped to the span of base text being recited during it, so a client can highlight the text as it is read. A span can be any length, from a verse down to a single syllable.

## Authentication

Use `X-API-Key` in deployed environments.

```text
X-API-Key: your_api_key
```

## Create Recording

```http
POST /v2/audios/{audio_id}/recordings
```

Sent as `multipart/form-data` with two parts:

| Part | Type | Required | Description |
|------|------|----------|-------------|
| `metadata` | string | Yes | JSON-encoded recording metadata |
| `audio` | file | Yes | The audio file, with an audio content type |

The `metadata` part:

```json
{
  "title": {
    "en": "Without music"
  },
  "duration_ms": 754000,
  "edition_id": "ED123"
}
```

Every field is optional, so `{}` is valid. `title` names the version. `edition_id` makes the recording follow that edition.

```bash
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/audios/AUD123/recordings" \
  -H "X-API-Key: your_api_key" \
  -F 'metadata={"edition_id":"ED123"}' \
  -F "audio=@chant.mp3;type=audio/mpeg"
```

Response:

```json
{
  "id": "REC123"
}
```

Validation rules:

- The `audio` part must have a supported audio content type and a non-empty body.
- `edition_id`, when given, must be an edition of the text the audio recites. An edition of a translation or of another text is rejected.
- `format` and `size_bytes` are derived from the upload and cannot be set by the caller.
- `duration_ms` is not derived from the file; supply it if you want it recorded.

The audio and the edition are checked before the upload, so a rejected request stores no file. The audio is then uploaded to a fresh key, `objects/{recording_id}`, before metadata is committed. A failed request after that point can leave an unused storage object, but cannot publish recording metadata for an unsuccessful upload.

Error responses:

- `404 Not Found`: Audio or edition does not exist.
- `422 Validation Error`: Malformed metadata JSON, an edition of another text, an unsupported or missing audio content type, or an empty audio body.

## List Recordings

```http
GET /v2/audios/{audio_id}/recordings
GET /v2/editions/{edition_id}/recordings
```

The first returns every recording of an audio. The second returns the recordings that follow an edition, which is what a reader needs for follow-along playback. Both are ordered by ID and return `[]` when there are none. `404` means the audio or edition does not exist.

```json
[
  {
    "id": "REC123",
    "audio_id": "AUD123",
    "text_id": "TXT123",
    "edition_id": "ED123",
    "title": {
      "en": "Without music"
    },
    "duration_ms": 754000,
    "format": "mp3",
    "size_bytes": 18432000
  }
]
```

`text_id` is the text the audio recites. `edition_id` is present only when the recording follows an edition.

## Get Recording

```http
GET /v2/recordings/{recording_id}
```

Returns one recording in the same shape as the list endpoints. `404` means the recording does not exist.

## Get Recording Audio

```http
GET /v2/recordings/{recording_id}/audio
```

Responds `307 Temporary Redirect` with a `Location` header pointing at a presigned storage URL valid for one hour. Most HTTP clients follow this automatically; a client that needs the URL itself should disable redirect following and read the header.

Because the redirect target is served by storage rather than the API, range requests and seeking work without any further support from this service.

```bash
curl -L "https://api-l25bgmwqoa-uc.a.run.app/v2/recordings/REC123/audio" \
  -H "X-API-Key: your_api_key" \
  -o reading.mp3
```

## Update Recording

```http
PATCH /v2/recordings/{recording_id}
```

Partially updates the recording and returns it. Accepts `title` (the version name) and `duration_ms`. Performance metadata such as language, license, and performers belongs to the audio; change it with `PATCH /v2/audios/{audio_id}`.

```json
{
  "title": {
    "en": "With tingsha and drum"
  }
}
```

As everywhere else in the API, null values are rejected in `PATCH` and at least one field must be provided. The audio file itself cannot be patched. Replace it by deleting the recording and uploading a new one.

## Follow an Edition

```http
PUT /v2/recordings/{recording_id}/follows/{edition_id}
DELETE /v2/recordings/{recording_id}/follows
```

`PUT` makes the recording follow an edition and returns `204 No Content`. The edition must be an edition of the text the audio recites; otherwise the request fails with `422`. Following the edition it already follows changes nothing. Following a different edition deletes the recording's cues, because their spans point into the old edition.

`DELETE` stops following the edition and deletes the cues, returning `204 No Content`. It also returns `204` when the recording follows no edition.

Error responses:

- `404 Not Found`: Recording or edition does not exist.
- `422 Validation Error`: The edition is an edition of another text.

## Delete Recording

```http
DELETE /v2/recordings/{recording_id}
```

Deletes the recording metadata, returning `204 No Content`. Its audio file remains in storage.

Delete behavior:

- Deletes the `Recording` node, its cues, and its title `Nomen` and `LocalizedText` subgraph.
- Retains the audio object in storage; the application does not clean up unreferenced files.
- Does not delete the parent `Audio`, the followed `Edition`, or lookup nodes.

Deleting an edition never deletes recordings. While a recording with cues follows the edition, the edition delete fails with `409 Conflict`; remove the cues or the follow first. Recordings without cues stop following the deleted edition and stay.

## Recording Cues

A cue says that, between `start_ms` and `end_ms` of the audio, the performer is reciting the base text in `span`. Spans use the same Unicode code point offsets into the content of the edition the recording follows, like every other annotation.

### Get Cues

```http
GET /v2/recordings/{recording_id}/cues
```

Returns every cue of the recording, ordered by `start_ms`, without pagination. A recording with no cues, or one that follows no edition, returns `[]`; `404` means the recording does not exist.

```json
[
  {"span": {"start": 0, "end": 42}, "start_ms": 0, "end_ms": 4200},
  {"span": {"start": 42, "end": 97}, "start_ms": 4200, "end_ms": 9100}
]
```

### Replace Cues

```http
PUT /v2/recordings/{recording_id}/cues
```

Replaces all cues of the recording and returns `204 No Content`. The request body is the array of cues, in the same shape `GET` returns.

```json
[
  {"span": {"start": 0, "end": 42}, "start_ms": 0, "end_ms": 4200},
  {"span": {"start": 42, "end": 97}, "start_ms": 4200, "end_ms": 9100}
]
```

Validation rules:

- The recording must follow an edition.
- The array must contain at least one cue. Use `DELETE` to remove all cues.
- `start_ms` must be less than `end_ms`.
- A span must cover at least one character and must lie within the edition content.
- Cues must be sorted by time and must not overlap in time. Gaps between cues are allowed.
- The same span may appear in several cues, which is how a refrain recited more than once is expressed.

Error responses:

- `404 Not Found`: Recording does not exist.
- `422 Validation Error`: The recording follows no edition, or any other rule above is violated.

### Delete Cues

```http
DELETE /v2/recordings/{recording_id}/cues
```

Deletes all cues of the recording and returns `204 No Content`. The recording and its audio are untouched.

### Content edits

Cue spans follow `PATCH /v2/editions/{edition_id}/content` on the followed edition, with the same rules as notes. Only the span moves; the audio times never change.

- An edit before a cue shifts its span, so it keeps covering the same recited text. An edit after it leaves it unchanged.
- An insert, delete, or replace strictly inside a span resizes the span to cover the edited text.
- A delete or replace that covers the whole span deletes the cue. That stretch of audio then has no highlight.
- A delete that overlaps one end of a span trims the span to the text that remains. A replace does the same, except that a replace overlapping the end of the span keeps the replacement text inside it.

### Client playback

1. Find the recordings that follow the edition with `GET /v2/editions/{edition_id}/recordings`, and fetch the edition's base text, for example through `GET /v2/editions/{edition_id}/content/url`.
2. Fetch the cues with `GET /v2/recordings/{recording_id}/cues`.
3. Play the audio from `GET /v2/recordings/{recording_id}/audio`.
4. On each time update, binary-search the cues by `start_ms` and highlight `content[span.start:span.end]` for the cue containing the current time. This works because cues are sorted and do not overlap.

## Developer Notes

- Routers: `routers/recordings.py`; the audio-scoped list and create routes live in `routers/audios.py`, and the edition-scoped list in `routers/editions.py`.
- Models: `models/recording.py`.
- Database: `database/recording_database.py`.
- Storage: new audio is stored at `objects/{recording_id}`, the same pattern editions use for content. The key is saved on the recording as `audio_key`. Recordings created before the audio change keep their original key, `recordings/{edition_id}/{recording_id}.{format}`. Audio is never replaced under the same key.
- Graph: `(:Recording)-[:RENDITION_OF]->(:Audio)`, with an optional `(:Recording)-[:FOLLOWS]->(:Edition)`. Triggers require the followed edition to be an edition of the text the audio recites, and allow cues only while `FOLLOWS` exists.
- Cues are stored as `(:Span)-[:SPAN_OF]->(:Cue {start_ms, end_ms})-[:CUE_OF]->(:Recording)`. `Cue` has no `id`, because cues are only written and read in bulk.
- `database/span_database.py` adjusts cue spans with the annotation rules, and deletes a `Cue` once its span is deleted.

## Upload lifecycle

Uploads use the multipart upload's spooled file rather than a second full in-memory copy. Metadata is published only after storage succeeds. There is no upload journal, cleanup queue, or client revision/operation identifier. All unreferenced storage objects are retained.
