# Audio as its own Expression

Status: final design, implemented on branch `feat/audio-expression-proposal`. Design: [#449](https://github.com/WeBuddhist/Webuddhist-library/issues/449). Implementation: [#452](https://github.com/WeBuddhist/Webuddhist-library/issues/452).

## Idea

- One performance of a text (one artist, one take) is an **Audio**. It is an Expression, at the same level as a Text.
- The Audio follows the **translation** pattern. It belongs to the same Work as the text it recites, and it names that text. It does not follow the commentary pattern (a new Work), because a recitation adds no new content.
- Each file of that performance (with music, without music) is a **Recording**. It is the Manifestation, like an Edition is of a Text.
- A Recording can follow one edition of the recited text. Cues point into that edition. The cue feature that is already in `dev` stays as it is.

## Graph

![Audio as Expression](audio-as-expression.png)

Editable source: [`audio-as-expression.excalidraw`](audio-as-expression.excalidraw) (open at excalidraw.com, File → Open). Mermaid source: [`audio-as-expression.mmd`](audio-as-expression.mmd).

| Level | Written (unchanged) | Audio |
|---|---|---|
| Work | `Work` | the same `Work` |
| Expression | `Text`: title, language, license, contributions. `TEXT_OF → Work`, `TRANSLATION_OF → Text` | `Audio`: title, language, license, date, contributions. `AUDIO_OF → Work`, `RECITATION_OF → Text` |
| Manifestation | `Edition`: file `content_key = objects/{edition_id}`, annotations | `Recording`: file `audio_key = objects/{recording_id}`, format, size, duration, version name, cues. `RENDITION_OF → Audio`, `FOLLOWS → Edition` (optional) |

Contributions on an Audio have the same shape as on a Text: `HAS_CONTRIBUTION → Contribution -[:WITH_ROLE]→ RoleType`, `-[:BY]→ Person | AI`. The audio roles are `narrator`, `singer` and `musician`.

## Rules

| # | Rule | Enforcement |
|---|---|---|
| R1 | The recited Text belongs to the Audio's Work. | The API takes the Work from the Text. Trigger `enforce_audio_recitation_in_work`. |
| R2 | The `FOLLOWS` edition is an edition of the recited Text. An edition of a translation or of another text is not permitted. | `422`. Trigger `enforce_recording_follows_recited_text`. |
| R3 | Cues only on a Recording with `FOLLOWS`. | `422`. Trigger `enforce_cue_requires_follows`. |
| R4 | Changing or removing `FOLLOWS` deletes the cues of that Recording. | API, in the same transaction. The same trigger rejects a change that keeps cues. |
| R5 | Deleting an edition never deletes audio. While a following Recording has cues, the delete fails. | `409`. |
| R6 | A Text cannot be deleted while an Audio recites it. | `409`. |
| R7 | An Audio cannot be deleted while it has Recordings. | `409`. |
| R8 | IDs come from `generate_id()`. New files go to `objects/{recording_id}`, saved as `audio_key`. Existing files keep their key. | API. |

An Audio needs only its Text. The edition is optional and can come later, when cues are added.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST`, `GET` | `/v2/texts/{text_id}/audios` | Create an Audio for a text; list the Audios of a text |
| `GET`, `PATCH`, `DELETE` | `/v2/audios/{audio_id}` | One Audio. The recited text cannot change. |
| `POST`, `GET` | `/v2/audios/{audio_id}/recordings` | Upload a Recording (multipart, optional `edition_id`); list Recordings |
| `GET`, `PATCH`, `DELETE` | `/v2/recordings/{recording_id}` | One Recording (version name, duration) |
| `PUT` | `/v2/recordings/{recording_id}/edition/{edition_id}` | Follow an edition (R2, R4) |
| `DELETE` | `/v2/recordings/{recording_id}/edition` | Stop following, and delete the cues |
| `GET`, `PUT`, `DELETE` | `/v2/recordings/{recording_id}/cues` | Unchanged |
| `GET` | `/v2/editions/{edition_id}/recordings` | The Recordings that follow an edition |

`POST /v2/editions/{edition_id}/recordings` is removed. Details: [audios-api.md](../api-documentation/audios-api.md), [recordings-api.md](../api-documentation/recordings-api.md).

## Existing recordings

`python -m scripts.migrate --apply` converts each recording that is attached to an edition, with the API stopped, before the release starts:

- A new Audio recites the edition's text and belongs to its Work. Title, language, license, date and contributions move to it. A missing language becomes the text's language; a missing license becomes `public`.
- The Recording follows the same edition, keeps its cues, and records its existing file key in `audio_key`. No file is copied.
- All recordings convert in one transaction. Later runs find nothing to convert.

The conversion runs after the new triggers are live, because the triggers they replace reject a Recording without `RECORDING_OF`. See [cutover.md](../design/cutover.md).

## Not in scope

- Moving existing files to the new key pattern.
- Restricting the roles a Text accepts. Texts accept every role today, `narrator` included.
- Highlights in other editions through `ALIGNED_TO` segments.
- Cleanup of audio files that no Recording references.
- Automatic cue generation from audio.
