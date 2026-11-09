#!/usr/bin/env python3
"""Mahāyāna / Vajrayāna audit of Tibetan texts on the WeBuddhist Library (Option B: classify by teaching, not language).

Every network call is a read: GET requests to the library, BDRC and GitHub, and anonymous `git clone` of public repos.
Nothing here writes to the library. Downloads go to AUDIT_CACHE (outside the repo); results go to docs/tag-audit/.

    export AUDIT_CACHE=/tmp/some-dir SSL_CERT_FILE=/etc/ssl/cert.pem
    LIBRARY_API_KEY=... python3 -I -B tibetan_audit.py export     # prod texts, tags, categories
    python3 -I -B tibetan_audit.py bdrc                           # BDRC records, labels, titles, authors
    python3 -I -B tibetan_audit.py rails                          # tag_ids in public WeBuddhist-Data repos
    LIBRARY_API_KEY=... python3 -I -B tibetan_audit.py content    # opening text and term counts per text
    python3 -I -B tibetan_audit.py evidence                       # automatic verification + reading batches
    python3 -I -B tibetan_audit.py merge                          # audit sheet, lists and apply scripts

The API key is read from LIBRARY_API_KEY, only sent in the X-API-Key header, and never printed or saved.
"""
import collections
import concurrent.futures as cf
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AUDIT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("AUDIT_CACHE", "")).expanduser()
BASE = "https://library-prod.webuddhist.com"
APP, UA = "webuddhist", "tag-audit/1.0"
TAGS = {"Theravada": "296WZOr8MXWteSqBBI7pl", "Mahayana": "zjHEBN2egpftHD7BRMQko", "Vajrayana": "ZPcgMZgVJxAMQ7rpfW72a"}
NAME = {v: k for k, v in TAGS.items()}
OTHER = {"FZ5STsdU0eLvo7Nb2CpvH": "Chants", "reYrCR4TFKpHbU8V2wH6d": "Kangyur"}
CATEGORY_DEFAULT = {"Tantras": "Vajrayana", "Middle Way": "Mahayana", "Mind-only Treatises": "Mahayana", "Prajñāpāramitā": "Mahayana"}
SPAN, EXCERPT = 40000, 1500
# Roots outside the Tibetan audit whose prod tag conflicts with Option B. Their Tibetan commentaries follow the corrected
# tradition, and they are listed in tibetan_incorrect_tags.csv, but no script changes them.
OUTSIDE_CORRECTIONS = {
    "KFDP2GKsN0LhzLoJ621MM": {"tradition": "Mahayana", "evidence": "Vajracchedikā (Diamond Sūtra): a Prajñāpāramitā Mahāyāna sūtra; its Tibetan commentaries (Toh 3816-3817) sit in the Tengyur Prajñāpāramitā section"},
    "sADPH5uSrp0XOntZmPvSJ": {"tradition": "Mahayana", "evidence": "Bodhicaryāvatāra: Śāntideva's guide to the bodhisattva path, filed in Derge under Madhyamaka (Toh 3871)"},
}
V_TERMS = ["དཀྱིལ་འཁོར", "དབང་བསྐུར", "སྔགས", "རྡོ་རྗེ", "རྣལ་འབྱོར་མ", "ཧཱུྃ", "ཧཱུཾ", "བསྐྱེད་རིམ", "རྫོགས་རིམ", "ཡི་དམ", "དཔའ་བོ", "ཕྱག་རྒྱ"]
M_TERMS = ["བྱང་ཆུབ་སེམས་དཔ", "སྟོང་པ་ཉིད", "ཕ་རོལ་ཏུ་ཕྱིན", "ཐེག་པ་ཆེན་པོ", "དབུ་མ", "རྣམ་པར་རིག", "ས་བཅུ", "བྱང་ཆུབ་ཀྱི་སེམས"]
V_LABEL = re.compile(r"sgrub thabs|dkyil chog|dkyil 'khor|initiations into the mandala|song of the vajra|^tantra$|rdo rje theg pa|guhyasamaja|gsang 'dus|bde mchog|gshin rje gshed|rdo rje rnam 'joms|vajravid|phyag rdor|gos sngon|vajrapani|mgon po/|ku ru kul|rdo rje rnal 'byor|rdo rje mkha|gdan bzhi|rta mgrin|mi g\.yo|mi 'khrugs|pad\+ma 'byung gnas|bskyed rim|dbang bzhi|pañcakrama|aṅgayoga|hevajra|saṃpūṭi|bla ma'i rnal 'byor|tantric vow|empowerment|bdud rtsi 'khyil|nang sgrub|offering cakes|gtor|srung 'khor|mandala|vajrāsanavajragīti|caryāgīti|vignantaka|rnal 'byor ma|e ka dzA TI|las tshogs|bar chad lam sel", re.I)
M_LABEL = re.compile(r"middle way|great vehical|byang chub sems|bodhicitta|prajñāpāramitā|prajnaparamita|sher phyin|madhyamaka|śūnyatāsaptati|vigrahavyāvartan|yuktiṣaṣṭik|catuḥśataka|satyadvaya|two truths|^emptiness$|bodhipathapradīpa|mahāyāna|chos nyid rnam|yogācārabhūm|viṃśatikā|rnal 'byor spyod pa'i sa|rnam rig|sutra tradition|discipline of the bodhisattva|bodhisattvasaṃvara|yon tan rin chen sdud pa|vaidalya|akṣaraśataka|pratītyasamutpādahṛdaya|mind training|three main points", re.I)
# Derge sections by Tohoku number (from memory; checked against the library's categories, which follow them).
TENGYUR = ((1109, 1179, "Praises", ""), (1180, 3785, "Tantra", "Vajrayana"), (3786, 3823, "Prajnaparamita", "Mahayana"), (3824, 3980, "Madhyamaka", "Mahayana"), (3981, 4019, "Sutra commentary", "Mahayana"), (4020, 4085, "Cittamatra", "Mahayana"), (4086, 4098, "Abhidharma", ""), (4099, 4149, "Vinaya", ""), (4150, 4157, "Jataka", ""), (4158, 4202, "Letters", ""), (4203, 4268, "Pramana", ""), (4269, 4460, "Language and arts", ""), (4461, 5500, "Miscellaneous", ""))
KANGYUR = ((1, 7, "K Vinaya", ""), (8, 43, "K Prajnaparamita", "Mahayana"), (44, 93, "K Avatamsaka/Ratnakuta", "Mahayana"), (94, 359, "K Sutra", ""), (360, 844, "K Tantra", "Vajrayana"), (845, 1108, "K Dharani", ""))


