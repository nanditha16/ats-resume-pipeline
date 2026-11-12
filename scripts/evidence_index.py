#!/usr/bin/env python3
"""
evidence_index.py -- structured view of the single-source-of-truth evidence files.

The evidence files (output/evidence/*.md, one whole file per source) are curated
ledgers, not loose notes: a Number Ledger of verified values, Approved Claim Copy,
and a binding Do-Not-Claim table. So the evidence cross-check is a LOOKUP, not a
text search. This module parses those tables once (at ingest, and again only when
a file changes) into output/evidence/evidence_index.json, and answers:

  - is this number backed?   LEDGER / APPROVED / NARRATIVE / PROFILE_ONLY / NOT_FOUND
  - does this text make a claim the Do-Not-Claim tables forbid?
  - what in a resume variant is unbacked? (audit)

Tables, not headings, are the handle: five of the eight real files share one heading
skeleton, two do not (Project Y has no headings at all), but every file has tables.

Tiers (what counts as verification):
  LEDGER        a value in a Number Ledger / Fact table             verified
  APPROVED      inside Approved Claim Copy                          verified
  NARRATIVE     elsewhere in an evidence file                       NOT verified (policy)
  PROFILE_ONLY  only in the LinkedIn export (self-reported)         NOT verified
  NOT_FOUND     in no file                                          NOT verified

CLI:
  python3 scripts/evidence_index.py build            rebuild index, print per-file summary + ledger diff
  python3 scripts/evidence_index.py status           index freshness
  python3 scripts/evidence_index.py prompt-files     files to put in the Stage 4 prompt
  python3 scripts/evidence_index.py audit --all      audit every resume variant
  python3 scripts/evidence_index.py audit NAME       one variant (output/resume/NAME.md)
  python3 scripts/evidence_index.py check "text"     check a snippet
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ATS_ROOT", "/app"))
if not ROOT.exists():
    ROOT = Path(__file__).parent.parent.resolve()
EVIDENCE = ROOT / "output" / "evidence"
INDEX_PATH = EVIDENCE / "evidence_index.json"
PREV_PATH = EVIDENCE / "evidence_index.prev.json"
POLICY_PATH = ROOT / "policy" / "evidence_policy.json"
RESUMES = ROOT / "output" / "resume"

DEFAULT_POLICY = {
    "narrative_counts_as_verified": False,   # the ledger is the source of truth
    "profile_counts_as_verified": False,     # LinkedIn is self-reported
    "dnc_unchecks_card": True,
    # A card that REWRITES a line still carrying a do-not-claim claim is unticked by default. Measured against a reviewer's decisions on
    # 40 ticked cards (JD4, JD6, JD7): 18 of 19 such cards were rejected (95%); the one accepted was accepted with an edit. Unticking on
    # unbacked numbers alone was only 81% right, so that is NOT done. Set false to restore the old behaviour (a note only).
    "untick_when_keeping_dnc": True,
    # Words that are not a tool name, so a skills item made only of them is not looked up in the evidence.
    "skill_generic_words": ["practices", "management", "monitoring", "alerting", "security", "observability", "governance", "architectures",
        "architecture", "orchestration", "evaluation", "delivery", "pipelines", "workflows", "infrastructure", "code", "engineering",
        "development", "software", "testing", "scripting", "automation", "platform", "platforms", "cloud", "data", "messaging", "languages",
        "frameworks", "methods", "tools", "api", "apis", "ai", "genai", "llm", "aws", "azure", "agile", "scrum", "kanban", "safe", "review",
        "quality", "standards", "design", "principles", "process", "leadership", "integration", "release", "planning", "soc", "enterprise",
        "program", "reporting", "analytics", "assisted", "prompt", "pipeline", "deployment", "systems", "services", "stack", "framework",
        "library", "libraries", "continuous", "flagging", "pipelines"],
    # Different spellings of the same tool (an evidence file says "Postgres"; the resume says "PostgreSQL").
    "skill_aliases": {"postgresql": ["postgres"], "mongodb": ["mongo"], "javascript": ["js"], "golang": ["go"], "yaml": ["yml"]},
    # Headline-title words. A reword may not introduce a level word, or a people-management noun, that no title of yours has
    # ("Senior" -> "Principal"; "Senior Software Engineering Leader" -> "Engineering Manager").
    "title_level_words": ["principal", "staff", "director", "head", "vp", "chief", "president"],
    "title_people_words": ["manager", "director", "head"],
    "dnc_stem_len": 5,
    # A do-not-claim row is a DESCRIPTION; the resume line usually holds only its distinctive core ("multi-agent", "zero rollbacks").
    # An anchor applies to EVERY line, whatever role it sits under, and is tied to a rule by a substring of that rule's claim, or
    # carries its own claim/why for a fact the evidence states without a rule row. Anchors describe YOUR claims, so they belong in
    # policy/evidence_policy.local.json (git-ignored). Entry shape, one of:
    #   {"pattern": "\\bmulti-agent\\b", "rule": "text found in the do-not-claim row's claim"}
    #   {"pattern": "\\bzero outages\\b", "claim": "Zero outages", "file": "Employer.md", "why": "what the evidence says", "say_instead": ""}
    "dnc_phrase_anchors": [],
    # role keywords (a regex) -> substring of the evidence FILE NAME that governs that role. A rule from Employer.md applies to bullets
    # under that employer's header only, so one role's rule cannot fire on another role's line (it did, 13 times, before rules were
    # scoped). The entry below is a placeholder: put your own in policy/evidence_policy.local.json (git-ignored).
    "role_map": [["acme corp|acme", "acme"]],
    # role key (the file-name substring above) -> the name shown on the review page
    "role_labels": {"acme": "Acme Corp"},
    # Regex for the owner-name prefix on resume file names ("Jane_Doe_Resume_Acme" -> "Acme"). Empty: strip everything up to "resume".
    "variant_owner_prefix": "",
    "generic_numbers": ["100", "50", "10", "5", "1", "0"],      # '100%' alone proves nothing: needs 2 shared words
    # Audience nouns a reword must not ADD to a role unless that role's own evidence mentions them (a real run added "with hospital
    # and clinic stakeholders" to a pre-launch startup role whose evidence never mentions hospitals).
    "audience_terms": ["hospital", "clinic", "clinician", "patient", "investor", "customer", "regulator"],
}
STATUS_TEXT = {
    "CONTEXT_MISMATCH": "your evidence has this number, but for something else",
    "NARRATIVE": "only in narrative text, not in a ledger",
    "PROFILE_ONLY": "only on your LinkedIn profile",
    "NOT_FOUND": "in no evidence file",
}
STOP = set("the and for with that this from into have been your their built claim used using only any all are was were "
           "has had not but can may per via its out one two across over under about into onto upon being those these "
           "such than then them they what when which while will would should could also each every more most other "
           "some very through throughout".split())


def load_policy() -> dict:
    """Defaults, then policy/evidence_policy.json (shared, committed), then policy/evidence_policy.local.json (yours, git-ignored: your
    employers, claims and name belong there). Later files replace earlier values key by key. Keys starting with _ are comments."""
    pol = dict(DEFAULT_POLICY)
    for path in (POLICY_PATH, POLICY_PATH.with_name("evidence_policy.local.json")):
        if path.exists():
            try:
                pol.update({k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")})
            except ValueError:
                pass
    return pol


# ── Markdown parsing ─────────────────────────────────────────────────────────
def clean_cell(c: str) -> str:
    c = re.sub(r"<br\s*/?>", " ", c)
    # inline math a converter makes of "I2C" ($I^{2}C$); only when it holds ^ or _, so "$1B ... $2M" is never touched
    c = re.sub(r"\$([^$\n]*[\^_][^$\n]*)\$", lambda m: re.sub(r"[\^_{}]", "", m.group(1)), c)
    c = re.sub(r"\\([$\"'\[\]()_*#|.\-])", r"\1", c)          # markdown escapes: \$ \" ...
    c = re.sub(r"\*\*|__", "", c)
    c = re.sub(r"(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])", "", c)
    return re.sub(r"\s+", " ", c).strip().strip('"\u201c\u201d').strip()


def _split_row(line: str):
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") else inner
    return [clean_cell(c) for c in re.split(r"(?<!\\)\|", inner)]


def parse_tables(text: str) -> list:
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            hdr = [h.lower() for h in _split_row(lines[i])]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append(_split_row(lines[j]))
                j += 1
            out.append({"header": hdr, "rows": rows, "line": i})
            i = j
        else:
            i += 1
    return out


_HEAD = re.compile(r"^(#{1,6})\s+(?:#\s+)*(.*?)\s*$")


def headings(text: str) -> list:
    """[(line, level, title)] -- tolerates the literal '# # 3. Title' artefact a docx
    heading can carry, and strips section numbers."""
    out = []
    for i, ln in enumerate(text.splitlines()):
        m = _HEAD.match(ln)
        if m and m.group(2):
            out.append((i, len(m.group(1)), re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", clean_cell(m.group(2)))))
    return out


def _section_at(heads: list, line: int) -> str:
    cur = ""
    for ln, _lvl, title in heads:
        if ln <= line:
            cur = title
        else:
            break
    return cur


def approved_claims(text: str, heads: list) -> list:
    """Bullets inside any 'Approved Claim Copy' section (until the next heading of the same or higher level)."""
    lines = text.splitlines()
    out = []
    for k, (ln, lvl, title) in enumerate(heads):
        if not re.search(r"approved claim", title, re.I):
            continue
        end = len(lines)
        for ln2, lvl2, _t in heads[k + 1:]:
            if lvl2 <= lvl:
                end = ln2
                break
        topic = ""
        for j in range(ln + 1, end):
            h = _HEAD.match(lines[j])
            if h:
                topic = clean_cell(h.group(2))
                continue
            # Two real shapes: "- *"claim"*" bullets (Employer B, Employer C, Employer E) and bare italic-quoted
            # paragraphs (Employer A, WHI). A bullets-only parser silently dropped 29 of 57 approved claims.
            b = re.match(r"^\s*[-*]\s+(.*)", lines[j]) or re.match(r'^\s*(\*?"[^|].*)$', lines[j])
            if b:
                t = clean_cell(b.group(1))
                if len(t) > 15:
                    out.append({"topic": topic, "text": t})
    return out


# ── Numbers ──────────────────────────────────────────────────────────────────
# A PDF-converted resume starts every bullet with the literal text "(cid:127)". Read as a number it is 127 on every line,
# which made one unrelated number "match" every bullet (and, via a plain number in your evidence such as "127 exchanges", could add false
# companion-number agreement). Removed before any number is read.
PDF_ARTIFACT = re.compile(r"\(cid:\d+\)")


def clean_pdf(text: str) -> str:
    return PDF_ARTIFACT.sub(" ", text or "")


_TOK = re.compile(r"(?P<cur>\$)?\s?(?P<num>\d[\d,]*(?:\.\d+)?)(?P<unit>\s?%|[KMB]\b|[x\u00d7]\b|\s?percent\b)?(?P<plus>\+)?", re.I)


def _norm_num(n: str) -> str:
    n = n.replace(",", "")
    return n.rstrip("0").rstrip(".") if "." in n else n


def tokens(text: str, metric_only: bool = False) -> list:
    """[(key, raw)]. key ignores the '+' so '15+' matches a ledger '15'; metric_only keeps only
    numbers with a unit, a '+', or a currency sign (what a resume claim looks like)."""
    out = []
    for m in _TOK.finditer(clean_pdf(text)):
        unit = (m.group("unit") or "").strip().lower().replace("\u00d7", "x")
        unit = "%" if unit == "percent" else unit
        cur = m.group("cur") or ""
        if metric_only and not (unit or m.group("plus") or cur):
            continue
        out.append((f"{cur}{_norm_num(m.group('num'))}{unit}", m.group(0).strip()))
    return out


# Words that say "something improved" without saying WHAT, so they cannot show two sentences are about the same thing.
GENERIC_STEMS = {"reduc", "incre", "impro", "achie", "accel", "deliv", "owned", "manag", "direc", "drove", "acros", "with",
                 "from", "that", "thei", "resul", "betwe", "withi", "month", "quart", "year", "yearl", "enter",
                 "level", "deplo", "progr", "team", "teams", "work", "workf", "suppo", "enabl", "ensur", "drive", "using"}
_BASELINE = re.compile(r"\b(baseline|pre-existing|distribution|distributions|share|mix|split|governed by)\b", re.I)


def ctx_stems(text: str, own: str = "") -> set:
    """What a snippet is about, as weighted stems: 'L:xxxxxx' for a long specific word (regression, playbook) or an
    acronym (ACC, NPID); 'S:xxxxx' for a short common one (risk, cost, time). Generic result words are dropped."""
    out = set()
    for w in re.findall(r"[a-z]{4,}", (text or "").lower()):
        if w in STOP or w[:5] in GENERIC_STEMS:
            continue
        out.add(("L:" + w[:6]) if len(w) >= 7 else ("S:" + w[:5]))
    out |= {"L:" + a.lower() for a in re.findall(r"\b[A-Z]{2,6}\b", text or "")}
    # Companion numbers ("710 to 357") travel with a measurement and are strong evidence two sentences describe the same
    # one even when the words differ ("Non-People IDs" vs "active service accounts"). The number being checked is
    # excluded, or every number would match itself. Years are not measurements.
    own_digits = {re.sub(r"[^\d.]", "", own)} if own else set()
    for n in re.findall(r"\b\d{2,}(?:[.,]\d+)?\b", text or ""):
        n = n.replace(",", "")
        if n not in own_digits and not re.fullmatch(r"(?:19|20)\d\d", n):
            out.add("L:#" + n)
    return out


def _agreement(a: set, b: set) -> int:
    """How strongly two stem sets say 'same subject': a shared specific word counts 2, a shared common word 1.
    One common word ('risk', 'market') is not enough to call two numbers the same measurement."""
    return sum(2 if x.startswith("L:") else 1 for x in (a & b))


def tokens_ctx(text: str, metric_only: bool = True, width: int = 90) -> list:
    """[(key, raw, stems_of_the_words_around_it)]: the number AND what it is said to measure."""
    out = []
    text = clean_pdf(text)
    for m in _TOK.finditer(text):
        unit = (m.group("unit") or "").strip().lower().replace("\u00d7", "x")
        unit = "%" if unit == "percent" else unit
        cur = m.group("cur") or ""
        if metric_only and not (unit or m.group("plus") or cur):
            continue
        key = f"{cur}{_norm_num(m.group('num'))}{unit}"
        out.append((key, m.group(0).strip(), ctx_stems(text[max(0, m.start() - width): m.end() + width], own=m.group("num"))))
    return out


def strip_years(text: str) -> str:
    """'13+ years' is derived from dates, not an evidence claim."""
    return re.sub(r"\b\d+\+?\s*(?:years?|yrs?)\b", " ", text or "", flags=re.I)


# ── Build ────────────────────────────────────────────────────────────────────
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _version(pairs) -> str:
    return _sha("\n".join(f"{f}:{h}" for f, h in sorted(pairs)))


def _tier(text: str) -> str:
    return "profile" if re.search(r"linkedin\.com/in/", text[:1500], re.I) else "evidence"


def parse_file(name: str, text: str) -> dict:
    heads = headings(text)
    tables = parse_tables(text)
    ledger, dnc = [], []
    for t in tables:
        hdr, joined = t["header"], " ".join(t["header"])
        section = _section_at(heads, t["line"])
        dcol = next((i for i, h in enumerate(hdr) if re.search(r"do not claim|do-not-claim|don'?t say", h)), None)
        if dcol is not None:
            wcol = next((i for i, h in enumerate(hdr) if "why" in h), None)
            scol = next((i for i, h in enumerate(hdr) if "instead" in h), None)
            for r in t["rows"]:
                claim = r[dcol] if len(r) > dcol else ""
                if len(claim) < 8 or claim.lower() in ("n/a", "none", "-"):
                    continue
                dnc.append({"file": name, "section": section, "claim": claim,
                            "why": r[wcol] if wcol is not None and len(r) > wcol else (f"{r[0]}" if len(hdr) > 1 else ""),
                            "say_instead": r[scol] if scol is not None and len(r) > scol else ""})
            continue
        vcol = next((i for i, h in enumerate(hdr) if "value" in h), None)
        if vcol is not None and re.search(r"metric|fact|value", joined):
            for r in t["rows"]:
                if len(r) > vcol and r[vcol]:
                    ledger.append({"file": name, "section": section, "metric": r[0], "value": r[vcol],
                                   "context": " | ".join(c for k, c in enumerate(r) if k not in (0, vcol) and c)[:400]})
    claims = [dict(c, file=name) for c in approved_claims(text, heads)]
    return {"file": name, "tier": _tier(text), "sha": _sha(text), "chars": len(text), "tokens_est": len(text) // 4,
            "tables": len(tables), "ledger": ledger, "do_not_claim": dnc, "approved": claims}


def build() -> dict:
    files = sorted(p for p in EVIDENCE.glob("*.md") if not p.name.startswith("."))
    parsed = [parse_file(p.name, p.read_text(encoding="utf-8", errors="replace")) for p in files]
    texts = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in files}

    led, appr, narr, prof = {}, {}, set(), set()
    words = set()
    for pf in parsed:
        text = texts[pf["file"]]
        if pf["tier"] == "profile":
            prof.update(k for k, _ in tokens(text))
            words.update(re.findall(r"[a-z0-9]{2,}", text.lower()))
            continue
        narr.update(k for k, _ in tokens(text))
        words.update(re.findall(r"[a-z0-9]{2,}", text.lower()))
        for row in pf["ledger"]:
            for k, raw in tokens(row["value"]):
                stems = sorted(ctx_stems(f"{row['metric']} {row['value']} {row['context']}", own=k))[:40]
                led.setdefault(k, [])
                if len(led[k]) < 8:
                    led[k].append([row["file"], row["metric"][:80], stems])
        for c in pf["approved"]:
            for k, _raw, st in tokens_ctx(c["text"], metric_only=False):
                appr.setdefault(k, [])
                if len(appr[k]) < 8:
                    appr[k].append([c["file"], c["topic"][:80], sorted(st)[:30]])

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ev_hash = _version([(pf["file"], pf["sha"]) for pf in parsed])
    prev = load_index()
    prev_hash = (prev.get("evidence_hash") or _version([(x["file"], x["sha"]) for x in prev["sources"]])) if prev else None
    index = {
        "built_at": now,
        # When the evidence CONTENT last changed. A re-ingest with nothing changed keeps the old value, so a
        # Stage 4 response is only called stale if the evidence really moved after it was written.
        "evidence_hash": ev_hash,
        "changed_at": (prev.get("changed_at") or prev["built_at"]) if prev and prev_hash == ev_hash else now,
        "sources": [{k: pf[k] for k in ("file", "tier", "sha", "chars", "tokens_est", "tables")}
                    | {"ledger_rows": len(pf["ledger"]), "dnc_rows": len(pf["do_not_claim"]),
                       "approved": len(pf["approved"])} for pf in parsed],
        "ledger": [r for pf in parsed for r in pf["ledger"]],
        "do_not_claim": [r for pf in parsed for r in pf["do_not_claim"]],
        "approved": [c for pf in parsed for c in pf["approved"]],
        "tok_ledger": led, "tok_approved": appr, "tok_narrative": sorted(narr), "tok_profile": sorted(prof),
        "words": sorted(words),
    }
    diff = _ledger_diff(load_index(), index)
    if INDEX_PATH.exists():
        PREV_PATH.write_text(INDEX_PATH.read_text())
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=1))
    index["_diff"] = diff
    return index


def _ledger_diff(old, new) -> dict:
    if not old:
        return {}
    key = lambda r: (r["file"], r["metric"])
    o, n = {key(r): r["value"] for r in old["ledger"]}, {key(r): r["value"] for r in new["ledger"]}
    return {"added": [f"{f}: {m} = {n[(f, m)]}" for (f, m) in n if (f, m) not in o],
            "removed": [f"{f}: {m} (was {o[(f, m)]})" for (f, m) in o if (f, m) not in n],
            "changed": [f"{f}: {m}: {o[(f, m)]} -> {n[(f, m)]}" for (f, m) in n if (f, m) in o and o[(f, m)] != n[(f, m)]]}


def load_index():
    if not INDEX_PATH.exists():
        return None
    try:
        return json.loads(INDEX_PATH.read_text())
    except ValueError:
        return None


def status() -> dict:
    idx = load_index()
    md = {p.name: _sha(p.read_text(encoding="utf-8", errors="replace"))
          for p in EVIDENCE.glob("*.md") if not p.name.startswith(".")} if EVIDENCE.exists() else {}
    if not idx:
        return {"indexed": False, "files": len(md), "ledger_rows": 0, "dnc_rules": 0, "approved_claims": 0,
                "stale": sorted(md)}
    known = {s["file"]: s["sha"] for s in idx["sources"]}
    return {"indexed": True, "built_at": idx["built_at"], "changed_at": idx.get("changed_at", idx["built_at"]), "files": len(md), "ledger_rows": len(idx["ledger"]),
            "dnc_rules": len(idx["do_not_claim"]), "approved_claims": len(idx["approved"]),
            "stale": sorted(n for n, h in md.items() if known.get(n) != h) + sorted(set(known) - set(md))}


def prompt_files() -> list:
    idx = load_index()
    names = sorted(p.name for p in EVIDENCE.glob("*.md") if not p.name.startswith(".")) if EVIDENCE.exists() else []
    if not idx:
        return names
    profile = {s["file"] for s in idx["sources"] if s["tier"] == "profile"}
    return [n for n in names if n not in profile]


# ── Checks ───────────────────────────────────────────────────────────────────
def token_status(key: str, idx: dict, ctx: set = None) -> tuple:
    """(status, [[file, metric], ...]). With `ctx` (the words around the number in the text being checked) a ledger
    or approved row only verifies the number if it is about the same thing (at least one shared content word).
    Without it, or against an index built before contexts were stored, the number alone decides, as before."""
    def split(entries):
        if ctx is None or any(len(e) < 3 for e in entries):
            return entries, []
        ok = [e for e in entries if _agreement(ctx, set(e[2])) >= 2]
        return ok, [e for e in entries if e not in ok]
    ok_l, other_l = split(idx["tok_ledger"].get(key, []))
    if ok_l:
        return "LEDGER", [e[:2] for e in ok_l]
    ok_a, other_a = split(idx["tok_approved"].get(key, []))
    if ok_a:
        return "APPROVED", [e[:2] for e in ok_a]
    if other_l or other_a:
        return "CONTEXT_MISMATCH", [e[:2] for e in (other_l + other_a)][:3]
    if key in set(idx["tok_narrative"]):
        return "NARRATIVE", []
    if key in set(idx["tok_profile"]):
        return "PROFILE_ONLY", []
    return "NOT_FOUND", []


def _verified(status: str, pol: dict) -> bool:
    return status in ("LEDGER", "APPROVED") or (status == "NARRATIVE" and pol["narrative_counts_as_verified"]) \
        or (status == "PROFILE_ONLY" and pol["profile_counts_as_verified"])


def _stems(text: str, n: int) -> set:
    return {w[:n] for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in STOP}


_BULLET = re.compile(r"^\s*(?:\(cid:\d+\)|[\u2022*\-])")
_SECTION_RESET = re.compile(r"^(SUMMARY|CORE COMPETENCIES|TECHNICAL STACK|CAREER HIGHLIGHTS|EDUCATION|CERTIFICATIONS|"
                            r"PROFESSIONAL EXPERIENCE)\s*$", re.I)


def scope_for(text: str, pol: dict):
    """Files whose rules govern `text` ('acme', 'globex', ...), or None for 'global' (no role named)."""
    t = (text or "").lower()
    hit = {fs for kw, fs in pol["role_map"] if re.search(kw, t)}
    return hit or None


def _windows(text: str, pol: dict = None):
    """(window, scope) per line. A role header line (company + dates/separator, not a bullet) sets the scope
    for the bullets under it; a section heading clears it."""
    pol = pol or load_policy()
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    scope = None
    for i, ln in enumerate(lines):
        if _SECTION_RESET.match(ln):
            scope = None
        elif not _BULLET.match(ln) and len(ln) < 170 and re.search(r"\b(19|20)\d\d\b|\s[-\u2013\u2014]\s|\|", ln):
            sc = scope_for(ln, pol)
            if sc:
                scope = sc
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        ln, nxt = clean_pdf(ln).strip(), clean_pdf(nxt).strip()
        yield ((ln + " " + nxt).strip()), scope, (ln, nxt)


def dnc_hits(text: str, idx: dict, pol: dict = None, hint: str = None) -> list:
    """Do-Not-Claim rules the text trips. Precise by design: fuzzy matching over all 85 rules flagged 224
    (rule, line) pairs on 18 variants, nearly all noise. Now:
      - a rule applies only where its own role applies (text under a role header, or `hint` naming the role);
        text with no role (Summary, Highlights, competencies) is checked against number rules only;
      - a number rule fires on the number plus a shared content word (two words if the number is generic, like 100%);
      - a number-free rule fires on 70% of its content words (at least 3);
      - a window that already says the rule's 'Say instead' wording is skipped."""
    pol = pol or load_policy()
    n, generic = pol["dnc_stem_len"], set(pol["generic_numbers"])
    hint_scope = scope_for(hint, pol) if hint else None
    hits, seen = [], set()
    wins = list(_windows(text, pol))
    for k, rule in enumerate(idx["do_not_claim"]):
        # Anchors: numbers with a unit, plus a distinctive PLAIN number (3+ digits, not a year). A rule such as "Built custom
        # feeds for each of the 127 exchanges" is anchored by 127; without it the matcher saw only 2 of 4 words in the
        # resume's "Standardized feed processing across 127 exchanges" and missed it.
        rtoks = [key for key, _ in tokens(rule["claim"], metric_only=True)] + \
                [key for key, _ in tokens(rule["claim"]) if re.fullmatch(r"\d{3,}", key) and not re.fullmatch(r"(?:19|20)\d\d", key)]
        rnums = set(rtoks)
        rstems = _stems(rule["claim"], n)
        sstems = _stems(rule["say_instead"], n)
        if not rnums and len(rstems) < 3:
            continue
        all_generic = bool(rnums) and all(re.sub(r"[^\d.]", "", t) in generic for t in rtoks)
        need = 2 if all_generic else 1
        fkey = rule["file"].lower()
        for w, sc, pair in wins:
            scope = hint_scope or sc
            if scope is not None and not any(f in fkey for f in scope):
                continue                       # another role's rule
            if scope is None and (not rnums or all_generic):
                continue                       # phrase rules, and a bare '100%' rule, need a role to be meaningful
            ws = _stems(w, n)
            if sstems and len(ws & sstems) / len(sstems) >= 0.6:
                continue
            if rnums:
                ok = rnums <= {key for key, _ in tokens(w)} and len(ws & rstems) >= need
            else:
                ok = len(ws & rstems) >= 3 and len(ws & rstems) / len(rstems) >= 0.7
            if ok:
                sig = (k, re.sub(r"\W+", "", w.lower())[:60])
                if sig not in seen:
                    seen.add(sig)
                    # show the line that matched, not the two-line window (the number is often on the second line)
                    best = max(pair, key=lambda L: (len(_stems(L, n) & rstems) + (3 if rnums & {key for key, _ in tokens(L)} else 0)))
                    hits.append({"rule": k, "file": rule["file"], "claim": rule["claim"][:220], "why": rule["why"][:300],
                                 "say_instead": rule["say_instead"][:300], "text": best[:200]})
    # Phrase anchors: any line, any role.
    for ai, a in enumerate(pol.get("dnc_phrase_anchors") or []):
        try:
            rx = re.compile(a["pattern"], re.IGNORECASE)
        except re.error:
            continue
        if a.get("rule"):
            ks = [k for k, r in enumerate(idx["do_not_claim"]) if a["rule"].lower() in r["claim"].lower()]
            if not ks:
                continue                        # the evidence no longer has that rule
            rid, rule = ks[0], idx["do_not_claim"][ks[0]]
        else:
            rid, rule = f"anchor:{ai}", {"file": a.get("file", ""), "claim": a["claim"], "why": a.get("why", ""), "say_instead": a.get("say_instead", "")}
        sstems = _stems(rule["say_instead"], n)
        for w, sc, pair in wins:
            line = next((L for L in pair if rx.search(L)), None)
            if line is None:
                continue
            if sstems and len(_stems(w, n) & sstems) / len(sstems) >= 0.6:
                continue                        # already says the approved wording
            sig = (rid, re.sub(r"\W+", "", line.lower())[:60])
            if sig not in seen:
                seen.add(sig)
                shown = line.strip() if len(line.strip()) >= 60 else " ".join(L.strip() for L in pair)   # a wrapped bullet's short tail
                hits.append({"rule": rid, "file": rule["file"], "claim": rule["claim"][:220], "why": rule["why"][:300],
                             "say_instead": rule["say_instead"][:300], "text": shown[:200]})
    return hits


