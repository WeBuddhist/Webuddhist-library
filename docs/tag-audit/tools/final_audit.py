"""Final tradition and chant audit for every text in the library (merge step).

The library stores tags on the WORK: a translation shares its root text's work (routers/texts.py tag_text ->
db.text.get_work_id; database/text_database.py CREATE_TRANSLATION_QUERY), so a translation always carries exactly its
root's tags. Every decision here is made per work and applies to all texts of that work.

Evidence per work:
  R  a blind reading of the work's own content (final/readings.json; readers saw content, titles and catalogue
     position, but not the existing tags or the earlier audit's verdict)
  P  the earlier, independent audit verdict (tibetan_audit.csv VERIFIED rows; tibetan_incorrect_tags.csv), if any
  E  the existing tradition tag on prod

Tradition rules:
  * Tag X only when R says X with high confidence, P (if any) also says X, and the earlier audit did not explicitly
    doubt the work                                                                            -> add / verified
  * R says MULTIPLE                                                                           -> skipped (multiple)
  * R says UNCERTAIN, or confidence below high, or R and P disagree                           -> skipped (insufficient)
  * EXISTING TAGS ARE NEVER TOUCHED (reviewer instruction, 2026-11-09): nothing is removed or replaced. Where content and
    the earlier audit both show another tradition, the work is listed as a suspected wrong tag for manual review.
    An existing tag E is otherwise kept
    and the work is flagged for review. Nothing is removed for uncertainty alone.
Chant rules:
  * R chant yes -> must have Chants (add if missing).
  * R chant no and kind commentary or treatise -> a Chants tag there is clearly wrong (remove).
  * R chant no of another kind, or review -> unchanged, flagged for review.

Inputs: AUDIT_CACHE (texts.json, categories.json from `tibetan_audit.py export`) and final/readings.json (the blind
readings of every work, with the brief the readers followed).
Outputs go to docs/tag-audit/final/. Nothing touches the server.
"""
import collections
import csv
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "final"
CACHE = Path(os.environ["AUDIT_CACHE"])
TAGS = {"Theravada": "296WZOr8MXWteSqBBI7pl", "Mahayana": "zjHEBN2egpftHD7BRMQko", "Vajrayana": "ZPcgMZgVJxAMQ7rpfW72a"}
NAME = {v: k for k, v in TAGS.items()}
CHANTS = "FZ5STsdU0eLvo7Nb2CpvH"
CLEARLY_NOT_RECITED = {"commentary", "treatise"}  # the kinds the requirement names as clearly not recited


def title(t):
    tt = t.get("title") or {}
    return tt.get(t["language"]) or tt.get("bo") or tt.get("en") or next(iter(tt.values()), "")


