# Tradition and Chants Tagging: Main Report

**Library:** library-prod.webuddhist.com, application `webuddhist`
**Audit date:** 2026-11-09
**Scope:** all 1,630 texts (877 works) in 12 languages and 14 categories
**Status:** plan ready for review. **Not applied to production.** Nothing committed or pushed.

## 1. Summary

- Every text was audited for a tradition and for chant status.
- The plan **only adds tags**: **400 tradition tags** and **67 Chants tags**, 467 additions in all. They reach 438 works and 480 texts, because translations share their root's tags.
- **No existing tag is removed or changed.**
- After the plan:
  - **1,350 of 1,630 texts** carry a tradition tag. The other **280** stay untagged on purpose, because the rules don't allow a confident tag.
  - **All 892 texts identified as chants** carry the Chants tag.
- Every chant decision is made, except 3 test records that are deliberately left alone.

"Added by the plan" below means tags **not yet on the library**. They're added only when the plan is applied with `apply_tag_plan.sh --apply`. Until then the library stays at the "today" numbers.

## 2. Tag IDs

| Tag | Tag ID | Type | Used by this plan |
|---|---|---|---|
| **Theravāda** | `296WZOr8MXWteSqBBI7pl` | tradition | adds |
| **Mahāyāna** | `zjHEBN2egpftHD7BRMQko` | tradition | adds |
| **Vajrayāna** | `ZPcgMZgVJxAMQ7rpfW72a` | tradition | adds |
| **Chants** | `FZ5STsdU0eLvo7Nb2CpvH` | chant or recitation | adds |
| **Kangyur** | `reYrCR4TFKpHbU8V2wH6d` | collection | not used or changed (on 48 works, 107 texts) |

**Tags live on the work, not the text.** A translation shares its root text's work, and the API (`POST /v2/texts/{id}/tags/{tag}`) tags the whole work. Each addition is made once, on the root text, and all its translations receive the tag automatically. The API merges tags, so a tag can never be duplicated.

## 3. Totals by tag

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

## 4. Tags by category

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

## 5. Tags by language (texts)

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

## 6. What the plan adds, in detail

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

59 of these come from the first reading. The other 8 come from the second, closer reading of the borderline texts (section 8).

## 7. What does not change

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

## 8. How decisions were made

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

## 9. Texts left without a tradition tag (280)

| Reason | Texts | Rule |
|---|---:|---|
| Content too short or generic, or not tradition-specific (e.g. medicine, grammar, divination) | 93 | 3 |
| Belongs to more than one tradition | 59 | 2 |
| Tradition likely, but only medium or low confidence | 120 | 1 and 3 |
| Reading and earlier audit disagree | 5 | 1 |
| Test or probe records (kept, not deleted) | 3 | — |

## 10. Dry run and tests

A **dry run** is a rehearsal against the live server. It reads the current tags and reports exactly what would be added, without changing anything.

| Check | Result |
|---|---|
| Prod dry run, 2026-11-09 16:24 (the 459 additions planned at that time) | all 459 pending; 0 already done, 0 blocked, 0 errors; nothing changed. Log: `final/apply_log_20261109_162441_dryrun.txt` |
| The 8 Chants additions from the second reading | **not yet dry-run on prod.** By instruction, prod and dev were not contacted for this check. Run the dry run before applying, so all 467 are checked against the live library |
| Offline check of the full 467-addition plan against the saved library export (2026-11-09) | **16 / 16 pass**: add-only; every line on a root text; tag names match IDs; no duplicates; no tag already present; no tradition added to a work that has one; every addition supported; no text with two tradition tags; no existing tag lost; every chant tagged; plan, CSVs and dry-run list agree |
| Local mock of the API on this machine only, same script, full 467-addition plan (2026-11-09 17:06) | dry run listed 467 additions and made 0 changes; a removal line was refused; a second tradition tag was blocked; applying sent exactly 467 additions (POST only) with 0 failures; a rerun found 0 to do and 467 already done; no existing tag lost; end state matched the plan exactly |

## 11. Verdicts

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

## 12. Open items for review (do not block applying)

| Item | Works | Texts |
|---|---:|---:|
| Suspected wrong existing tradition tags: content and the earlier audit both show Mahāyāna, but the tag is Vajrayāna. Kept, as instructed. | 19 | 109 |
| Existing Chants on texts that read as not recited. Kept. | 50 | 141 |
| Existing tradition tags this audit couldn't confirm. Kept. | — | 546 |
| Hindi `RC3AY0IJsCQzTMm7LddnJ` is titled "Pañcasīla Yācanā" but its text is the forgiveness request (Khamā Yācanā). Same tags either way, so it's a content fix. | 1 | 1 |

Also: the readings were done by AI readers from excerpts, and no person has checked them. A reviewer spot check is recommended before applying.

## 13. How to apply (run by the library owner)

1. **Dry run** (read-only):

   ```bash
   BASE=https://library-prod.webuddhist.com zsh docs/tag-audit/apply_tag_plan.sh
   ```

   It should report `467 additions, 0 already done, 0 blocked, 0 errors`.
2. **Apply.** Run the same command with `--apply`. The script asks for the API key (unless `LIBRARY_API_KEY` is set), shows the 467 additions and waits for you to type `APPLY`. After each one it checks that the tag is present and every earlier tag is still there.
3. **Confirm.** Run the dry run again. It should report `0 additions, 467 already done`.

To apply one tag at a time, add `--only Vajrayana`, `--only Mahayana`, `--only Theravada` or `--only Chants`.

`apply_tag_plan.sh` is the only apply script. Don't run `tools/tibetan_audit.py merge`, which would write the old pre-audit scripts again.

## 14. Files (in `final/`)

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
| `apply_log_20261109_162441_dryrun.txt` | Output of the prod dry run |

To rebuild the CSVs from a fresh read-only export:

```bash
AUDIT_CACHE=<export dir> python3 -I tools/final_audit.py
```