def audit_text(text: str, idx: dict, pol: dict = None) -> dict:
    """Every number in the text, judged AT EACH OCCURRENCE (a number can be verified in one sentence and
    coincidental in another). A number is reported with its worst status."""
    pol = pol or load_policy()
    rank = {"CONTEXT_MISMATCH": 0, "NOT_FOUND": 1, "PROFILE_ONLY": 2, "NARRATIVE": 3, "APPROVED": 4, "LEDGER": 5}
    seen = {}
    for key, raw, ctx in tokens_ctx(strip_years(text), metric_only=True):
        st, where = token_status(key, idx, ctx)
        d = seen.setdefault(key, {"raw": raw, "count": 0, "status": st, "found_in": where})
        d["count"] += 1
        if rank[st] < rank[d["status"]]:
            d["status"], d["found_in"] = st, where
    metrics = [{"raw": d["raw"], "count": d["count"], "status": d["status"], "verified": _verified(d["status"], pol),
                "found_in": d["found_in"]} for d in seen.values()]
    metrics.sort(key=lambda m: (m["verified"], -m["count"]))
    return {"metrics": metrics, "dnc": dnc_hits(text, idx, pol)}


def check_introduced(before: str, after: str, idx: dict, pol: dict = None, hint: str = None) -> dict:
    """What a change ADDS, judged against the evidence. Wording already in the base resume is not blamed on the card."""
    pol = pol or load_policy()
    bkeys = {k for k, _ in tokens(strip_years(before), metric_only=True)}
    metrics, problems = [], []
    for key, raw, ctx in tokens_ctx(strip_years(after), metric_only=True):
        if key in bkeys or any(m["raw"] == raw for m in metrics):
            continue
        st, where = token_status(key, idx, ctx)
        ok = _verified(st, pol)
        metrics.append({"raw": raw, "status": st, "verified": ok, "found_in": where})
        if not ok:
            why = STATUS_TEXT[st] + (f": it is the ledger's {where[0][1]!r}" if st == "CONTEXT_MISMATCH" and where else "")
            problems.append({"raw": raw, "label": f"{raw} ({why})"})
    before_sigs = {(h["rule"]) for h in dnc_hits(before, idx, pol, hint)} if before else set()
    dnc = [h for h in dnc_hits(after, idx, pol, hint) if h["rule"] not in before_sigs]
    return {"metrics": metrics, "problems": problems, "dnc": dnc}


