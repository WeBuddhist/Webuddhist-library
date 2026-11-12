# Tags API Documentation

Tags are application-scoped labels that can be attached to texts and segments. They are useful for cross-cutting collections such as "philosophy", "practice", "featured", or project-specific editorial states.

## Concepts

- Tags belong to one application, selected with the required `X-Application` header on tag CRUD routes.
- Tag titles and descriptions are localized string objects.
- Texts can be created or patched with `tag_ids`, and can also be tagged or untagged through dedicated endpoints.
- Segment tags are attached directly to segment nodes.
- Listing tags only returns tags for the requested application.
- A tag can belong to at most one [tag group](#tag-groups) of its application. Text filters OR the tags inside a group.

## Authentication

Use `X-API-Key` in deployed environments. Tag CRUD routes also require `X-Application`.

```text
X-API-Key: your_api_key
X-Application: webuddhist
```

## List Tags

```http
GET /v2/tags
```

Returns all tags that belong to the application in `X-Application`.

```json
[
  {
    "id": "TAG123",
    "title": {
      "en": "Philosophy",
      "bo": "གྲུབ་མཐའ"
    },
    "description": {
      "en": "Texts related to philosophical systems"
    },
    "group_id": null
  }
]
```

`group_id` is the ID of the tag's [tag group](#tag-groups), or `null` when the tag has no group.

Example:

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/tags" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"
```

## Create Tag

```http
POST /v2/tags
```

Creates a tag in the requested application. The same title can exist in different applications, but duplicate titles in the same application are rejected.

Request:

```json
{
  "title": {
    "en": "Meditation",
    "bo": "སྒོམ།"
  },
  "description": {
    "en": "Meditation and contemplative practice texts"
  }
}
```

Response:

```json
{
  "id": "TAG123"
}
```

Example:

```bash
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/tags" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist" \
  -H "Content-Type: application/json" \
  -d '{
    "title": {"en": "Meditation", "bo": "སྒོམ།"},
    "description": {"en": "Meditation and contemplative practice texts"}
  }'
```

## Delete Tag

```http
DELETE /v2/tags/{tag_id}
```

Deletes the tag from the application in `X-Application`. Deleting a tag in one application does not delete same-title tags in another application.

Successful deletion returns `204 No Content`.

Delete behavior:

- Deletes the `Tag` node and its title/description `Nomen` and `LocalizedText` subgraphs.
- Removes all `HAS_TAG` relationships from works and segments that used this tag.
- Does not delete works, texts, editions, segmentations, segments, or applications.

Error responses:

- `404 Not Found`: Tag does not exist in the requested application.
- `401 Unauthorized`: Missing or invalid API key in deployed environments.
- `422 Validation Error`: Missing `X-Application` header.

```bash
curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/tags/TAG123" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"
```

## Tag or Untag a Text

```http
POST /v2/texts/{text_id}/tags/{tag_id}
DELETE /v2/texts/{text_id}/tags/{tag_id}
```

These endpoints attach or remove a tag from the work behind a text. They return `204 No Content`.

```bash
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/TEXT123/tags/TAG123" \
  -H "X-API-Key: your_api_key"

curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/TEXT123/tags/TAG123" \
  -H "X-API-Key: your_api_key"
```

Texts can also be created or replaced with tag IDs:

```json
{
  "title": {"bo": "ཆོས་ཀྱི་གཞུང་།"},
  "language": "bo",
  "category_id": "category",
  "contributions": [{"type": "person", "id": "PERSON123", "role": "author"}],
  "tag_ids": ["TAG123"]
}
```

Filter texts by tag. Pass comma-separated IDs in `tag_id`. Tags in the same [tag group](#tag-groups) are ORed;
a tag without a group is a group of its own. The default `tag_id_match=all` requires every group:

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?tag_id=TAG123,TAG456" \
  -H "X-API-Key: your_api_key"
```

Use `tag_id_match=any` to return texts having at least one listed tag:

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?tag_id=TAG123,TAG456&tag_id_match=any" \
  -H "X-API-Key: your_api_key"