# ---------------------------------------------------------------- helpers
def cache():
    if not str(CACHE) or str(CACHE) == ".":
        sys.exit("Set AUDIT_CACHE to a scratch directory.")
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE


def lib_headers():
    k = os.environ.get("LIBRARY_API_KEY", "")
    if not k:
        sys.exit("Set LIBRARY_API_KEY for library reads.")
    return {"X-API-Key": k, "X-Application": APP, "User-Agent": UA, "Accept": "application/json"}


def get(url, headers, timeout=120, tries=3, raw=False):
    """GET only. Returns (data, error)."""
    last = ""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method="GET"), timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
                return (body if raw else json.loads(body)), ""
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (401, 403, 404, 410, 422):
                break
        except Exception as e:  # network errors are retried
            last = type(e).__name__
        time.sleep(1 + attempt)
    return None, last


def jload(name):
    return json.loads((CACHE / name).read_text())


def jsave(name, data):
    (CACHE / name).write_text(json.dumps(data, ensure_ascii=False))


def tradition(t):
    found = [NAME[i] for i in (t or {}).get("tag_ids") or [] if i in NAME]
    return found[0] if len(found) == 1 else ("several" if found else "")


def owner(by, t):
    while t and t.get("translation_of") and t["translation_of"] in by:
        t = by[t["translation_of"]]
    return t


def title(t):
    tt = (t or {}).get("title") or {}
    return tt.get((t or {}).get("language", "")) or tt.get("bo") or tt.get("en") or next(iter(tt.values()), "")


def is_test(t):
    s = title(t)
    return "[TEST]" in s or "probe" in s.lower()


# ---------------------------------------------------------------- export
def cmd_export():
    cache()
    h = lib_headers()
    texts, offset = [], 0
    while True:
        body, err = get(f"{BASE}/v2/texts?limit=100&offset={offset}", h)
        if body is None:
            sys.exit(f"/v2/texts offset={offset} failed: {err}")
        texts += body["items"]
        if not body.get("has_more"):
            break
        offset += 100
    tags, _ = get(f"{BASE}/v2/tags", h)
    cats, _ = get(f"{BASE}/v2/categories", h)
    jsave("texts.json", texts)
    jsave("tags.json", tags)
    jsave("categories.json", cats)
    print("texts:", len(texts), "| tags:", [(t["id"], t["title"].get("en")) for t in tags], "| top categories:", len(cats))


def categories():
    return {c["id"]: (c.get("title") or {}).get("en", c["id"]) for c in jload("categories.json")}