_SMALL_WORDS = {"of", "and", "the", "for", "to", "in", "a", "an", "on", "&"}


def _is_initialism(acr: str, text: str) -> bool:
    """True only for an EXPLICIT definition: 'lines of business (LOB)' or 'LOB (lines of business)'. Matching initials
    anywhere in free text is not safe: some unrelated three-word phrase will always spell G-C-P. Small words may or
    may not count ('lines OF business' counts the O; 'Return on Investment' counts the O too)."""
    n, a = len(acr), acr.lower()

    def spell(words):
        for skip in (False, True):
            if "".join(w[0].lower() for w in words if not (skip and w.lower() in _SMALL_WORDS)) == a:
                return True
        return False

    for m in re.finditer(r"\(\s*" + re.escape(acr) + r"\s*\)", text):
        words = re.findall(r"[A-Za-z&]+", text[max(0, m.start() - 80): m.start()])
        if any(k <= len(words) and spell(words[-k:]) for k in range(n, n + 4)):
            return True
    for m in re.finditer(re.escape(acr) + r"\s*\(([^)]{3,80})\)", text):
        if spell(re.findall(r"[A-Za-z&]+", m.group(1))):
            return True
    return False


def unsupported_acronyms(before: str, after: str, resume_text: str, idx: dict) -> list:
    """Acronyms a change INTRODUCES that appear nowhere in the resume, the evidence or the profile. Found on JD1:
    the Summary rewrite claimed experience 'including EHR workflow automation', but EHR is in neither the resume nor
    any evidence file (0 and 0), and Stage 4's own Gap 6 said the candidate has no EHR experience; on JD23 the model
    added GCP to a Technical Stack that never mentions it. Deliberately narrow: acronyms only. A JD term that is plain
    English (hospital, discovery) is how paraphrase is meant to work, and whether it is truly supported is a judgement;
    an acronym absent from everything is not. An abbreviation of words already present (LOB for 'lines of business')
    is not new."""
    known = set(idx.get("words", [])) | set(re.findall(r"[a-z0-9]{2,}", (resume_text or "").lower()))
    seen = set(re.findall(r"\b[A-Z]{2,6}\b", before or ""))
    pool = f"{resume_text or ''} {before or ''} {after or ''}"
    return sorted({a for a in re.findall(r"\b[A-Z]{2,6}\b", after or "")
                   if a not in seen and a.lower() not in known and not _is_initialism(a, pool)})


