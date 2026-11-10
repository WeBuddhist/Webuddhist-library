# Tradition and Chants Tagging: Main Report

**Library audited:** library-prod.webuddhist.com (prod), application `webuddhist`. Snapshot taken 2026-11-09.
**Uploaded to:** library.webuddhist.com (dev) **only, for now**, on 2026-11-10. Dev was snapshotted before and after the upload.
**Audit date:** 2026-11-09. Updated 2026-11-10 with the dev upload.
**Scope:** all 1,630 texts (877 works) in 12 languages and 14 categories
**Status:** plan committed on branch `tradition-tag-audit`. **Applied to dev and verified. Not applied to prod.**

## 1. Summary

- Every text was audited for a tradition and for chant status.
- The plan **only adds tags**: **400 tradition tags** and **67 Chants tags**, 467 additions in all. They reach 438 works and 480 texts, because translations share their root's tags.
- **No existing tag is removed or changed.**
- After the plan:
  - **1,350 of 1,630 texts** carry a tradition tag. The other **280** stay untagged on purpose, because the rules don't allow a confident tag.
  - **All 892 texts identified as chants** carry the Chants tag.
- Every chant decision is made, except 3 test records that are deliberately left alone.

In sections 4 to 7, **"Today"** is **prod's snapshot of 2026-11-09**, and **"Added by the plan"** is the 467 additions in `tag_plan.tsv`. Prod is not changed yet, so it still matches the "Today" numbers. **Dev has its own before-and-after numbers in section 2**, because its data differs from prod's.

## 2. Upload status: prod and dev