# ---------------------------------------------------------------- BDRC
def bdrc_get(res):
    p = CACHE / "bdrc" / f"{res}.json"
    if p.exists():
        return res, json.loads(p.read_text()), ""
    d, err = get(f"https://purl.bdrc.io/resource/{res}.jsonld", {"User-Agent": UA, "Accept": "application/ld+json"}, timeout=30)
    if d is not None:
        p.write_text(json.dumps(d, ensure_ascii=False))
    return res, d, err


def nodes(d):
    g = (d or {}).get("@graph")
    return g if isinstance(g, list) else ([d] if d else [])


def ids(v):
    v = v if isinstance(v, list) else [v] if v else []
    return [(x.get("@id", "") if isinstance(x, dict) else str(x)).replace("bdr:", "") for x in v]


def label(d, res, prefer):
    for n in nodes(d):
        if isinstance(n, dict) and n.get("@id", "").endswith(res):
            v = n.get("skos:prefLabel")
            v = v if isinstance(v, list) else [v] if v else []
            pick = {x.get("@language"): x.get("@value") for x in v if isinstance(x, dict)}
            for p in prefer:
                if pick.get(p):
                    return pick[p]
            return next(iter(pick.values()), "")
    return ""


def cmd_bdrc():
    cache()
    (CACHE / "bdrc").mkdir(exist_ok=True)
    by = {t["id"]: t for t in jload("texts.json")}
    res_for = {t["id"]: re.sub(r"^.*/", "", t["bdrc"].strip()).replace("bdr:", "") for t in by.values()
               if not t.get("translation_of") and t["language"].startswith("bo") and t.get("bdrc")}
    rec, failed, topics, people = {}, {}, set(), set()
    with cf.ThreadPoolExecutor(5) as pool:
        for res, d, err in pool.map(bdrc_get, sorted(set(res_for.values()))):
            if d is None:
                failed[res] = err
                continue
            main = next((n for n in nodes(d) if isinstance(n, dict) and n.get("@id", "").endswith(res)), {})
            creators = []
            for n in nodes(d):
                if isinstance(n, dict) and "AgentAsCreator" in str(n.get("@type") or n.get("type")):
                    ag, role = ids(n.get("agent")), ids(n.get("role"))
                    if ag:
                        creators.append((ag[0], role[0] if role else ""))
                        people.add(ag[0])
            rec[res] = {"genre": ids(main.get("workGenre")), "about": ids(main.get("workIsAbout")), "instances": ids(main.get("workHasInstance")),
                        "title_wylie": label(d, res, ("bo-x-ewts", "en")), "title_en": label(d, res, ("en",)), "creators": creators}
            topics.update(rec[res]["genre"] + rec[res]["about"])
    labels = {}
    with cf.ThreadPoolExecutor(5) as pool:
        for res, d, err in pool.map(bdrc_get, sorted((topics | people) - {""})):
            labels[res] = label(d, res, ("en", "bo-x-ewts") if res.startswith("T") else ("bo-x-ewts", "en")) if d else ""
    roles = {"R0ER0019": "author", "R0ER0025": "translator", "R0ER0026": "translator", "R0ER0027": "pandita", "R0ER0014": "commentator"}
    for v in rec.values():
        v["authors"] = "; ".join(f"{labels.get(p) or p} ({roles.get(r, 'creator')})" for p, r in v.pop("creators"))
    jsave("bdrc.json", {"res_for": res_for, "records": rec, "labels": labels, "failed": failed})
    print("BDRC ids:", len(set(res_for.values())), "| fetched:", len(rec), "| failed:", failed, "| labels:", len(labels),
          "| with genre/topic:", sum(bool(v["genre"] or v["about"]) for v in rec.values()), "| with authors:", sum(bool(v["authors"]) for v in rec.values()))


def bdrc_evidence(tid, B):
    res = B["res_for"].get(tid, "")
    r = B["records"].get(res)
    out = {"bdrc_id": res, "bdrc_title": "", "bdrc_authors": "", "derge": "", "derge_signal": "", "label_signal": "", "bdrc_labels": ""}
    if not res:
        out["bdrc_labels"] = "no BDRC id on the library record"
        return out
    if r is None:
        out["bdrc_labels"] = f"BDRC lookup failed ({B['failed'].get(res, '?')})"
        return out
    labs = [B["labels"].get(g) or g for g in r["genre"] + r["about"]]
    sig = {s for s, rx in (("Vajrayana", V_LABEL), ("Mahayana", M_LABEL)) if any(rx.search(x) for x in labs)}
    out.update({"bdrc_title": r["title_wylie"], "bdrc_authors": r["authors"], "bdrc_labels": ", ".join(labs[:6]),
                "label_signal": "both" if len(sig) == 2 else next(iter(sig), "")})
    for inst in r["instances"]:
        m = re.fullmatch(r"(MW23703|MW22084)_(\d+)", inst)
        if m:
            n, table = int(m[2]), TENGYUR if m[1] == "MW23703" else KANGYUR
            for lo, hi, name, s in table:
                if lo <= n <= hi:
                    out["derge"], out["derge_signal"] = f"Derge {name}, Toh {n}", s
    return out