_ACR = re.compile(r"\b[A-Z]{2,6}s?\b")


def _acr_set(t: str) -> set:
    return {a[:-1] if a.endswith("s") and len(a) > 3 else a for a in _ACR.findall(t or "")}


def dropped_specifics(before: str, after: str, elsewhere: str = None) -> list:
    """Acronyms and numbers a reword REMOVES. Seen on a real run: one reword dropped 'in partnership with the CTO' (supported by the
    evidence) to add an audience the evidence never mentions, and another replaced an acronym with a plain phrase. A paraphrase should change wording, not delete what you did. Acronyms and numbers only: plain
    words are reworded all the time. Fires on about 8% of Stage 3 cards."""
    raws = {k: raw for k, raw in tokens(strip_years(before), metric_only=True)}
    na = {k for k, _ in tokens(strip_years(after), metric_only=True)}
    lost_acr = _acr_set(before) - _acr_set(after)
    lost_num = {k for k in raws if k not in na}
    if elsewhere is not None:
        # Only a REAL loss counts: something that now exists nowhere else in the resume. The Summary rules deliberately
        # move numbers to Career Highlights, and HITL/ML are on other lines, so those are relocated, not dropped.
        lost_acr -= _acr_set(elsewhere)
        lost_num -= {k for k, _ in tokens(strip_years(elsewhere), metric_only=True)}
    return sorted(lost_acr) + [raws[k] for k in sorted(lost_num)]