| | Prod (`library-prod.webuddhist.com`) | Dev (`library.webuddhist.com`) |
|---|---|---|
| Texts | 1,630 | 1,445 |
| Snapshot before upload | 2026-11-09 (the "Today" numbers in this report) | 2026-11-10, just before the upload |
| Plan applied | **no**, prod is unchanged | **yes**, 2026-11-10, with `apply_tag_plan.sh --apply` |
| Additions sent | — | **448** of the 467 (19 target texts don't exist on dev; see below) |
| Removed or changed | nothing | nothing; the script only adds |
| Result | — | 448 added, 0 failed |
| Verified | prod dry run on 2026-11-09 (section 11) | dev dry run after the upload: `0 additions, 448 already done, 0 blocked, 0 errors` |

**What was uploaded to dev** (works; translations get the tag through their work):

| Tag | Tag ID | Works |
|---|---|---:|
| Vajrayāna | `ZPcgMZgVJxAMQ7rpfW72a` | 227 |
| Mahāyāna | `zjHEBN2egpftHD7BRMQko` | 152 |
| Theravāda | `296WZOr8MXWteSqBBI7pl` | 2 |
| Chants | `FZ5STsdU0eLvo7Nb2CpvH` | 67 |
| **Total** | | **448** |

**Dev before and after the upload (texts, with works in brackets):**

| Tag | Dev before | Change | Dev after |
|---|---:|---:|---:|
| Vajrayāna `ZPcgMZgVJxAMQ7rpfW72a` | 713 (124) | +230 (+227) | **943 (351)** |
| Mahāyāna `zjHEBN2egpftHD7BRMQko` | 4 (2) | +171 (+152) | **175 (154)** |
| Theravāda `296WZOr8MXWteSqBBI7pl` | 87 (55) | +4 (+2) | **91 (57)** |
| No tradition tag | 641 (605) | −405 (−381) | **236 (224)** |
| Chants `FZ5STsdU0eLvo7Nb2CpvH` | 786 (163) | +67 (+67) | **853 (230)** |
| **Total** | **1,445 (786)** | | **1,445 (786)** |

443 dev texts changed. **No text lost a tag, and no text has two tradition tags.**

**Dev tradition by category (texts, Vajrayāna / Mahāyāna / Theravāda / none):**

| Category | Texts | Dev before | Dev after | Chants before → after |
|---|---:|---|---|---|
| Liturgy (Chant and Recitation) | 784 | 694 / 0 / 58 / 32 | 700 / 1 / 62 / 21 | 771 → 774 |
| Tantras | 275 | 0 / 0 / 0 / 275 | 223 / 0 / 0 / 52 | 0 → 26 |
| Middle Way | 107 | 10 / 0 / 0 / 97 | 10 / 70 / 0 / 27 | 0 → 2 |
| Prajñāpāramitā | 58 | 9 / 0 / 0 / 49 | 9 / 45 / 0 / 4 | 9 → 9 |
| Miscellaneous | 41 | 0 / 0 / 0 / 41 | 1 / 15 / 0 / 25 | 0 → 35 |
| Mind-only Treatises | 39 | 0 / 0 / 0 / 39 | 0 / 22 / 0 / 17 | 0 → 0 |
| Systematic Treatises | 32 | 0 / 0 / 21 / 11 | 0 / 0 / 21 / 11 | 0 → 0 |
| Logic | 28 | 0 / 0 / 0 / 28 | 0 / 2 / 0 / 26 | 0 → 0 |
| Discourses | 26 | 0 / 0 / 0 / 26 | 0 / 13 / 0 / 13 | 0 → 0 |
| Monastic Rules | 16 | 0 / 0 / 0 / 16 | 0 / 0 / 0 / 16 | 0 → 1 |
| Mind Trainings and Advices | 15 | 0 / 0 / 0 / 15 | 0 / 3 / 0 / 12 | 0 → 0 |
| Linguistics Treatises | 10 | 0 / 0 / 0 / 10 | 0 / 0 / 0 / 10 | 0 → 0 |
| History and Stories | 1 | 0 / 0 / 0 / 1 | 0 / 0 / 0 / 1 | 0 → 0 |
| Uncategorised (probe record) | 1 | 0 / 0 / 0 / 1 | 0 / 0 / 0 / 1 | 0 → 0 |
| Only on dev (not in the prod audit) | 12 | 0 / 4 / 8 / 0 | 0 / 4 / 8 / 0 | 6 → 6 |
| **Total** | **1,445** | **713 / 4 / 87 / 641** | **943 / 175 / 91 / 236** | **786 → 853** |

**How dev differs from prod:**
- 1,433 texts exist on both.
- 197 prod texts are not on dev. These are mostly English and Tibetan Liturgy and Discourses texts.
- 12 dev texts are not on prod. They're Pali Abhidhamma commentaries and a few sūtras and dhāraṇīs, each already tagged and left as they are.
- On texts that exist on both, the tags were the same as prod's for all but 12.

**The 19 additions not made on dev.** All are Vajrayāna, on Tibetan Liturgy texts that exist on prod but not on dev under the same IDs:
- Taklung practice texts and supplications
- lineage and long-life prayers
- the Mañjuśrī-nāma-saṃgīti
- Sitātapatrā, Siṃhamukhā and Cakrasaṃvara texts
- an Akṣobhya rite

The same texts may exist on dev under other IDs. They'll be tagged on prod when the plan is applied there.

**To check dev again later** (read-only, from the `Webuddhist-library` folder), use the dev plan `final/tag_plan_dev.tsv`, which is `tag_plan.tsv` without the 19 lines. It should report `0 additions, 448 already done, 0 blocked, 0 errors`:

```bash
BASE=https://library.webuddhist.com zsh docs/tag-audit/apply_tag_plan.sh --plan final/tag_plan_dev.tsv
```

The full 467-line plan reports the 19 missing texts as errors on dev, and the script then refuses to apply. That's expected: those texts don't exist there.

## 3. Tag IDs

| Tag | Tag ID | Type | Used by this plan |
|---|---|---|---|
| **Theravāda** | `296WZOr8MXWteSqBBI7pl` | tradition | adds |
| **Mahāyāna** | `zjHEBN2egpftHD7BRMQko` | tradition | adds |
| **Vajrayāna** | `ZPcgMZgVJxAMQ7rpfW72a` | tradition | adds |
| **Chants** | `FZ5STsdU0eLvo7Nb2CpvH` | chant or recitation | adds |
| **Kangyur** | `reYrCR4TFKpHbU8V2wH6d` | collection | not used or changed (on 48 works, 107 texts) |

**Tags live on the work, not the text.** A translation shares its root text's work, and the API (`POST /v2/texts/{id}/tags/{tag}`) tags the whole work. Each addition is made once, on the root text, and all its translations receive the tag automatically. The API merges tags, so a tag can never be duplicated.

## 4. Totals by tag

**Texts:**

| Tag | Tag ID | Today | Added by the plan | After the plan |
|---|---|---:|---:|---:|
| Vajrayāna | `ZPcgMZgVJxAMQ7rpfW72a` | 710 | +267 | **977** |
| Mahāyāna | `zjHEBN2egpftHD7BRMQko` | 88 | +171 | **259** |
| Theravāda | `296WZOr8MXWteSqBBI7pl` | 110 | +4 | **114** |
| No tradition tag | — | 722 | −442 | **280** |
| **Total texts** | | 1,630 | | 1,630 |
| Chants | `FZ5STsdU0eLvo7Nb2CpvH` | 968 | +67 | **1,035** |

**Works** (an original text with its translations):

| Tag | Today | Added by the plan | After the plan |
|---|---:|---:|---:|
| Vajrayāna | 127 | +246 | **373** |
| Mahāyāna | 39 | +152 | **191** |
| Theravāda | 65 | +2 | **67** |
| No tradition tag | 646 | −400 | **246** |
| **Total works** | 877 | | 877 |
| Chants | 254 | +67 | **321** |

## 5. Tags by category

**Tradition tags (texts):**

| Category | Texts | Today: Vajra / Mahā / Thera / none | Added: Vajrayāna `ZPcg…` / Mahāyāna `zjHE…` / Theravāda `296W…` | After: Vajra / Mahā / Thera / none |
|---|---:|---|---|---|
| Liturgy (Chant and Recitation) | 878 | 696 / 9 / 60 / 113 | +43 / +1 / +4 | 739 / 10 / 64 / 65 |
| Tantras | 275 | 0 / 0 / 0 / 275 | +223 / — / — | 223 / 0 / 0 / 52 |
| Discourses | 110 | 0 / 68 / 16 / 26 | — / +13 / — | 0 / 81 / 16 / 13 |
| Middle Way | 107 | 10 / 0 / 0 / 97 | — / +70 / — | 10 / 70 / 0 / 27 |
| Prajñāpāramitā | 66 | 4 / 9 / 4 / 49 | — / +45 / — | 4 / 54 / 4 / 4 |
| Miscellaneous | 41 | 0 / 0 / 0 / 41 | +1 / +15 / — | 1 / 15 / 0 / 25 |
| Systematic Treatises | 41 | 0 / 0 / 30 / 11 | — / — / — | 0 / 0 / 30 / 11 |
| Mind-only Treatises | 39 | 0 / 0 / 0 / 39 | — / +22 / — | 0 / 22 / 0 / 17 |
| Logic | 28 | 0 / 0 / 0 / 28 | — / +2 / — | 0 / 2 / 0 / 26 |
| Monastic Rules | 16 | 0 / 0 / 0 / 16 | — / — / — | 0 / 0 / 0 / 16 |
| Mind Trainings and Advices | 15 | 0 / 0 / 0 / 15 | — / +3 / — | 0 / 3 / 0 / 12 |
| Linguistics Treatises | 10 | 0 / 0 / 0 / 10 | — / — / — | 0 / 0 / 0 / 10 |
| History and Stories | 3 | 0 / 2 / 0 / 1 | — / — / — | 0 / 2 / 0 / 1 |
| Uncategorised (probe record) | 1 | 0 / 0 / 0 / 1 | — / — / — | 0 / 0 / 0 / 1 |
| **Total** | **1,630** | **710 / 88 / 110 / 722** | **+267 / +171 / +4** | **977 / 259 / 114 / 280** |

**Chants tag `FZ5STsdU0eLvo7Nb2CpvH` (texts):**

| Category | Chants (identified) | Already have Chants | Added by the plan | Not chants | Carry Chants after | Kept Chants on non-chants |
|---|---:|---:|---:|---:|---:|---:|
| Liturgy (Chant and Recitation) | 795 | 792 | +3 | 81 | 868 | 71 |
| Tantras | 26 | 0 | +26 | 249 | 26 | 0 |
| Discourses | 18 | 18 | — | 92 | 84 | 66 |
| Middle Way | 2 | 0 | +2 | 105 | 2 | 0 |
| Prajñāpāramitā | 15 | 15 | — | 51 | 17 | 2 |
| Miscellaneous | 35 | 0 | +35 | 6 | 35 | 0 |
| Systematic Treatises | 0 | 0 | — | 41 | 0 | 0 |
| Mind-only Treatises | 0 | 0 | — | 39 | 0 | 0 |
| Logic | 0 | 0 | — | 28 | 0 | 0 |
| Monastic Rules | 1 | 0 | +1 | 15 | 1 | 0 |
| Mind Trainings and Advices | 0 | 0 | — | 15 | 0 | 0 |
| Linguistics Treatises | 0 | 0 | — | 10 | 0 | 0 |
| History and Stories | 0 | 0 | — | 3 | 2 | 2 |
| Uncategorised (probe record) | — | — | — | — | 0 | 0 |
| **Total** | **892** | **825** | **+67** | **735** | **1,035** | **141** |

Three test records (2 in Liturgy, 1 uncategorised) are neither chants nor non-chants and are left alone. "Kept Chants on non-chants" means texts that read as not recited but already carry Chants. Existing tags are not touched, so those tags stay and are listed for review.

## 6. Tags by language (texts)

| Language | Texts | Today: Vajra / Mahā / Thera / none | After: Vajra / Mahā / Thera / none | Carry Chants after |
|---|---:|---|---|---:|
| Tibetan (bo) | 809 | 122 / 38 / 10 / 639 | 367 / 190 / 10 / 242 | 279 |
| English (en) | 222 | 109 / 43 / 15 / 55 | 129 / 52 / 15 / 26 | 201 |
| Hindi (hi) | 131 | 93 / 1 / 30 / 7 | 93 / 1 / 32 / 5 | 130 |
| Chinese (zh) | 120 | 104 / 2 / 0 / 14 | 106 / 11 / 0 / 3 | 106 |
| Mongolian (mn) | 93 | 92 / 1 / 0 / 0 | 92 / 1 / 0 / 0 | 93 |
| Nepali (ne) | 93 | 92 / 1 / 0 / 0 | 92 / 1 / 0 / 0 | 93 |
| Vietnamese (vi) | 93 | 92 / 1 / 0 / 0 | 92 / 1 / 0 / 0 | 93 |
| Pali (pi) | 61 | 0 / 0 / 55 / 6 | 0 / 0 / 57 / 4 | 36 |
| Sanskrit (sa) | 4 | 2 / 1 / 0 / 1 | 2 / 2 / 0 / 0 | 2 |
| Marathi (mr) | 2 | 2 / 0 / 0 / 0 | 2 / 0 / 0 / 0 | 0 |
| Thai (th) | 1 | 1 / 0 / 0 / 0 | 1 / 0 / 0 / 0 | 1 |
| Classical Chinese (lzh) | 1 | 1 / 0 / 0 / 0 | 1 / 0 / 0 / 0 | 1 |
| **Total** | **1,630** | **710 / 88 / 110 / 722** | **977 / 259 / 114 / 280** | **1,035** |

## 7. What the plan adds, in detail

**Tradition tags (400 works, 442 texts):**

| Tag | Tag ID | Works | Texts | Mainly |
|---|---|---:|---:|---|
| Vajrayāna | `ZPcgMZgVJxAMQ7rpfW72a` | 246 | 267 | Tantras (223 texts): sādhanas, tantric rites, tantra commentaries; and 43 Liturgy texts |
| Mahāyāna | `zjHEBN2egpftHD7BRMQko` | 152 | 171 | Middle Way 70, Prajñāpāramitā 45, Mind-only 22, Miscellaneous 15 (praises), Discourses 13 |
| Theravāda | `296WZOr8MXWteSqBBI7pl` | 2 | 4 | The Pali Buddha Vandanā and Mora Paritta, with their Hindi translations |

Tradition tags go **only to works that have no tradition tag today**, so none can create a second one. Every addition rests on a high-confidence reading of the text's own content. For 396 of the 400 works, the earlier independent audit agrees as well.

**Chants tags (67 works, 67 texts):**

| Kind | Works |
|---|---:|
| Praises | 44 |
| Prayers and aspirations | 11 |
| Recitation liturgies: Medicine Buddha reading rites, a ransom rite, a Mahākāla torma invocation, a short verse sādhana | 7 |
| Rites written as the words to recite: bodhicitta, novice precepts, confession, Seven Tathāgatas reading | 4 |
| Dhāraṇī | 1 |

59 of these come from the first reading. The other 8 come from the second, closer reading of the borderline texts (section 9).

## 8. What does not change

- **All 908 existing tradition tags stay as they are.** 362 are confirmed by the content. The other 546 are kept although this audit could not confirm them, since existing tags are out of scope.
- **All 968 existing Chants tags stay**, including 141 texts (50 works) whose content reads as not recited:

  | Kind | Works |
  |---|---:|
  | Sūtras, mostly long Kangyur sūtras | 31 |
  | Tārā-praise commentaries | 7 |
  | Instruction texts | 5 |
  | Narrative texts | 4 |
  | Ritual manuals | 2 |
  | Treatise | 1 |

- **The Kangyur tag is untouched.**
- **No records and no test records are deleted.**

## 9. How decisions were made

1. **Every work's content was read.** For all 877 works, independent readers read the opening and a middle passage of the text itself. They didn't see existing tags or the earlier audit's verdict.
2. **A tradition tag is added only when:**
   - the reading shows exactly one tradition with high confidence;
   - the earlier, independent audit (if it judged the work) agrees;
   - and the earlier audit did not doubt it.

   Language, author, collection or position in the canon alone never decides. Where both sources judged a work, they agree on 572 of 584 works.
3. **Chants:** a text is a chant when, as written, it's the words people recite: prayers, praises, dhāraṇīs, recitation liturgies, verse sādhanas, auspicious verses, refuge and precept formulas, and short liturgical sūtras. It isn't a chant when it's mainly something else: a ritual or sādhana manual (instructions about a rite, with recitations embedded), a long doctrinal or narrative sūtra, a commentary, treatise, teaching song or instruction.
4. **The 212 borderline chant texts** (137 works) got a second, closer reading of their opening, middle and end. Where the readers split, or where texts in the same family were decided differently, I read the content and decided each family once, for 17 works:
   - **Chant:** the four editions of the Medicine Buddha reading rite.
   - **Not a chant:** three Vajravidāraṇa-cycle ritual manuals, and six short canonical realisation songs.

   Result: 22 chants (8 newly tagged) and 113 not chants. The 2 test works stay undecided.

## 10. Texts left without a tradition tag (280)

| Reason | Texts | Rule |
|---|---:|---|
| Content too short or generic, or not tradition-specific (e.g. medicine, grammar, divination) | 93 | 3 |
| Belongs to more than one tradition | 59 | 2 |
| Tradition likely, but only medium or low confidence | 120 | 1 and 3 |
| Reading and earlier audit disagree | 5 | 1 |
| Test or probe records (kept, not deleted) | 3 | — |

## 11. Dry run and tests

A **dry run** is a rehearsal against the live server. It reads the current tags and reports exactly what would be added, without changing anything.

| Check | Result |
|---|---|
| Prod dry run, 2026-11-09 16:24 (the 459 additions planned at that time) | all 459 pending; 0 already done, 0 blocked, 0 errors; nothing changed. Log: `final/apply_log_20261109_162441_dryrun.txt` |
| The 8 Chants additions from the second reading | **not yet dry-run on prod.** Run the prod dry run before applying there, so all 467 are checked against the live library. On dev they were applied and verified (section 2). |
| Offline check of the full 467-addition plan against the saved library export (2026-11-09) | **16 / 16 pass**: add-only; every line on a root text; tag names match IDs; no duplicates; no tag already present; no tradition added to a work that has one; every addition supported; no text with two tradition tags; no existing tag lost; every chant tagged; plan, CSVs and dry-run list agree |
| Local mock of the API on this machine only, same script, full 467-addition plan (2026-11-09 17:06) | dry run listed 467 additions and made 0 changes; a removal line was refused; a second tradition tag was blocked; applying sent exactly 467 additions (POST only) with 0 failures; a rerun found 0 to do and 467 already done; no existing tag lost; end state matched the plan exactly |
| Dev dry run before upload, 2026-11-10 (dev plan, 448 additions) | 448 pending; 0 already done, 0 blocked, 0 errors |
| Dev upload, 2026-11-10 | 448 added, 0 failed. Log: `final/apply_log_20261110_101628_apply.txt` |
| Dev dry run after upload, 2026-11-10 | `0 additions, 448 already done, 0 blocked, 0 errors`; no text lost a tag; no text has two tradition tags. Log: `final/apply_log_20261110_102701_dryrun.txt` |

## 12. Verdicts

| Check | Verdict |
|---|---|
| Rule 1: one tradition only when clear | **PASS**: all 400 additions are high-confidence and supported |
| Rule 2: multiple traditions left untagged, never two tags | **PASS**: 0 texts with two tradition tags; no tag on multi-tradition texts |
| Rule 3: Chant and Recitation texts tagged only when clear | **PASS**: 48 Liturgy texts added, all clear; unclear ones left as is |
| Every text audited, untagged exceptions justified | **PASS**: 1,630 / 1,630, with 280 justified exceptions |
| Chants identified | **PASS**: 892 chants, 735 not chants; only 3 test records undecided |
| Every identified chant has Chants | **PASS** once the plan is applied (67 missing today) |
| No new decision assigns multiple tradition tags | **PASS** |
| Existing tags untouched | **PASS**: the plan and the script are add-only |
| Counts reconcile across plan, CSVs, dry-run list and script | **PASS** (467 additions everywhere) |

## 13. Open items for review (do not block applying)

| Item | Works | Texts |
|---|---:|---:|
| Suspected wrong existing tradition tags: content and the earlier audit both show Mahāyāna, but the tag is Vajrayāna. Kept, as instructed. | 19 | 109 |
| Existing Chants on texts that read as not recited. Kept. | 50 | 141 |
| Existing tradition tags this audit couldn't confirm. Kept. | — | 546 |
| Hindi `RC3AY0IJsCQzTMm7LddnJ` is titled "Pañcasīla Yācanā" but its text is the forgiveness request (Khamā Yācanā). Same tags either way, so it's a content fix. | 1 | 1 |

Also: the readings were done by AI readers from excerpts, and no person has checked them. A reviewer spot check is recommended before applying.

## 14. How to apply (run by the library owner)

**Dev:** applied and verified on 2026-11-10 (section 2). To recheck it, run the dev command in section 2.

**Prod:** not applied yet. When the reviewer approves:

1. **Dry run** (read-only):

   ```bash
   BASE=https://library-prod.webuddhist.com zsh docs/tag-audit/apply_tag_plan.sh
   ```

   It should report `467 additions, 0 already done, 0 blocked, 0 errors`.
2. **Apply.** Run the same command with `--apply`. The script asks for the API key (unless `LIBRARY_API_KEY` is set), shows the 467 additions and waits for you to type `APPLY`. After each one it checks that the tag is present and every earlier tag is still there.
3. **Confirm.** Run the dry run again. It should report `0 additions, 467 already done`.

To apply one tag at a time, add `--only Vajrayana`, `--only Mahayana`, `--only Theravada` or `--only Chants`.

`apply_tag_plan.sh` is the only apply script. Don't run `tools/tibetan_audit.py merge`, which would write the old pre-audit scripts again.

## 15. Files (in `final/`)

| File | Contents |
|---|---|
| `VERIFICATION_REPORT.md` | This report |
| `audit_all_texts.csv` | All 1,630 texts: ID, title, language, work, category, existing and final tradition, action, reason, chant decision, Chants now and planned, evidence, status |
| `tradition_tag_changes.csv` | The 400 tradition additions, with affected text IDs and evidence |
| `chant_tag_changes.csv` | The 67 Chants additions, with evidence |
| `skipped_and_disputed.csv` | Every untagged or questionable case, with its reason |
| `tag_plan.tsv` | The 467 additions the script applies, one per line, with tag IDs |
| `dry_run.txt` | Every record that changes, including translations affected through their work |
| `readings.json` | The content readings of every work, the second chant reading, and both briefs |
| `tag_plan_dev.tsv` | The dev plan: the 448 lines of `tag_plan.tsv` whose texts exist on dev |
| `apply_log_20261109_162441_dryrun.txt` | Output of the prod dry run (2026-11-09) |
| `apply_log_20261110_101628_apply.txt` | Output of the dev upload (2026-11-10): every addition, with OK or FAIL |
| `apply_log_20261110_102701_dryrun.txt` | Output of the dev dry run after the upload (2026-11-10) |

To rebuild the CSVs from a fresh read-only export:

```bash
AUDIT_CACHE=<export dir> python3 -I tools/final_audit.py
```