# ---------------------------------------------------------------- rails
def clone(repo):
    dest = CACHE / "rails" / repo
    if (dest / ".git").exists():
        return repo, ""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for s in (["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", f"https://github.com/WeBuddhist-Data/{repo}.git", str(dest)],
              ["git", "-C", str(dest), "sparse-checkout", "set", "--no-cone", "/1-SOURCES/**/*.md"]):
        r = subprocess.run(s, capture_output=True, text=True, timeout=600, env=env)
        if r.returncode:
            return repo, (r.stderr.strip().splitlines() or ["failed"])[-1][:120]
    return repo, ""


def cmd_rails():
    cache()
    (CACHE / "rails").mkdir(exist_ok=True)
    repos, page = [], 1
    while True:  # public repos only, no GitHub credentials
        body, err = get(f"https://api.github.com/orgs/WeBuddhist-Data/repos?type=public&per_page=100&page={page}", {"User-Agent": UA, "Accept": "application/vnd.github+json"}, timeout=60)
        if body is None:
            sys.exit(f"GitHub listing failed: {err}")
        repos += [x["name"] for x in body if not x.get("private")]
        if len(body) < 100:
            break
        page += 1
    errors = {}
    with cf.ThreadPoolExecutor(6) as pool:
        for repo, err in pool.map(clone, sorted(set(repos))):
            if err:
                errors[repo] = err
    found, files = collections.defaultdict(list), 0
    for repo in sorted(set(repos)):
        root = CACHE / "rails" / repo / "1-SOURCES"
        for p in (root.rglob("*.md") if root.exists() else []):
            m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", p.read_text(encoding="utf-8", errors="replace")[:6000], re.S)
            if not m:
                continue
            files += 1
            fm, key = {}, None
            for line in m[1].splitlines():  # flat YAML: "key: value", or "key:" followed by "- item" lines
                kv = re.match(r"^([A-Za-z_]+):[ \t]*(.*)$", line)
                if kv:
                    key = kv[1]
                    fm.setdefault(key, kv[2].strip())
                elif key and re.match(r"^\s*-\s+\S", line):
                    fm[key] = (fm[key] + " " + line.strip()[1:].strip()).strip()
            tid = fm.get("text_id", "").strip("\"' ")
            if re.fullmatch(r"[A-Za-z0-9_-]{21}", tid):
                found[tid].append({"repo": repo, "path": str(p.relative_to(CACHE / "rails" / repo)), "tag_ids": re.findall(r"[A-Za-z0-9_-]{21}", fm.get("tag_ids", ""))})
    jsave("rails.json", {"repos": sorted(set(repos)), "errors": errors, "files": files, "found": found})
    print("public repos:", len(set(repos)), "| clone errors:", errors, "| files with frontmatter:", files, "| text ids:", len(found))


def rails_tradition(tid, RA):
    return " / ".join(sorted({NAME[i] for h in RA["found"].get(tid, []) for i in h["tag_ids"] if i in NAME}))


# ---------------------------------------------------------------- content
def content_get(args):
    tid, ed = args
    d, err = get(f"{BASE}/v2/editions/{ed}/content?span_start=0&span_end={SPAN}", lib_headers())
    if d is None:
        return tid, None, err
    text = d if isinstance(d, str) else str(d)
    return tid, {"excerpt": text[:EXCERPT], "syllables": text.count("་"), "v": {t: text.count(t) for t in V_TERMS if t in text}, "m": {t: text.count(t) for t in M_TERMS if t in text}}, ""


def tibetan_works(by):
    return [t for t in by.values() if not t.get("translation_of") and t["language"].startswith("bo")]


def cmd_content():
    cache()
    by = {t["id"]: t for t in jload("texts.json")}
    path = CACHE / "content.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    todo = [(t["id"], t["editions"][0]) for t in tibetan_works(by) if t["id"] not in done and t.get("editions")]
    errors = collections.Counter()
    with cf.ThreadPoolExecutor(6) as pool:
        for tid, res, err in pool.map(content_get, todo):
            if res is None:
                errors[err] += 1
            else:
                done[tid] = res
    path.write_text(json.dumps(done, ensure_ascii=False))
    print("content:", len(done), "| new:", len(todo), "| errors:", dict(errors), "| no edition:", sum(not t.get("editions") for t in tibetan_works(by)))