def carried_problems(before: str, after: str, idx: dict, pol: dict = None, hint: str = None) -> tuple:
    """What an edit LEAVES IN PLACE that the evidence does not support: (unbacked numbers, do-not-claim rules the line
    already trips). check_introduced blames a card only for what it adds, which is right for blame but hid the
    reviewer's real objection: a reword that polishes a bullet around a number your ledger does not back, or adds a buzzword
    to a line that already breaks your do-not-claim list. Shown as notes, never as unchecking flags."""
    pol = pol or load_policy()
    bk = {k for k, _ in tokens(strip_years(before), metric_only=True)}
    a = audit_text(after, idx, pol)
    unbacked = [f"{m['raw']} ({STATUS_TEXT[m['status']]})" for m in a["metrics"]
                if not m["verified"] and tokens(m["raw"]) and tokens(m["raw"])[0][0] in bk]
    rb = {h["rule"] for h in dnc_hits(before, idx, pol, hint)}
    dnc, seen = [], set()
    for h in dnc_hits(after, idx, pol, hint):          # one entry per rule, not per matching line
        if h["rule"] in rb and h["rule"] not in seen:
            seen.add(h["rule"])
            dnc.append(h)
    return unbacked, dnc


def scope_at(text: str, offset: int, pol: dict = None):
    """The role (scope set) a character offset in the resume belongs to: the nearest role header above it."""
    pol = pol or load_policy()
    scope, pos = None, 0
    for ln in text.splitlines(keepends=True):
        if pos > offset:
            break
        raw = clean_pdf(ln).strip()
        if _SECTION_RESET.match(raw):
            scope = None
        elif not _BULLET.match(ln) and len(raw) < 170 and re.search(r"\b(19|20)\d\d\b|\s[-\u2013\u2014]\s|\|", raw):
            sc = scope_for(raw, pol)
            if sc:
                scope = sc
        pos += len(ln)
    return scope


def introduced_audience_terms(before: str, after: str, resume_text: str, start_offset: int, pol: dict = None) -> tuple:
    """(terms, evidence_file): audience nouns the change ADDS that the role's own evidence file never mentions. Silent when the
    role has no evidence file to judge against. Measured on every existing Stage 3 edit: 0 false alarms in 145, and the one real
    case."""
    pol = pol or load_policy()
    rx = re.compile(r"\b(" + "|".join(re.escape(t) for t in pol["audience_terms"]) + r")(?:s|es)?\b", re.I)
    scope = scope_at(resume_text, start_offset, pol)
    files = [p for p in sorted(EVIDENCE.glob("*.md")) if scope and any(f in p.name.lower() for f in scope)] if EVIDENCE.exists() else []
    files = [p for p in files if _tier(p.read_text(encoding="utf-8", errors="replace")) != "profile"]
    if not files:
        return [], None
    role_text = " ".join(p.read_text(encoding="utf-8", errors="replace").lower() for p in files)
    added = {m.group(1).lower() for m in rx.finditer(after or "")} - {m.group(1).lower() for m in rx.finditer(before or "")}
    return sorted(t for t in added if not re.search(r"\b" + re.escape(t), role_text)), files[0].name


def _lead_title(text: str):
    """The title phrase a Summary or headline opens with ('Senior Technical Program Manager | Cloud ...' or 'Engineering Manager with
    13+ years ...'), or None when the text does not open with one."""
    t = re.sub(r"^[^A-Za-z]+", "", clean_pdf(text or "")).strip()
    ph = re.split(r"\s+(?:with|\||-|\u2013|\u2014|,|who|delivering|leading|driving|owning)\s+|[|,.:;]", t, maxsplit=1)[0].strip()
    if 1 <= len(ph.split()) <= 7 and re.search(r"manager|engineer|leader|director|lead\b|analyst|architect|program|project", ph, re.I):
        return ph
    return None


def title_change(before: str, after: str, resume_text: str, pol: dict = None):
    """(new title phrase, reason) when a reword changes the opening title into one that claims more or something different than any
    title of yours: a level word (principal, director...) or a people-management noun (manager...) that is new to the title and absent
    from every evidence title and every role title in the resume. None otherwise. Measured on 333 existing Stage 3 edits: 6 flagged
    (the 2 JD6 'Principal' cards, three 'Engineering Manager' cards, JD8's 'Project Manager'), 0 false alarms in the other 145."""
    pol = pol or load_policy()
    b, a = _lead_title(before), _lead_title(after)
    if not a or not b or _tnorm(a) == _tnorm(b):
        return None
    known = {_tnorm(x) for m in evidence_titles() for x in m["options"] + m["corporate"]} | {_tnorm(r["resume_title"]) for r in role_title_report(resume_text, pol)}
    known_words = {w for t in known for w in t.split()}
    bw = set(_tnorm(b).split())
    new = set(_tnorm(a).split()) - bw
    levels = sorted(w for w in new if w in set(pol["title_level_words"]) and w not in known_words)
    people = sorted(w for w in new if w in set(pol["title_people_words"]) and not any(_tnorm(a) in t for t in known))
    if levels:
        return a, "the level word " + ", ".join(f'"{w}"' for w in levels) + " is new to your titles"
    if people and not any(_tnorm(a) in t for t in known):
        return a, "it is not a title in your evidence"
    return None


def skills_section_span(text: str):
    """(start, end) character offsets of the Technical Stack / Skills section, or None."""
    t = text or ""
    m = re.search(r"(?im)^(?:TECHNICAL (?:STACK|SKILLS)|SKILLS|TECHNOLOGIES|TOOLS(?: & TECHNOLOGIES)?)[ \t]*$", t)
    if not m:
        return None
    e = re.search(r"(?m)^[A-Z][A-Z &/]{5,}[ \t]*$", t[m.end():])
    return m.end(), m.end() + (e.start() if e else min(1500, len(t) - m.end()))