```

## Tag or Untag a Segment

```http
POST /v2/segments/{segment_id}/tags/{tag_id}
DELETE /v2/segments/{segment_id}/tags/{tag_id}
```

These endpoints attach or remove a tag from a segment. They return `204 No Content`.

```bash
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/segments/SEG123/tags/TAG123" \
  -H "X-API-Key: your_api_key"
```

## Tag Groups

A tag group says which tags are alternatives to each other, such as the traditions Theravāda, Mahāyāna and
Vajrayāna. When texts are filtered by tag, a text matches a group if it has **any** of the group's selected tags.
`tag_id_match` then combines the groups: `all` (default) requires every group, `any` requires at least one.
A tag without a group is a group of its own, so applications that never create groups keep the plain
all/any behavior.

With Theravāda and Mahāyāna in a "Tradition" group and Chant ungrouped:

| `tag_id` | Matches |
|---|---|
| `THERAVADA,MAHAYANA` | Texts tagged Theravāda or Mahāyāna |
| `MAHAYANA,CHANT` | Mahāyāna chants |
| `THERAVADA,MAHAYANA,CHANT` | Chants tagged Theravāda or Mahāyāna |

Groups belong to one application and use the required `X-Application` header. A tag can be in at most one group,
and only in a group of its own application.

### List Tag Groups

```http
GET /v2/tag-groups
```

```json
[
  {
    "id": "GROUP123",
    "title": {"en": "Tradition"},
    "description": {"en": "Buddhist traditions"},
    "tag_ids": ["THERAVADA", "MAHAYANA", "VAJRAYANA"]
  }
]
```

### Create Tag Group

```http
POST /v2/tag-groups
```

Takes the same body as [Create Tag](#create-tag) and returns `{"id": "GROUP123"}` with `201 Created`. Duplicate
titles in the same application are rejected with `422`.

### Update Tag Group

```http
PATCH /v2/tag-groups/{group_id}
```

Updates `title` and/or `description` and returns the updated group with `200 OK`.

- A sent field replaces its localizations as a whole, like the other PATCH endpoints: a group titled
  `{"en": "Tradition", "bo": "ལུགས་སྲོལ།"}` patched with `{"title": {"en": "Tradition"}}` keeps only `en`. Send every
  language you want to keep.
- `null` values, an empty body, and unknown fields are rejected with `422`. A title already used by another group
  of the application is rejected with `422`, and an unknown group returns `404`.

```bash
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/tag-groups/GROUP123" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist" \
  -H "Content-Type: application/json" \
  -d '{"title": {"en": "Tradition", "bo": "ལུགས་སྲོལ།"}}'
```

### Delete Tag Group

```http
DELETE /v2/tag-groups/{group_id}
```

Deletes an empty group and returns `204 No Content`. A group that still has tags is rejected with `409 Conflict`,
because deleting it would change how filters combine those tags. Remove its tags first.

### Add or Remove a Tag

```http
POST /v2/tag-groups/{group_id}/tags/{tag_id}
DELETE /v2/tag-groups/{group_id}/tags/{tag_id}
```

Both return `204 No Content`. Adding a tag that is already in another group moves it to this group. Removing
leaves the tag in place without a group. Unknown groups or tags, or ones from another application, return `404`.

```bash
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/tag-groups/GROUP123/tags/THERAVADA" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"
```

## Developer Notes

The router lives in `routers/tags.py`; text and segment tagging endpoints are in `routers/texts.py` and `routers/segments.py`. The request and response models are `TagInput` and `TagOutput` in `models/tag.py`.

Tag groups live in `routers/tag_groups.py` and `database/tag_group_database.py`, stored as
`(:TagGroup)-[:BELONGS_TO]->(:Application)` with `(:Tag)-[:IN_GROUP]->(:TagGroup)`. With `tag_id_match=all`, text
filters resolve the requested tag IDs into groups with `TagDatabase.resolve_groups` for both the Neo4j list and
catalog title search. With `any`, grouping cannot change the result, so the lookup is skipped.

Tag titles and descriptions use the same `LocalizedString` shape as categories. Empty localized strings, extra request fields, and missing required application headers are rejected by Pydantic/FastAPI validation.