def content_signal(c):
    if not c:
        return "", "content not available"
    v, m = sum(c["v"].values()), sum(c["m"].values())
    top = lambda x: ", ".join(f"{k} {n}" for k, n in sorted(x.items(), key=lambda kv: -kv[1])[:3]) or "none"
    sig = "too few terms" if v + m < 3 else "Vajrayana" if v >= max(2 * m, 3) else "Mahayana" if m >= max(2 * v, 3) else "mixed"
    return sig, f"tantric terms {v} ({top(c['v'])}); Mahayana terms {m} ({top(c['m'])}); {c['syllables']} syllables read"


# ---------------------------------------------------------------- evidence
def build_rows():
    by = {t["id"]: t for t in jload("texts.json")}
    cats, B, RA, CN = categories(), jload("bdrc.json"), jload("rails.json"), jload("content.json")
    rows = {}
    for t in tibetan_works(by):
        root = owner(by, by.get(t.get("commentary_of"))) if t.get("commentary_of") else None
        b = bdrc_evidence(t["id"], B)
        sig, detail = content_signal(CN.get(t["id"]))
        cat = cats.get(t.get("category_id"), t.get("category_id") or "")
        rows[t["id"]] = {"text_id": t["id"], "title": title(t), "title_en": t["title"].get("en", ""), "kind": "commentary" if root else "root",
                         "category": cat, "root_text_id": root["id"] if root else "", "root_title": title(root) if root else "", "root_language": root["language"] if root else "",
                         "prod_tradition": tradition(t) or "none", "prod_other_tags": ", ".join(OTHER.get(i, i) for i in t["tag_ids"] if i not in NAME) or "none",
                         "category_default": CATEGORY_DEFAULT.get(cat, ""), **b, "content_signal": sig, "content_detail": detail,
                         "rails_tradition": rails_tradition(t["id"], RA), "is_test": is_test(t), "translations": len(t.get("translations") or [])}
    return by, rows, CN


def auto_verify(r):
    """VERIFIED without reading when the catalogue (Derge position, else category default) and at least one independent
    source (BDRC labels or text content) agree, and no source (catalogue, labels, content, rails repo) points elsewhere."""
    cat = r["derge_signal"] or r["category_default"]
    if cat not in TAGS:
        return ""
    ind = [x for x in (r["label_signal"], r["content_signal"]) if x in TAGS]
    weak = [x for x in (r["label_signal"], r["content_signal"]) if x in ("both", "mixed")]
    others = [x for x in (r["rails_tradition"],) if x]
    if cat in ind and all(x == cat for x in ind) and not weak and all(x == cat for x in others):
        return cat
    return ""


def cmd_evidence():
    cache()
    by, rows, CN = build_rows()
    auto, read = {}, []
    for tid, r in rows.items():
        if r["is_test"]:
            continue
        a = auto_verify(r) if r["kind"] == "root" else ""
        cur = r["prod_tradition"]
        if a and (cur == "none" or cur == a):  # untagged, or an existing tag the evidence confirms
            auto[tid] = a
        else:  # everything else is read, including every existing tag the evidence does not confirm
            read.append(tid)
    random.seed(7)
    sample = random.sample(sorted(auto), min(40, len(auto)))
    items = []
    for tid in read + sample:
        r = rows[tid]
        items.append({"text_id": tid, "check": "blind re-check" if tid in auto else "classify", "title_tibetan": r["title"], "title_en": r["title_en"],
                      "kind": r["kind"], "library_category": r["category"], "root_text": {"id": r["root_text_id"], "title": r["root_title"], "language": r["root_language"]} if r["root_text_id"] else None,
                      "prod_tradition_tag": r["prod_tradition"], "text_opening": (CN.get(tid) or {}).get("excerpt", ""),
                      "bdrc": {"id": r["bdrc_id"], "title_wylie": r["bdrc_title"], "authors": r["bdrc_authors"], "labels": r["bdrc_labels"], "derge": r["derge"]},
                      "term_counts": r["content_detail"], "rails_repo_tradition": r["rails_tradition"] or "none"})
    items.sort(key=lambda x: ((x["root_text"] or {}).get("id") or x["text_id"], x["kind"] != "root"))
    bd = CACHE / "batches"
    bd.mkdir(exist_ok=True)
    for f in bd.glob("*.json"):
        f.unlink()
    for i in range(0, len(items), 15):
        (bd / f"b{i // 15 + 1:02d}.json").write_text(json.dumps(items[i:i + 15], ensure_ascii=False, indent=1))
    jsave("auto.json", auto)
    tests = [tid for tid, r in rows.items() if r["is_test"]]
    print("Tibetan works:", len(rows), "| untagged:", sum(r["prod_tradition"] == "none" for r in rows.values()), "| test records:", tests)
    print("auto verified:", len(auto), collections.Counter(auto.values()), "| to read:", len(read), "(untagged", sum(rows[t]["prod_tradition"] == "none" for t in read), ", tagged but questioned", sum(rows[t]["prod_tradition"] != "none" for t in read), ") | blind sample:", len(sample))
    print("batches:", len(list(bd.glob("*.json"))), "| items:", len(items), "| without opening:", sum(not x["text_opening"] for x in items))


