# Audios API Documentation

An audio is one performance of a text: one artist (or group, or AI) and one take. It sits at the same level as a written text. Like a translation, it belongs to the Work of the text it recites, and it names that text. Its files are [recordings](recordings-api.md), for example a version without music and a version with music.

## Concepts

- **Recited text**: Every audio recites exactly one text, chosen when the audio is created. It cannot change. To recite another text, create another audio. The audio belongs to that text's Work.
- **No edition needed**: An audio needs only its text. A recording links to an edition later, when cues are added.
- **Performers**: Credited through the same `contributions` shape used by texts. Every contribution must have role `narrator`, `singer`, or `musician`, and points to one person or AI.
- **One performance**: A second take by the same artist is a new audio. Versions of the same take are recordings of one audio.

## Authentication

Use `X-API-Key` in deployed environments.

```text
X-API-Key: your_api_key
```

## Create Audio

```http
POST /v2/texts/{text_id}/audios
```

```json
{
  "title": {
    "en": "Heart Sutra, morning chant"
  },
  "license": "cc-by",
  "date": "2024",
  "contributions": [
    {
      "type": "person",
      "id": "PER123",
      "role": "singer"
    },
    {
      "type": "person",
      "id": "PER456",
      "role": "musician"
    }
  ]
}
```

Only `contributions` is required. `title` is free text and optional. `language` defaults to the language of the recited text, and `license` defaults to `public`.

Response:

```json
{
  "id": "AUD123"
}
```

Validation rules:

- `contributions` must contain at least one entry, and every entry must have role `narrator`, `singer`, or `musician`. AI narrators use `{"type": "ai", "id": "...", "role": "narrator"}`.
- Referenced persons must already exist; a person can be identified by `id` or `bdrc_id`, but not both.
- `language` is a BCP 47 tag whose base subtag must exist as a language in the database.

Error responses:

- `400 Bad Request`: Unknown language.
- `404 Not Found`: Text does not exist.
- `422 Validation Error`: Another role, an empty `contributions` list, or an unknown person.

## List Audios for a Text

```http
GET /v2/texts/{text_id}/audios
```

Returns every audio that recites the text, ordered by ID. A text with no audio returns `[]`; `404` means the text does not exist.

## Get Audio

```http
GET /v2/audios/{audio_id}
```

```json
{
  "id": "AUD123",
  "text_id": "TXT123",
  "title": {
    "en": "Heart Sutra, morning chant"
  },
  "language": "bo",
  "license": "cc-by",
  "date": "2024",
  "contributions": [
    {
      "type": "person",
      "id": "PER123",
      "bdrc_id": "P1583",
      "role": "singer",
      "name": {
        "bo": "བླ་མ་"
      }
    }
  ],
  "recordings": ["REC123", "REC456"]
}
```

`text_id` is the recited text. `recordings` lists the IDs of the audio's recordings. `404` means the audio does not exist.

## Update Audio

```http
PATCH /v2/audios/{audio_id}
```

Partially updates the audio and returns it. Accepts `title`, `language`, `license`, `date`, and `contributions`. The recited text cannot change.

Supplying `contributions` replaces the whole list, and the performer-role rule applies to the replacement. As everywhere else in the API, null values are rejected in `PATCH` and at least one field must be provided.

## Delete Audio

```http
DELETE /v2/audios/{audio_id}
```

Deletes the audio, its contributions, and its title, returning `204 No Content`. Persons and the recited text stay.

An audio with recordings cannot be deleted: the request fails with `409 Conflict`. Delete its recordings first.

Deleting a text fails with `409 Conflict` while an audio recites it.

## Developer Notes

- Routers: `routers/audios.py`; the text-scoped list and create routes live in `routers/texts.py`.
- Models: `models/audio.py` and `models/contribution.py`.
- Database: `database/audio_database.py`, with the shared `Contribution` queries in `database/contribution_database.py`.
- Graph: `(:Audio)-[:AUDIO_OF]->(:Work)` and `(:Audio)-[:RECITATION_OF]->(:Text)`. The Work is taken from the recited text when the audio is created, and a trigger keeps the two in agreement. Title, language, license, and contributions use the same relationships as on a text.