def skill_terms(txt: str) -> list:
    """The tool and technology names in a skills block, nested ones included: 'AWS (EKS, Lambda)' gives AWS, EKS, Lambda;
    'Spring Boot / MVC' gives Spring Boot, MVC; 'Label:' prefixes are dropped."""
    flat = re.sub(r"\s+", " ", clean_pdf(txt or ""))
    flat = re.sub(r"\b(?:[A-Z][A-Za-z&/ -]{2,38}):\s", "| ", flat)
    out = []
    for part in re.split(r"[|;]", flat):
        depth, cur, pieces = 0, "", []
        for ch in part:
            if ch == "(":
                depth += 1
                cur += "("
            elif ch == ")":
                depth = max(0, depth - 1)
                cur += ")"
            elif ch == "," and depth == 0:
                pieces.append(cur)
                cur = ""
            else:
                cur += ch
        pieces.append(cur)
        for p in pieces:
            inner = re.findall(r"\(([^)]*)\)", p)
            segs = [re.sub(r"\([^)]*\)", "", p)] + [s for i in inner for s in i.split(",")]
            for seg in segs:
                for sub in re.split(r"\s+/\s+|(?<=[a-z])/(?=[A-Z])", seg):
                    sub = sub.strip(" .;:()*")
                    if sub:
                        out.append(sub)
    return out


def _skill_keys(item: str, pol: dict) -> list:
    generic = set(pol["skill_generic_words"])
    keys = []
    for tk in re.findall(r"[A-Za-z][A-Za-z0-9.+#-]*", item):
        lw = tk.lower().strip(".")
        if lw in generic or len(lw) < 3:
            continue
        if re.search(r"[A-Z].*[A-Z]|[0-9.]", tk) or tk[0].isupper():
            keys.append(tk)
    return keys


_VOCAB = {"key": None, "vocab": set()}


def evidence_vocab() -> set:
    """Every word in the evidence files, LinkedIn profile included, lowercase, with dotted and plural forms folded in."""
    files = sorted(EVIDENCE.glob("*.md")) if EVIDENCE.exists() else []
    key = tuple((p.name, p.stat().st_mtime) for p in files)
    if _VOCAB["key"] != key:
        v = set()
        for p in files:
            toks = re.findall(r"[a-z0-9][a-z0-9.+#_-]*", p.read_text(encoding="utf-8", errors="replace").lower())
            for a, b in zip(toks, toks[1:]):               # "Cosmos DB" is CosmosDB; "Node JS" is NodeJS
                v.add(re.sub(r"[^a-z0-9]", "", a + b))
            for tk in toks:
                tk = tk.strip(".-_")
                v.update({tk, tk.replace(".", ""), tk.replace("-", ""), tk.rstrip("s")})
                for part in re.split(r"[-_]", tk):             # "NOE-Bitbucket" is Bitbucket; "Postgres-15" is Postgres
                    if part:
                        v.update({part, part.rstrip("s")})
                if "." in tk:                                   # "spec.yml" mentions yml (YAML); parts under 3 letters ("js") are skipped
                    v.update(x for x in tk.split(".") if len(x) >= 3)
        _VOCAB["key"], _VOCAB["vocab"] = key, v
    return _VOCAB["vocab"]


def unverified_skills(text: str, pol: dict = None) -> list:
    """Skills items whose distinctive words are missing from every evidence file. Information, not a verdict: the tool may be
    real and just not written down. Checked by whole word, with aliases (Postgres/PostgreSQL) and plurals (ADR/ADRs). An item is
    flagged when ANY distinctive word is absent, so 'Microsoft SQL Server' is caught although 'SQL' is mentioned."""
    pol = pol or load_policy()
    vocab, aliases, out, seen = evidence_vocab(), pol["skill_aliases"], [], set()
    if not vocab:
        return []

    def present(tk):
        lw = tk.lower().strip(".")
        forms = {lw, lw.replace(".", ""), lw.replace("-", ""), lw.rstrip("s")} | {a for a in aliases.get(lw, [])}
        return bool(forms & vocab)

    for item in skill_terms(text):
        keys = _skill_keys(item, pol)
        if keys and not all(present(k) for k in keys):
            sig = item.lower()
            if sig not in seen:
                seen.add(sig)
                out.append(item)
    return out


def role_label(key: str, pol: dict = None) -> str:
    """The display name for a role key (the evidence file-name substring from role_map), from `role_labels` in the policy."""
    pol = pol or load_policy()
    return (pol.get("role_labels") or {}).get(key) or key.replace("_", " ").title()


def cited_roles(evidence_field: str, pol: dict = None) -> list:
    """The roles whose evidence files a Stage 4 card cites. Two roles for one role-specific bullet means it welds two employers'
    work together: a real card combined one employer's test tooling with another's internship prototype firmware into
    'Engineered embedded firmware ... deployed in clinical settings'."""
    pol = pol or load_policy()
    found = set()
    for cited, actual in resolve_cited_files(evidence_field):
        name = (actual or cited).lower()
        for _kw, fs in pol["role_map"]:
            if fs in name:
                found.add(fs)
    return sorted(found)


def prompt_rules() -> str:
    return """# Evidence rules (the evidence files are a curated single source of truth)
- Every number you propose must be a value from a Number Ledger or Verified Facts table. Quote the row. A number that is only in narrative text is not verified; do not use it.
- Prefer wording from "Approved Claim Copy" verbatim. It is already vetted.
- The Do-Not-Claim tables are binding. Never propose a claim that matches one. If a gap can only be closed by a banned claim, list it under True Gaps and give the table's "Say instead" wording if it has one.
- Cite as: File.md, then the section or table row (for example: Employer_A.md, Number Ledger, "Platform Resilience").
- The LinkedIn profile is self-reported and is not evidence. Never cite it.
- If an evidence file conflicts with the current resume (dates, figures, titles, scope), list it under a final heading containing the word Flags."""


# ── Summary proof candidates ─────────────────────────────────────────────────
# Words that mark a ledger row as evidence of a business metric. Keys are the metric names style_rules.ROLE_METRICS
# gives each role family; an unknown name falls back to its own words.
PROOF_KEYWORDS = {
    "time to market": ["time-to-market", "time to market", "release", "cycle", "cutover", "faster", "accelerat", "lead time",
                       "delivery time", "velocity", "onboarding", "turnaround"],
    "cost efficiency": ["cost", "saving", "budget", "spend", "licens", "consolidat"],
    "risk reduction": ["risk", "defect", "incident", "audit", "violation", "compliance", "recurrence", "regression",
                       "outage", "rollback", "mttr", "recovery", "non-people", "exposure"],
    "revenue growth": ["revenue", "sales", "growth"], "product adoption": ["adoption", "engagement", "uptake", "active user"],
    "customer retention": ["retention", "churn", "renewal"],
    "engineering productivity": ["productivity", "velocity", "throughput", "automation", "build", "deploy", "cycle"],
    "platform reliability": ["uptime", "availability", "reliab", "incident", "mttr", "latency", "outage", "resilien"],
    "availability": ["uptime", "availability", "resilien", "failover"],
    "scalability": ["scal", "throughput", "tps", "volume", "capacity", "concurren"],
    "compliance": ["compliance", "audit", "pci", "hipaa", "gdpr", "regulat", "violation"],
    "financial exposure avoided": ["exposure", "loss", "fraud", "penalt", "fine"],
    "operational cost": ["cost", "saving", "licens", "consolidat"], "infrastructure cost optimization": ["cost", "infrastructure", "cloud", "licens"],
    "service reliability": ["uptime", "availability", "incident", "mttr", "reliab"],
    "business continuity": ["continuity", "recovery", "failover", "resilien", "rollback"],
    "productivity gains": ["productivity", "hours", "saved", "automation"], "revenue impact": ["revenue", "adoption", "growth"],
    "decision efficiency": ["decision", "report", "dashboard", "insight"], "cost savings": ["cost", "saving", "licens", "consolidat"],
    "operational efficiency": ["efficien", "throughput", "cycle", "automation", "manual"],
}
_NOT_ACHIEVED = re.compile(r"\b(target|projected|forecast|planned|estimate|goal|design)\b", re.I)


_RESULT_VERBS = re.compile(r"\b(reduc\w*|increas\w*|improv\w*|eliminat\w*|cut|cutting|accelerat\w*|optimi[sz]\w*|attain\w*|"
                           r"achiev\w*|remediat\w*|sav\w*|lower\w*|faster)\b", re.I)