# ---------------------------------------------------------------- merge
APPLY = r'''#!/bin/zsh
# __PURPOSE__
#
# Verified by the local audit (see tibetan_audit.csv). It changes LIVE data on the server in BASE; nothing runs automatically.
# Safety: it only ADDS tags with POST, never removes or replaces any, so Chants, Kangyur and other tags stay. Before any
# change it checks every tag ID exists for webuddhist, every text exists and is Tibetan, and skips texts that already have
# a tradition tag. It asks you to type APPLY first, and afterwards checks each text kept its earlier tags.
set -u
BASE="${BASE:?Set BASE first, for example: BASE=https://library-prod.webuddhist.com zsh this-file.sh}"
APP=webuddhist
LANG_PREFIX=bo
TAG=__TAG__
TRADITION_IDS=(296WZOr8MXWteSqBBI7pl zjHEBN2egpftHD7BRMQko ZPcgMZgVJxAMQ7rpfW72a)
IDS=(
__IDS__
)

printf 'API key: '; read -rs KEY; echo
[[ -n $KEY ]] || { echo "No API key entered."; exit 1; }
H=(-H "X-API-Key: $KEY" -H "X-Application: $APP")
tags=$(curl -sS "$BASE/v2/tags" "${H[@]}") || { echo "Could not read tags. Stopping."; exit 1; }
[[ $tags == *"\"id\":\"$TAG\""* ]] || { echo "Tag $TAG is not a webuddhist tag on $BASE. Stopping."; exit 1; }

echo "Checking ${#IDS} texts ..."
todo=()
for id in $IDS; do
  [[ $id =~ '^[A-Za-z0-9_-]{21}$' ]] || { echo "Invalid id: $id. Stopping."; exit 1; }
  body=$(curl -sS -w $'\n%{http_code}' "$BASE/v2/texts/$id" "${H[@]}")
  code=${body##*$'\n'}; body=${body%$'\n'*}
  if [[ $code != 200 ]]; then echo "SKIP $id: HTTP $code"; continue; fi
  if [[ $body != *"\"language\":\"$LANG_PREFIX"* ]]; then echo "SKIP $id: not Tibetan"; continue; fi
  current=${${body#*\"tag_ids\":\[}%%\]*}
  for t in $TRADITION_IDS; do
    if [[ $current == *$t* ]]; then echo "SKIP $id: already has a tradition tag"; continue 2; fi
  done
  todo+=($id)
done
echo "${#todo} of ${#IDS} texts will get the tag."
(( ${#todo} )) || exit 0
printf 'Type APPLY to add the tags: '; read -r answer
[[ $answer == APPLY ]] || { echo "Stopped. Nothing was changed."; exit 1; }

failed=0
for id in $todo; do
  before=$(curl -sS "$BASE/v2/texts/$id" "${H[@]}"); before=${${before#*\"tag_ids\":\[}%%\]*}
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/v2/texts/$id/tags/$TAG" "${H[@]}")
  after=$(curl -sS "$BASE/v2/texts/$id" "${H[@]}"); after=${${after#*\"tag_ids\":\[}%%\]*}
  ok=1
  [[ $code == 204 && $after == *$TAG* ]] || ok=0
  for old in ${(s:,:)before}; do [[ $after == *$old* ]] || ok=0; done
  if (( ok )); then echo "OK   $id"; else echo "FAIL $id (HTTP $code)"; failed=$((failed + 1)); fi
done
echo "Failed: $failed"
(( failed == 0 ))
'''

FIELDS = ["text_id", "title", "title_en", "kind", "category", "root_text_id", "root_title", "prod_tradition", "prod_other_tags",
          "status", "tradition", "confidence", "method", "evidence", "unverified_reason", "blind_check",
          "bdrc_id", "bdrc_title", "bdrc_authors", "derge", "bdrc_labels", "content_signal", "content_detail", "rails_tradition"]