def main():
    texts = json.loads((CACHE / "texts.json").read_text())
    by = {t["id"]: t for t in texts}
    cats = {}

    def walk(cs):
        for c in cs:
            cats[c["id"]] = (c.get("title") or {}).get("en", c["id"]); walk(c.get("children") or [])
    walk(json.loads((CACHE / "categories.json").read_text()))

    readings = {}
    rdoc = json.loads((OUT / "readings.json").read_text())
    for r in rdoc["readings"]:
        readings[r["work_id"]] = r
    # Second, closer chant reading of the works the first reading left borderline (longer excerpts: opening, middle, end).
    second = {r["work_id"]: r for r in rdoc.get("chant_review", [])}
    prior = {}
    for x in csv.DictReader(open(HERE / "tibetan_audit.csv", encoding="utf-8")):
        if x["status"] == "VERIFIED" and x["tradition"] in TAGS:
            prior[x["text_id"]] = (x["tradition"], f"earlier audit ({x['method']}, {x['confidence']}): {x['evidence'][:160]}")
    for x in csv.DictReader(open(HERE / "tibetan_incorrect_tags.csv", encoding="utf-8")):
        prior.setdefault(x["text_id"], (x["tradition"], f"earlier audit: {x['evidence'][:160]}"))
    # Works the earlier audit read and explicitly doubted (not merely "not read yet"): its doubt counts as conflicting evidence.
    doubt = {x["text_id"]: x["unverified_reason"] for x in csv.DictReader(open(HERE / "tibetan_audit.csv", encoding="utf-8"))
             if x["status"] == "UNVERIFIED" and x["unverified_reason"] and not x["unverified_reason"].startswith("not read")}

    def root(t):
        seen = set()
        while t.get("translation_of") in by and t["id"] not in seen:
            seen.add(t["id"]); t = by[t["translation_of"]]
        return t
    works = collections.defaultdict(list)
    for t in texts:
        works[root(t)["id"]].append(t)

    decisions, plan = {}, []
    for wid, members in works.items():
        w = by[wid]
        trads = sorted({NAME[i] for m in members for i in m["tag_ids"] if i in NAME})
        E = trads[0] if len(trads) == 1 else ("+".join(trads) if trads else "")
        H = any(CHANTS in m["tag_ids"] for m in members)
        R = readings.get(wid)
        P = prior.get(wid)
        d = {"existing": E, "has_chants": H, "reading": R, "prior": P}
        # ---------- tradition
        if R is None:
            final, t_status, t_action, why = E, "Skipped for Review", "none", "no reading of this work"
        elif R.get("is_test"):
            final, t_status, t_action, why = E, "Skipped for Review", "none", "test or probe record (kept, not tagged)"
        else:
            rt, conf = R["tradition"], R["confidence"]
            doubted = wid in doubt and f"leans {rt}" not in doubt[wid]
            clear = rt in TAGS and conf == "high" and (P is None or P[0] == rt) and not doubted
            if rt in TAGS and conf == "high" and doubted and P is None:
                P = (f"doubtful ({doubt[wid][:120]})", f"earlier audit doubted it: {doubt[wid][:160]}")
                d["prior"] = P
            if len(trads) > 1:
                final, t_status, t_action, why = E, "Skipped for Review", "none", "has more than one tradition tag"
            elif clear and E == rt:
                final, t_status, t_action, why = rt, "Verified", "none", "existing tag confirmed by content"
            elif clear and not E:
                final, t_status, t_action, why = rt, "Corrected", "add", "missing tag; content shows one tradition"
            elif clear and E and P is not None and P[0] == rt:
                final, t_status, t_action, why = E, "Skipped for Review", "none", f"suspected wrong tag: content and earlier audit both show {rt}, existing {E} kept (existing tags are not touched)"
            elif clear and E:
                final, t_status, t_action, why = E, "Skipped for Review", "none", f"content reads as {rt} but existing tag is {E}; no second source, tag kept"
            elif rt == "MULTIPLE":
                final, t_status, t_action, why = E, "Skipped for Review", "none", "multiple traditions" + (f"; existing {E} kept for review" if E else "")
            elif rt in TAGS and conf == "high":
                final, t_status, t_action, why = E, "Skipped for Review", "none", f"reading says {rt}, earlier audit says {P[0]}" + (f"; existing {E} kept" if E else "")
            else:
                final, t_status, t_action, why = E, "Skipped for Review", "none", f"insufficient evidence ({rt}, {conf})" + (f"; existing {E} kept, not confirmed" if E else "")
        d.update(final=final, t_status=t_status, t_action=t_action, t_why=why)
        # ---------- chant
        if R is None or R.get("is_test"):
            c_dec, c_status, c_action, c_why = "review", "Skipped for Review", "none", "not read" if R is None else "test record"
        else:
            c, kind = R["chant"], (R.get("chant_kind") or "").lower()
            via = ""
            if c == "review" and wid in second:
                s2 = second[wid]
                c, kind, via = s2["chant"], s2["form"].lower(), f" (second reading, {s2['confidence']})"
                R = {**R, "chant": c, "chant_kind": s2["form"], "chant_evidence": f"second reading ({s2['confidence']}): {s2['evidence']}"}
                d["reading"] = R
            if c == "yes":
                c_dec, c_status, c_action, c_why = "yes", ("Verified" if H else "Corrected"), ("none" if H else "add"), f"{kind}: recited text{via}"
            elif c == "no" and not H:
                c_dec, c_status, c_action, c_why = "no", "Verified", "none", f"{kind}: not recited{via}"
            elif c == "no" and kind in CLEARLY_NOT_RECITED:
                c_dec, c_status, c_action, c_why = "no", "Skipped for Review", "none", f"{kind}: not recited{via}, but carries Chants; existing tag kept (existing tags are not touched)"
            elif c == "no":
                c_dec, c_status, c_action, c_why = "no", "Skipped for Review", "none", f"{kind}: reads as not recited{via}, but carries Chants; not clearly wrong, kept"
            else:
                c_dec, c_status, c_action, c_why = "review", "Skipped for Review", "none", f"{kind}: borderline" + ("; Chants kept" if H else "")
        d.update(chant=c_dec, c_status=c_status, c_action=c_action, c_why=c_why)
        decisions[wid] = d
        # ---------- plan (one operation per work, on its original text; it applies to every translation)
        if t_action == "add":
            plan.append(("add", wid, TAGS[final], final, why))
        if t_action == "replace":
            plan.append(("remove", wid, TAGS[E], E, why)); plan.append(("add", wid, TAGS[final], final, why))
        if c_action == "add":
            plan.append(("add", wid, CHANTS, "Chants", c_why))
        if c_action == "remove":
            plan.append(("remove", wid, CHANTS, "Chants", c_why))

    OUT.mkdir(exist_ok=True)
    rows = []
    for wid, members in works.items():
        d, R = decisions[wid], decisions[wid]["reading"] or {}
        for m in sorted(members, key=lambda m: (m["id"] != wid, m["language"], m["id"])):
            st = "Skipped for Review" if "Skipped for Review" in (d["t_status"], d["c_status"]) else ("Corrected" if "Corrected" in (d["t_status"], d["c_status"]) else "Verified")
            ev = R.get("evidence", "")
            if d["prior"]:
                ev += f" | {d['prior'][1]}"
            if m["id"] != wid:
                ev = f"translation of {wid} (same work, shares its tags; title match: {R.get('translations_match')}) | " + ev
            rows.append({"text_id": m["id"], "title": title(m), "language": m["language"],
                         "kind": "translation" if m["id"] != wid else ("commentary" if m.get("commentary_of") else "root"),
                         "work_id": wid, "category": cats.get(by[wid].get("category_id"), by[wid].get("category_id") or ""),
                         "existing_tradition": d["existing"], "final_tradition": d["final"], "tradition_action": d["t_action"],
                         "tradition_status": d["t_status"], "tradition_reason": d["t_why"],
                         "reading": f"{R.get('tradition', '')} ({R.get('confidence', '')})" if R else "",
                         "is_chant": d["chant"], "chant_kind": R.get("chant_kind", ""), "has_chants_tag": "yes" if d["has_chants"] else "no",
                         "chants_action": d["c_action"], "chant_status": d["c_status"], "chant_reason": d["c_why"],
                         "evidence": ev, "chant_evidence": R.get("chant_evidence", ""), "status": st,
                         "proposed_status_note": "proposed, not applied" if st == "Corrected" else ""})
    rows.sort(key=lambda r: (r["work_id"] != r["text_id"], r["category"], r["language"], r["text_id"]))
    rows.sort(key=lambda r: (r["category"], r["work_id"], r["kind"] == "translation"))

    def write(name, data, cols=None):
        cols = cols or list(data[0])
        with open(OUT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(data)
    write("audit_all_texts.csv", rows)
    n_members = {wid: len(ms) for wid, ms in works.items()}
    ids_of = {wid: " ".join(m["id"] for m in ms) for wid, ms in works.items()}
    tchg = [{"work_id": wid, "title": title(by[wid]), "language": by[wid]["language"], "texts_affected": n_members[wid], "affected_text_ids": ids_of[wid],
             "action": d["t_action"], "remove_tag": d["existing"] if d["t_action"] == "replace" else "",
             "add_tag": d["final"], "reason": d["t_why"], "evidence": (d["reading"] or {}).get("evidence", "") + (f" | {d['prior'][1]}" if d["prior"] else "")}
            for wid, d in decisions.items() if d["t_action"] in ("add", "replace")]
    write("tradition_tag_changes.csv", sorted(tchg, key=lambda r: (r["action"], r["add_tag"], r["work_id"])),
          ["work_id", "title", "language", "texts_affected", "affected_text_ids", "action", "remove_tag", "add_tag", "reason", "evidence"])
    cchg = [{"work_id": wid, "title": title(by[wid]), "language": by[wid]["language"], "texts_affected": n_members[wid], "affected_text_ids": ids_of[wid],
             "action": d["c_action"], "chant_kind": (d["reading"] or {}).get("chant_kind", ""), "reason": d["c_why"],
             "evidence": (d["reading"] or {}).get("chant_evidence", "")}
            for wid, d in decisions.items() if d["c_action"] in ("add", "remove")]
    write("chant_tag_changes.csv", sorted(cchg, key=lambda r: (r["action"], r["work_id"])),
          ["work_id", "title", "language", "texts_affected", "affected_text_ids", "action", "chant_kind", "reason", "evidence"])
    skip = []
    for wid, d in decisions.items():
        for kind, st, why in (("tradition", d["t_status"], d["t_why"]), ("chant", d["c_status"], d["c_why"])):
            if st == "Skipped for Review":
                grp = ("multiple traditions" if "multiple traditions" in why else "suspected wrong tag (kept)" if "suspected wrong tag" in why
                       else "disputed existing tag" if "content reads" in why
                       else "sources disagree" if "earlier audit says" in why else "test record" if "test" in why
                       else "more than one tag" if "more than one" in why else "insufficient evidence") if kind == "tradition" else ("chant: borderline" if d["chant"] == "review" else "chant: Chants on a commentary (kept)" if "existing tags are not touched" in why else "chant: Chants tag not clearly wrong")
                skip.append({"work_id": wid, "title": title(by[wid]), "language": by[wid]["language"], "texts_affected": n_members[wid],
                             "category": cats.get(by[wid].get("category_id"), ""), "issue": kind, "group": grp,
                             "existing_tradition": d["existing"], "reading": f"{(d['reading'] or {}).get('tradition', '')} ({(d['reading'] or {}).get('confidence', '')})",
                             "earlier_audit": d["prior"][0] if d["prior"] else "", "reason": why,
                             "evidence": (d["reading"] or {}).get("evidence" if kind == "tradition" else "chant_evidence", "")})
    write("skipped_and_disputed.csv", sorted(skip, key=lambda r: (r["issue"], r["group"], r["category"], r["work_id"])),
          ["work_id", "title", "language", "texts_affected", "category", "issue", "group", "existing_tradition", "reading", "earlier_audit", "reason", "evidence"])
    with open(OUT / "tag_plan.tsv", "w", encoding="utf-8") as f:
        f.write("# action\ttext_id\ttag_id\ttag_name\treason  (one line per work; tags live on the work, so each line covers all its translations)\n")
        for a, i, g, n, why in plan:
            f.write(f"{a}\t{i}\t{g}\t{n}\t{why}\n")
    with open(OUT / "dry_run.txt", "w", encoding="utf-8") as f:
        f.write("Dry run from the local export: every record that changes if tag_plan.tsv is applied.\n\n")
        for a, i, g, n, why in plan:
            f.write(f"{a.upper():6} {n:9} on work {i} ({by[i]['language']}) {title(by[i])[:70]}\n")
            for m in works[i]:
                if m["id"] != i:
                    f.write(f"         also on translation {m['id']} ({m['language']}) {title(m)[:60]}\n")

    # ---------- checks and summary
    C = collections.Counter
    final_state = {}
    for wid, d in decisions.items():
        tags = {i for m in works[wid] for i in m["tag_ids"]}
        for a, i, g, n, _ in plan:
            if i == wid:
                tags.discard(g) if a == "remove" else tags.add(g)
        final_state[wid] = tags
    multi = [w for w, tg in final_state.items() if sum(t in NAME for t in tg) > 1]
    chants_missing = [w for w, d in decisions.items() if d["chant"] == "yes" and CHANTS not in final_state[w]]
    summary = {
        "texts": len(texts), "works": len(works), "works_read": sum(w in readings for w in works),
        "texts_by_tradition_status": dict(C(r["tradition_status"] for r in rows)),
        "texts_by_chant_status": dict(C(r["chant_status"] for r in rows)),
        "texts_by_status": dict(C(r["status"] for r in rows)),
        "texts_verified_single_tradition": sum(r["tradition_status"] == "Verified" for r in rows),
        "texts_proposed_added_tradition": sum(r["tradition_action"] == "add" for r in rows),
        "texts_proposed_replaced_tradition": sum(r["tradition_action"] == "replace" for r in rows),
        "texts_skipped_multiple": sum(r["tradition_status"] != "Verified" and "multiple traditions" in r["tradition_reason"] for r in rows),
        "texts_skipped_insufficient": sum(r["tradition_status"] == "Skipped for Review" and "multiple traditions" not in r["tradition_reason"] and "content reads" not in r["tradition_reason"] and "suspected wrong tag" not in r["tradition_reason"] and "test" not in r["tradition_reason"] for r in rows),
        "texts_skipped_disputed": sum("content reads" in r["tradition_reason"] for r in rows),
        "texts_suspected_wrong_tag_kept": sum("suspected wrong tag" in r["tradition_reason"] for r in rows),
        "texts_chants_on_commentary_kept": sum("not recited, but carries Chants; existing" in r["chant_reason"] for r in rows),
        "texts_skipped_test": sum("test or probe" in r["tradition_reason"] for r in rows),
        "texts_identified_chants": sum(r["is_chant"] == "yes" for r in rows),
        "texts_chant_missing_tag_now": sum(r["is_chant"] == "yes" and r["has_chants_tag"] == "no" for r in rows),
        "texts_chant_tag_removals": sum(r["chants_action"] == "remove" for r in rows),
        "texts_chant_review": sum(r["chant_status"] == "Skipped for Review" for r in rows),
        "works_plan_ops": dict(C(f"{a} {n}" for a, _, _, n, _ in plan)),
        "check_multiple_tradition_after_plan": len(multi), "check_chants_missing_after_plan": len(chants_missing),
        "check_translations_title_mismatch": [w for w, d in decisions.items() if (d["reading"] or {}).get("translations_match") is False],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