def proof_candidates(business_metrics: list, idx: dict, highlights_text: str = "", per_metric: int = 2) -> list:
    """Verified numbers that could be the Summary's ONE proof, grouped by the business metric the role is judged on.
    Two sources: Approved Claim Copy sentences (your vetted wording, where the achievements live: only 19 of 105
    ledger cells carry a unit number and most are scale or baselines) and ledger rows. Only ACHIEVED results: anything
    that says target, projected, forecast, planned or design is skipped (an uptime figure may be a design target in your ledger).
    A number already in Career Highlights is ranked last, because the Summary must not repeat it."""
    hl = {k for k, _ in tokens(highlights_text, metric_only=True)}
    profile = {s_["file"] for s_ in idx["sources"] if s_["tier"] == "profile"}
    pool = []                                    # (label, text, tokens, file, section, from_approved)
    for c in idx["approved"]:
        toks = [(k, raw) for k, raw in tokens(strip_years(c["text"]), metric_only=True)
                if not _BASELINE.search(c["text"][max(0, c["text"].find(raw) - 70): c["text"].find(raw) + len(raw) + 40])]
        if toks and not _NOT_ACHIEVED.search(c["text"]):
            pool.append((c["topic"] or c["text"][:60], c["text"], toks, c["file"], c["topic"], True))
    for r in idx["ledger"]:
        toks = tokens(r["value"], metric_only=True)
        if r["file"] in profile or not toks or _NOT_ACHIEVED.search(f"{r['metric']} {r['value']}"):
            continue
        pool.append((r["metric"], f"{r['metric']}: {r['value']}. {r['context']}", toks, r["file"], r["section"], False))
    out, used = [], set()
    for bm in business_metrics:
        kws = PROOF_KEYWORDS.get(bm) or re.findall(r"[a-z]{4,}", bm.lower())
        rows = []
        for label, text, toks, file, section, approved in pool:
            if not any(k in text.lower() for k in kws):
                continue
            best = next((t for t in toks if t[0].endswith(("%", "x"))), toks[0])
            key = best[0]
            score = (3 if key.endswith(("%", "x")) else 2 if key.startswith("$") else 1) \
                + (0 if key in hl else 3) + (2 if approved else 0) + (2 if _RESULT_VERBS.search(text) else 0)
            rows.append((score, label, text, best[1], file, section, approved, key))
        for score, label, text, raw, file, section, approved, key in sorted(rows, key=lambda x: -x[0]):
            if (file, text[:60]) in used:
                continue
            used.add((file, text[:60]))
            # an approved sentence is shown whole; a ledger row shows its label and its own value text ("~80% reduction")
            # A ledger row keeps its scope note: "Deployment Lead Time Reduction ~80%" is "(Platform/Pipeline scope only)" in the
            # Employer A ledger, and a Summary that drops that limit overclaims.
            if approved:
                shown = text[:240]
            else:
                row = next((r for r in idx["ledger"] if r["file"] == file and r["metric"] == label), None)
                shown = f"{label}: {row['value']}" + (f" ({row['context'][:120]})" if row and row["context"] else "") if row else label
            out.append({"for": bm, "metric": label[:90], "value": raw, "file": file, "section": section,
                        "text": shown, "in_highlights": key in hl})
            if sum(1 for o in out if o["for"] == bm) >= per_metric:
                break
    return out


# ── Role dates ───────────────────────────────────────────────────────────────
_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+(?:\d{1,2},\s*)?20\d\d"
_RANGE = re.compile(rf"({_MON})\s*(?:to|\u2013|\u2014|-)\s*(Present|present|{_MON})")


def _ym(s: str):
    m = re.match(r"([A-Za-z]{3})[a-z]*\.?,?\s+(?:\d{1,2},\s*)?(20\d\d)", s.strip())
    return (int(m.group(2)), _MONTHS[m.group(1).lower()]) if m else None


def _fmt(ym) -> str:
    return "present" if ym is None else f"{list(_MONTHS)[ym[1] - 1].title()} {ym[0]}"


def evidence_role_dates() -> list:
    """Tenure the evidence states: a 'Dates' row in a role table (Employer A: Jan 2019 to Jun 2022; Employer B: Jan 2017 to Dec 2018;
    Project Y: Mar 2024 to present), a Employer C-style 'working dates (Jan 2012 - Dec 2012)', and the Project X file's
    'work records dating from at least October 2026'. [{file, start, end, kind}]; end None means present."""
    out = []
    for p in sorted(EVIDENCE.glob("*.md")) if EVIDENCE.exists() else []:
        text = p.read_text(encoding="utf-8", errors="replace")
        if _tier(text) == "profile":
            continue
        for t in parse_tables(text):
            for r in t["rows"]:
                if r and r[0].strip().lower() == "dates" and len(r) > 1:
                    m = _RANGE.search(r[1])
                    if m:
                        out.append({"file": p.name, "start": _ym(m.group(1)), "end": None if m.group(2).lower() == "present" else _ym(m.group(2)), "kind": "dates row"})
        flat = re.sub(r"\s+", " ", clean_cell(text))
        m = re.search(rf"working dates[^.]{{0,25}}?({_MON})\s*(?:to|\u2013|\u2014|-)\s*({_MON})", flat, re.I)
        if m:
            out.append({"file": p.name, "start": _ym(m.group(1)), "end": _ym(m.group(2)), "kind": "working dates"})
        m = re.search(rf"records dating from at least ({_MON})", flat, re.I)
        if m:
            out.append({"file": p.name, "start": _ym(m.group(1)), "end": None, "kind": "records only from"})
    return out


def date_ranges(text: str) -> list:
    """Every 'Mon YYYY - Mon YYYY|Present' range in a text, including one wrapped across two lines."""
    t = re.sub(r"\s*[\u2013\u2014-]\s*\n\s*", " \u2013 ", clean_pdf(text))
    return [(_ym(m.group(1)), None if m.group(2).lower() == "present" else _ym(m.group(2))) for m in _RANGE.finditer(t)]


def role_date_conflicts(resume_text: str, pol: dict = None) -> list:
    """Resume role dates that claim more than the evidence supports: a start EARLIER than the evidence's, or an end LATER.
    Deterministic, so the date conflicts no longer depend on which ones a given Stage 4 run happens to mention (an earlier
    JD1 run listed Project X and Project Y dates; the next run listed neither, and the evidence had not changed)."""
    pol = pol or load_policy()
    text = re.sub(r"\s*[\u2013\u2014-]\s*\n\s*", " \u2013 ", clean_pdf(resume_text))        # a date range wrapped across two lines
    ev_dates = evidence_role_dates()
    out, seen = [], set()
    for ln in text.splitlines():
        m = _RANGE.search(ln)
        if not m:
            continue
        rs, re_ = _ym(m.group(1)), (None if m.group(2).lower() == "present" else _ym(m.group(2)))
        scope = scope_for(ln, pol)
        if not scope:
            continue
        for e in ev_dates:
            if not any(f in e["file"].lower() for f in scope) or e["start"] is None:
                continue
            problems = []
            if rs and rs < e["start"]:
                problems.append("starts earlier")
            if e["kind"] != "records only from" and e["end"] is not None and (re_ is None or (re_ and re_ > e["end"])):
                problems.append("ends later")
            sig = (e["file"], m.group(0))
            if problems and sig not in seen:
                seen.add(sig)
                role = re.sub(r"\s+", " ", ln[:ln.find(m.group(0))]).strip(" |-")
                role = role if len(role) <= 80 else role[:80].rsplit(" ", 1)[0]
                out.append({"role": role, "resume": f"{_fmt(rs)} to {_fmt(re_)}", "file": e["file"], "kind": e["kind"], "pair": (rs, re_),
                            "evidence": (f"records only from {_fmt(e['start'])}; the file asks you to confirm start date, title and engagement type"
                                         if e["kind"] == "records only from" else f"{_fmt(e['start'])} to {_fmt(e['end'])}"),
                            "problems": problems})
    return out


# ── Role titles ──────────────────────────────────────────────────────────────
_TITLE_WORDS = re.compile(r"engineer|manager|director|\blead\b|analyst|president|specialist|architect|officer|intern|developer|consultant|programmer", re.I)