def cmd_merge():
    cache()
    by, rows, CN = build_rows()
    auto = jload("auto.json")
    items = {x["text_id"]: x for f in sorted((CACHE / "batches").glob("*.json")) for x in json.loads(f.read_text())}
    res, problems = {}, []
    for f in sorted((CACHE / "results").glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except ValueError:
            problems.append(f"{f.name}: not valid JSON")
            continue
        for x in data:
            tid = x.get("text_id")
            if tid not in items:
                problems.append(f"{f.name}: unknown id {tid}")
            elif x.get("tradition") not in ("Theravada", "Mahayana", "Vajrayana", "UNVERIFIED") or x.get("confidence") not in ("high", "medium", "low"):
                problems.append(f"{f.name}: bad values for {tid}")
            elif tid not in res:
                res[tid] = x
    missing = sorted(set(items) - set(res))

    out = {}
    for tid, r in rows.items():
        o = {k: r.get(k, "") for k in FIELDS}
        o["derge"], o["blind_check"] = r["derge"], ""
        x = res.get(tid)
        if r["is_test"]:
            o.update(status="UNVERIFIED", tradition="", method="", unverified_reason="Looks like a test record")
        elif tid in auto:
            o.update(status="VERIFIED", tradition=auto[tid], confidence="high", method="catalogue + BDRC or content agree",
                     evidence=f"Catalogue: {r['derge'] or 'category ' + r['category']}; BDRC labels: {r['label_signal'] or 'none'}; content: {r['content_signal']}")
            if x:
                o["blind_check"] = "agrees" if x["tradition"] == auto[tid] else f"disagrees ({x['tradition']})"
                if x["tradition"] != auto[tid]:
                    o.update(status="UNVERIFIED", tradition="", confidence="low", unverified_reason=f"Automatic {auto[tid]}, but reading says {x['tradition']}: {x['evidence']}")
        elif x:
            if x["tradition"] == "UNVERIFIED":
                o.update(status="UNVERIFIED", tradition="", confidence=x["confidence"], method="read", evidence=x["evidence"], unverified_reason=x.get("unverified_reason") or "evidence insufficient")
            elif x["confidence"] == "low":
                o.update(status="UNVERIFIED", tradition="", confidence="low", method="read", evidence=x["evidence"], unverified_reason=f"evidence too weak (leans {x['tradition']})")
            else:
                o.update(status="VERIFIED", tradition=x["tradition"], confidence=x["confidence"], method="read", evidence=x["evidence"])
            o["evidence"] += f" [sources: {x.get('sources', '')}]" if x.get("sources") else ""
        elif r["prod_tradition"] != "none":
            o.update(status="EXISTING TAG NOT QUESTIONED", tradition=r["prod_tradition"], method="no source disagrees with the prod tag")
        else:
            o.update(status="UNVERIFIED", tradition="", method="", unverified_reason="not read yet")
        out[tid] = o
    # Commentaries follow their root: copy a verified or unquestioned root tradition, and flag disagreement.
    for tid, o in out.items():
        r = rows[tid]
        if r["kind"] != "commentary":
            continue
        root = out.get(r["root_text_id"])
        if root is not None:
            rt = root["tradition"] if root["status"] in ("VERIFIED", "EXISTING TAG NOT QUESTIONED") else ""
        else:  # root outside Tibetan, for example Sanskrit: a verified correction, else its prod tag
            rt = OUTSIDE_CORRECTIONS.get(r["root_text_id"], {}).get("tradition") or tradition(by.get(r["root_text_id"]))
        if not rt:
            continue
        if o["status"] == "VERIFIED" and o["tradition"] != rt:
            o.update(status="UNVERIFIED", unverified_reason=f"reading says {o['tradition']} but the root text is {rt}", tradition="")
        elif o["status"] == "UNVERIFIED" and o["unverified_reason"] in ("not read yet", "evidence insufficient") or (o["status"] == "UNVERIFIED" and "root" in o["unverified_reason"].lower() and "test" not in o["unverified_reason"].lower()):
            o.update(status="VERIFIED", tradition=rt, confidence="medium", method="commentary follows its root", evidence=f"Commentary on {r['root_title']} ({rt})", unverified_reason="")
    # Existing tags
    for o in out.values():
        p = o["prod_tradition"]
        if p == "none":
            o["existing"] = "none"
        elif o["status"] == "VERIFIED":
            o["existing"] = "correct" if o["tradition"] == p else "INCORRECT"
        elif o["status"] == "EXISTING TAG NOT QUESTIONED":
            o["existing"] = "not questioned"
        else:
            o["existing"] = "unconfirmed"
    fields = FIELDS[:10] + ["existing"] + FIELDS[10:]
    order = {"VERIFIED": 0, "UNVERIFIED": 1, "EXISTING TAG NOT QUESTIONED": 2}
    allrows = sorted(out.values(), key=lambda o: (order[o["status"]], o["existing"] != "INCORRECT", o["tradition"], o["category"], o["text_id"]))
    with open(AUDIT / "tibetan_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(allrows)
    outside = []
    for tid, c in OUTSIDE_CORRECTIONS.items():
        t = by[tid]
        o = {k: "" for k in fields}
        o.update(text_id=tid, title=title(t), title_en=t["title"].get("en", ""), kind="root (outside the Tibetan audit: language " + t["language"] + ")",
                 prod_tradition=tradition(t) or "none", prod_other_tags=", ".join(OTHER.get(i, i) for i in t["tag_ids"] if i not in NAME) or "none",
                 status="VERIFIED", tradition=c["tradition"], confidence="high", method="known text", evidence=c["evidence"] + f"; its {len(t.get('translations') or [])} translations share the tag",
                 existing="INCORRECT" if tradition(t) != c["tradition"] else "correct")
        outside.append(o)
    for name, pred, extra in (("tibetan_incorrect_tags.csv", lambda o: o["existing"] == "INCORRECT", [o for o in outside if o["existing"] == "INCORRECT"]), ("tibetan_unverified.csv", lambda o: o["status"] == "UNVERIFIED", [])):
        with open(AUDIT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fields)
            w.writeheader()
            w.writerows([o for o in allrows if pred(o)] + extra)
    counts = {}
    for trad, fname in (("Mahayana", "apply_mahayana.sh"), ("Vajrayana", "apply_vajrayana.sh")):
        idl = sorted(o["text_id"] for o in allrows if o["status"] == "VERIFIED" and o["tradition"] == trad and o["existing"] == "none")
        assert all(re.fullmatch(r"[A-Za-z0-9_-]{21}", i) for i in idl)
        text = APPLY.replace("__PURPOSE__", f"Add the {trad} tag to {len(idl)} untagged Tibetan root texts and commentaries verified as {trad}.").replace("__TAG__", TAGS[trad]).replace("__IDS__", "\n".join(f"  {i}" for i in idl))
        assert " -X DELETE" not in text and " -X PATCH" not in text and " -X PUT" not in text
        (AUDIT / fname).write_text(text, encoding="utf-8")
        counts[trad] = len(idl)
    th = [o for o in allrows if o["status"] == "VERIFIED" and o["tradition"] == "Theravada" and o["existing"] == "none"]
    # Pali check: every Pali root text and commentary should be Theravada (language is decisive for the Pali canon).
    pali = [t for t in by.values() if not t.get("translation_of") and t["language"].startswith("pi")]
    print("Pali works:", len(pali), "| tags:", collections.Counter(tradition(t) or "none" for t in pali),
          "| untagged:", sorted(t["id"] for t in pali if not tradition(t)), "| tagged other than Theravada:", [t["id"] for t in pali if tradition(t) not in ("", "Theravada")])
    print("problems:", problems[:10], "| missing results:", len(missing))
    print("rows:", len(allrows), "| status:", collections.Counter(o["status"] for o in allrows))
    print("verified by tradition:", collections.Counter(o["tradition"] for o in allrows if o["status"] == "VERIFIED"))
    print("untagged verified:", collections.Counter(o["tradition"] for o in allrows if o["status"] == "VERIFIED" and o["existing"] == "none"), "| untagged unverified:", sum(o["status"] == "UNVERIFIED" and o["existing"] == "none" for o in allrows))
    print("existing:", collections.Counter(o["existing"] for o in allrows), "| incorrect:", [(o["text_id"], o["prod_tradition"], o["tradition"]) for o in allrows if o["existing"] == "INCORRECT"])
    print("blind check:", collections.Counter(o["blind_check"].split(" ")[0] for o in allrows if o["blind_check"]))
    print("unverified reasons:", collections.Counter(o["unverified_reason"][:50] for o in allrows if o["status"] == "UNVERIFIED").most_common(12))
    print("scripts:", counts, "| untagged verified Theravada (not scripted):", [o["text_id"] for o in th])


if __name__ == "__main__":
    cmds = {"export": cmd_export, "bdrc": cmd_bdrc, "rails": cmd_rails, "content": cmd_content, "evidence": cmd_evidence, "merge": cmd_merge}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