def _tnorm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def evidence_titles() -> list:
    """Per role file: the titles the evidence says you MAY use (the 'Title (Resume & Public Profiles)' row, options
    separated by ' / '), the corporate title it names in parentheses, and any 'official HR title' sentence.
    It is a menu, not an instruction: each resume variant can pick a different one with its own reasoning."""
    out = []
    for p in sorted(EVIDENCE.glob("*.md")) if EVIDENCE.exists() else []:
        text = p.read_text(encoding="utf-8", errors="replace")
        if _tier(text) == "profile":
            continue
        opts, corp = [], []
        for t in parse_tables(text):
            for r in t["rows"]:
                if r and re.match(r"title\s*\(resume", r[0], re.I) and len(r) > 1:
                    for m in re.finditer(r"\([^)]*?\btitle\s*:\s*([^)]+)\)", r[1], re.I):
                        corp += [x.strip(" .") for x in m.group(1).split(" / ") if x.strip()]
                    main = re.sub(r"\([^)]*?\btitle\s*:[^)]*\)", "", r[1], flags=re.I)
                    opts += [x.strip(" .") for x in main.split(" / ") if x.strip()]
        if not opts:
            continue
        flat = re.sub(r"\s+", " ", clean_cell(text))
        official = [m.group(1).strip(" .*") for m in re.finditer(
            r"(?:official (?:HR )?title is|designate your position as)\s*([A-Z][A-Za-z &/\-]{5,60}?)\s*(?:in the|without|[.,;]|$)", flat)]
        out.append({"file": p.name, "options": opts, "corporate": corp, "official": official})
    return out


def role_title_report(resume_text: str, pol: dict = None) -> list:
    """Each resume role's title against the evidence's title menu for that role. status: 'listed' (an option or the corporate
    title), 'not listed' (with the closest listed title), or 'listed, official differs' (the file also states an official HR
    title that is different, as the Employer C file does for a background-check note). Informational: no title is mandated.
    Example: a resume says 'Associate Vice President' where the evidence lists the corporate officer title 'Assistant Vice President'."""
    pol = pol or load_policy()
    menus = evidence_titles()
    text = re.sub(r"\s*[\u2013\u2014-]\s*\n\s*", " \u2013 ", clean_pdf(resume_text))
    out = []
    for ln in text.splitlines():
        if _BULLET.match(ln) or not _RANGE.search(ln):
            continue
        scope = scope_for(ln, pol)
        head = ln[:_RANGE.search(ln).start()]
        parts = re.split(r"\s+[-\u2013\u2014]\s+", head, maxsplit=1)
        if not scope or len(parts) < 2 or not _TITLE_WORDS.search(parts[0]):
            continue
        rtitle = re.sub(r"\s+", " ", parts[0]).strip(" |")
        for m in menus:
            if not any(f in m["file"].lower() for f in scope):
                continue
            listed = {_tnorm(x) for x in m["options"] + m["corporate"]}
            rparts = [_tnorm(x) for x in rtitle.split(" / ")]
            if all(x in listed for x in rparts) or _tnorm(rtitle) in listed:
                status = "listed"
                if m["official"] and not any(_tnorm(o) == _tnorm(rtitle) for o in m["official"]):
                    status = "listed, official differs"
            else:
                status = "not listed"
            words = set(_tnorm(rtitle).split())
            best = max(m["options"] + m["corporate"], key=lambda o: len(words & set(_tnorm(o).split())) / max(1, len(words | set(_tnorm(o).split()))))
            out.append({"role": re.sub(r"\s+", " ", parts[1])[:60].strip(" |"), "file": m["file"], "resume_title": rtitle,
                        "status": status, "options": m["options"], "corporate": m["corporate"], "official": m["official"], "closest": best})
    return out


# ── Cited file names ─────────────────────────────────────────────────────────
def _name_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\.md$", "", s.lower()))


def resolve_cited_files(field: str) -> list:
    """[(cited_text, actual_file_or_None)] for the .md names in a Stage 4 'Evidence Source' field.
    Compared with case, spaces, dots, underscores and hyphens ignored, longest candidate first, because the model
    does not copy names exactly: it wrote 'Project X.md' (with a space) for the file
    Project_X.md (ingest strips spaces and parentheses), and a last-word parser turned that into 'X.md' and
    wrongly called four valid cards stale."""
    real = {_name_key(p.name): p.name for p in EVIDENCE.glob("*.md")} if EVIDENCE.exists() else {}
    out = []
    for m in re.findall(r"([A-Za-z0-9][\w.\- ]*?\.md)", (field or "").replace("`", " ")):
        toks = m.split()
        hit = None
        for k in range(len(toks), 0, -1):                 # "A B.md" before "B.md"
            cand = " ".join(toks[-k:])
            if _name_key(cand) in real:
                hit = (cand, real[_name_key(cand)])
                break
        out.append(hit or (toks[-1], None))
    return out


# ── Variant audit ────────────────────────────────────────────────────────────
def audit_variant(path: Path, idx: dict, pol: dict) -> dict:
    a = audit_text(path.read_text(encoding="utf-8", errors="replace"), idx, pol)
    c = {"LEDGER": 0, "APPROVED": 0, "NARRATIVE": 0, "PROFILE_ONLY": 0, "NOT_FOUND": 0, "CONTEXT_MISMATCH": 0}
    for m in a["metrics"]:
        c[m["status"]] += 1
    return {"name": path.stem, "counts": c, "unbacked": [m for m in a["metrics"] if not m["verified"]], "dnc": a["dnc"],
            "metrics": a["metrics"]}


def audit_all() -> list:
    idx, pol = load_index(), load_policy()
    return [audit_variant(p, idx, pol) for p in sorted(RESUMES.glob("*.md"))] if idx and RESUMES.exists() else []


def _print_build(idx):
    print(f"{'file':44}{'tier':9}{'~tok':>7}{'tables':>7}{'ledger':>7}{'dnc':>5}{'approved':>9}")
    for s in idx["sources"]:
        print(f"{s['file'][:43]:44}{s['tier']:9}{s['tokens_est']:7}{s['tables']:7}{s['ledger_rows']:7}{s['dnc_rows']:5}{s['approved']:9}")
        if s["tier"] != "profile" and s["tables"] and not (s["ledger_rows"] or s["dnc_rows"]):
            print(f"   WARNING: {s['tables']} table(s) but none recognised as a ledger or do-not-claim table; check the header names")
    tot = sum(s["tokens_est"] for s in idx["sources"] if s["tier"] != "profile")
    print(f"\n{len(idx['ledger'])} ledger rows, {len(idx['do_not_claim'])} do-not-claim rules, {len(idx['approved'])} approved claims; "
          f"~{tot:,} tokens of evidence (profile excluded)")
    d = idx.get("_diff") or {}
    if d and (d["added"] or d["removed"] or d["changed"]):
        print("\nLedger changes since the last build:")
        for k in ("changed", "added", "removed"):
            for line in d[k][:15]:
                print(f"   {k:8} {line}")


def _main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "build":
        if not EVIDENCE.exists() or not list(EVIDENCE.glob("*.md")):
            print("No evidence files in output/evidence/. Run ingest_evidence.sh first.", file=sys.stderr)
            return 1
        _print_build(build())
    elif cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "prompt-rules":
        print(prompt_rules())
    elif cmd == "prompt-files":
        print("\n".join(prompt_files()))
    elif cmd == "audit":
        res = audit_all() if "--all" in argv else None
        idx, pol = load_index(), load_policy()
        if idx is None:
            print("No evidence index. Run ingest_evidence.sh first.", file=sys.stderr)
            return 1
        if res is None:
            res = [audit_variant(RESUMES / f"{argv[1]}.md", idx, pol)]
        print(f"{'variant':42}{'ledger':>7}{'approved':>9}{'other*':>7}{'narr.':>6}{'profile':>8}{'none':>5}{'dnc':>5}")
        for r in res:
            c = r["counts"]
            print(f"{r['name'][:41]:42}{c['LEDGER']:7}{c['APPROVED']:9}{c['CONTEXT_MISMATCH']:7}{c['NARRATIVE']:6}{c['PROFILE_ONLY']:8}{c['NOT_FOUND']:5}{len(r['dnc']):5}")
        print("* other = your evidence has that number, but for a different measurement")
        if len(res) == 1:
            for m in res[0]["unbacked"]:
                print(f"  unbacked: {m['raw']:10} x{m['count']}  {STATUS_TEXT[m['status']]}")
            for h in res[0]["dnc"]:
                print(f"  DO-NOT-CLAIM [{h['file']}] {h['claim'][:90]}\n      line: {h['text'][:110]}\n      say instead: {h['say_instead'][:110]}")
    elif cmd == "check":
        idx = load_index()
        print(json.dumps(audit_text(" ".join(argv[1:]), idx), indent=1) if idx else "no index")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
