#!/usr/bin/env python3
"""
scripts/trim_review.py — Stage 7: bullet-count trimming for an already-
finalized resume.

DIFFERENT PROBLEM FROM STAGE 5/6, on purpose
Stage 5/6 decide whether to ADD or REWORD text, using judgments an LLM
already made in Stages 2-4. This stage decides what to CUT from a resume
that's already been merged and manually reviewed -- there's no Stage 2-4
opinion to replay, because "should we have 11 bullets under one role"
was never a question Stage 2-4 was asked. So this stage computes its own
scores directly from two things that already exist and need no LLM call:
  - the real JD text (input/other/{JD}.txt)
  - the finalized resume (output/review_resume/{JD}_new_resume.md)

WHY STILL ZERO LLM CALLS
"Which bullet is most relevant to this JD" sounds like it wants a model,
but the actual decision is small enough to make with counting: how many
of the JD's own emphasized words does a bullet contain, does it have a
quantified metric, is it a near-duplicate of a stronger bullet in the
same role, and is it unusually long for a quick skim. None of that needs
judgment beyond arithmetic once the JD's important terms are extracted --
and that extraction is frequency-counting on the JD's own text, not a
model's opinion of what's important.

CAP, NOT A WHOLESALE REWRITE
Only roles with MORE than `--cap` (default 5) bullets get trim candidates
at all. A role with 5 or fewer bullets is left alone entirely -- this is
a "cut the excess" tool, not a full bullet-quality audit.

DELETION SAFETY
Deletions are ALWAYS proposed, never applied without an explicit approved
id list (`--apply-ids`), mirroring Stage 6 exactly. Nothing here can
delete a bullet nobody looked at. The full pre-trim version is always
recoverable by re-running Stage 5 (`synthesize_resume.py {JD} --force`),
so no separate backup file is kept.

USAGE
    python3 scripts/trim_review.py JD3 --manifest-only
    python3 scripts/trim_review.py JD3 --apply-ids "trim-1-2,trim-1-5"
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from synthesize_resume import KNOWN_SECTIONS, is_role_header, strip_markup  # single source of truth, not a second copy

ROOT = Path(__file__).parent.parent.resolve()
INPUT_OTHER = ROOT / "input" / "other"
OUTPUT = ROOT / "output"
REVIEW_RESUME_DIR = OUTPUT / "review_resume"

STOPWORDS = set("""
a an the and or but of to in on for with at by from as is are was were be
been being this that these those you your our we they it its their his
her not no can will would should could may might must have has had do
does did i he she them then than than so if into out up down over under
about across per via etc also more most other another same such only
just very each every all any both either neither
""".split())

DEFAULT_CAP = 5


# ── JD keyword extraction ───────────────────────────────────────────────────
def extract_jd_keywords(jd_text: str, top_words: int = 60, top_bigrams: int = 30) -> dict:
    """Weighted {term: weight}, built purely from frequency in the JD's own
    text -- no external list, no model call. Bigrams get a slight weight
    boost over single words since a repeated two-word phrase ('program
    management', 'cross-functional') is usually a more specific signal
    than either word alone."""
    lower = jd_text.lower()
    words = re.findall(r"[a-z][a-z0-9+/#\-]{2,}", lower)
    words = [w for w in words if w not in STOPWORDS]
    freq = Counter(words)
    weights = {w: float(c) for w, c in freq.most_common(top_words)}

    bigrams = [f"{a} {b}" for a, b in zip(words, words[1:])]
    bigram_freq = Counter(bigrams)
    for bg, c in bigram_freq.most_common(top_bigrams):
        if c >= 2:
            weights[bg] = c * 1.5
    return weights


def score_against_jd(text: str, jd_keywords: dict) -> float:
    """Word-boundary matching, not plain substring. Plain 'term in text'
    produced two confirmed false positives in real runs: 'engine' credited
    for appearing inside 'engineering', and 'work' credited for appearing
    inside 'frameworks' -- both coincidental, neither meaning anything
    about actual relevance. \\b on both ends of the (possibly multi-word)
    term fixes both without changing genuine matches: 'ai-forward' still
    matches 'ai-forward operator' since word boundaries sit at the phrase's
    edges either way, but 'work' no longer matches inside 'framework'."""
    if not text or not jd_keywords:
        return 0.0
    lower = text.lower()
    total = 0.0
    for term, w in jd_keywords.items():
        if re.search(r"\b" + re.escape(term) + r"\b", lower):
            total += w
    return round(total, 1)


# ── Resume parsing: PROFESSIONAL EXPERIENCE roles + bullets, with line spans
# so a deletion can remove the exact original lines (including any wrapped
# continuation lines merged into one logical bullet during scoring). ───────
def parse_experience_roles(resume_text: str):
    """Scans PROFESSIONAL EXPERIENCE (split into one group per role header)
    AND CAREER HIGHLIGHTS (treated as a single group -- it has no role
    headers to split on, just a flat bulleted list). CAREER HIGHLIGHTS was
    missing entirely until a real resume showed why that's a problem: Stage
    4 inserted 2 new bullets there, pushing it to 9 bullets -- the single
    most over-cap section in the whole resume -- while every Professional
    Experience role sat at or under 5. The trim page correctly reported
    "0 roles over cap" by its own logic and still missed the actual
    bloat, because the section that needed trimming wasn't a place this
    function ever looked."""
    lines = resume_text.split("\n")
    roles, current, active_section = [], None, None
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s in KNOWN_SECTIONS:
            if current:
                roles.append(current)
                current = None
            active_section = s if s in ("PROFESSIONAL EXPERIENCE", "CAREER HIGHLIGHTS") else None
            if active_section == "CAREER HIGHLIGHTS":
                current = {"role": "CAREER HIGHLIGHTS", "bullets": []}
            continue
        if active_section is None:
            continue
        if active_section == "PROFESSIONAL EXPERIENCE" and is_role_header(s):
            if current:
                roles.append(current)
            current = {"role": s, "bullets": []}
        elif s.startswith("•") and current is not None:
            current["bullets"].append({"text": s.lstrip("•").strip(), "line_start": i, "line_end": i})
        elif current is not None and current["bullets"] and s:
            # wrapped continuation of the previous bullet -- extend its text
            # and its line span, same merge rule md_to_pdf.py's renderer uses
            current["bullets"][-1]["text"] += " " + s
            current["bullets"][-1]["line_end"] = i
    if current:
        roles.append(current)
    return roles, lines


# ── Scoring ──────────────────────────────────────────────────────────────────
def score_bullets(bullets: list, jd_keywords: dict):
    texts = [b["text"] for b in bullets]
    word_sets = [set(re.findall(r"[a-z]{4,}", t.lower())) for t in texts]
    scored = []
    for i, t in enumerate(texts):
        ats = score_against_jd(t, jd_keywords)
        has_metric = bool(re.search(r"\d", t))
        word_count = len(t.split())
        best_overlap, redundant_with = 0.0, None
        for j, other_words in enumerate(word_sets):
            if i == j or not word_sets[i] or not other_words:
                continue
            overlap = len(word_sets[i] & other_words) / len(word_sets[i] | other_words)
            if overlap > best_overlap:
                best_overlap, redundant_with = overlap, j
        is_redundant = best_overlap >= 0.35
        keep_score = ats * 3 + (2 if has_metric else 0) \
            - max(0, word_count - 35) * 0.15 - (5 if is_redundant else 0)
        scored.append({
            "ats_score": ats, "has_metric": has_metric, "word_count": word_count,
            "is_redundant": is_redundant, "redundant_with": redundant_with,
            "keep_score": round(keep_score, 2),
        })
    return scored


def build_reason(s: dict) -> str:
    reasons = []
    if s["ats_score"] == 0:
        reasons.append("no match to this JD's emphasized terms")
    if not s["has_metric"]:
        reasons.append("no quantified metric — harder to verify at a skim")
    if s["is_redundant"]:
        reasons.append("overlaps heavily with a stronger bullet in this role")
    if s["word_count"] > 40:
        reasons.append(f"long ({s['word_count']} words) for a quick recruiter skim")
    if not reasons:
        reasons.append("lowest combined relevance/impact score among this role's bullets")
    return "; ".join(reasons)


def compute_trim_manifest(resume_text: str, jd_keywords: dict, cap: int = DEFAULT_CAP):
    roles, lines = parse_experience_roles(resume_text)
    candidates = []
    for ridx, role in enumerate(roles):
        bullets = role["bullets"]
        if len(bullets) <= cap:
            continue
        scored = score_bullets(bullets, jd_keywords)
        order = sorted(range(len(bullets)), key=lambda i: scored[i]["keep_score"])  # worst first
        cut_idxs = set(order[: len(bullets) - cap])
        for i, b in enumerate(bullets):
            s = scored[i]
            candidates.append({
                "id": f"trim-{ridx}-{i}",
                "role": role["role"],
                "bullet": strip_markup(b["text"]),
                "ats_score": s["ats_score"],
                "has_metric": s["has_metric"],
                "word_count": s["word_count"],
                "keep_score": s["keep_score"],
                "reason": build_reason(s) if i in cut_idxs else "kept — among the top bullets in this role",
                "default_deleted": i in cut_idxs,
                "line_start": b["line_start"],
                "line_end": b["line_end"],
                "role_bullet_count": len(bullets),
            })
    return candidates


def apply_trim(resume_text: str, candidates: list, accepted_ids: set):
    lines = resume_text.split("\n")
    to_delete = [c for c in candidates if c["id"] in accepted_ids]
    # Delete from the bottom up so earlier line indices stay valid.
    for c in sorted(to_delete, key=lambda c: -c["line_start"]):
        del lines[c["line_start"]: c["line_end"] + 1]
    return "\n".join(lines), to_delete


# ── File resolution ──────────────────────────────────────────────────────────
def resolve_resume_path(jd_name: str) -> Path:
    new_path = REVIEW_RESUME_DIR / f"{jd_name}_new_resume.md"
    if new_path.exists():
        return new_path
    old_path = OUTPUT / f"{jd_name}_new_resume.md"  # transition-period fallback
    if old_path.exists():
        return old_path
    raise FileNotFoundError(
        f"No finalized resume found at {new_path} or {old_path} — "
        f"run Review & merge (Stage 5/6) first."
    )


def resolve_jd_path(jd_name: str) -> Path:
    p = INPUT_OTHER / f"{jd_name}.txt"
    if not p.exists():
        raise FileNotFoundError(f"No JD text found at {p}")
    return p


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Stage 7 — trim over-long resume roles against the real JD text.")
    ap.add_argument("jd")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP, help="Max bullets per role before trimming kicks in (default 5)")
    ap.add_argument("--manifest-only", action="store_true")
    ap.add_argument("--apply-ids", default=None)
    args = ap.parse_args()

    try:
        resume_path = resolve_resume_path(args.jd)
        jd_path = resolve_jd_path(args.jd)
    except FileNotFoundError as e:
        if args.manifest_only:
            print(json.dumps({"ok": False, "error": str(e)}))
            return
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    resume_text = resume_path.read_text(encoding="utf-8", errors="replace")
    jd_text = jd_path.read_text(encoding="utf-8", errors="replace")
    jd_keywords = extract_jd_keywords(jd_text)
    candidates = compute_trim_manifest(resume_text, jd_keywords, cap=args.cap)

    if args.manifest_only:
        print(json.dumps({
            "ok": True, "jd": args.jd, "cap": args.cap,
            "roles_over_cap": len({c["role"] for c in candidates}),
            "candidates": candidates,
        }, indent=2))
        return

    if not candidates:
        print(f"No roles exceed {args.cap} bullets for {args.jd} — nothing to trim.")
        return

    if args.apply_ids is None:
        print(f"{len(candidates)} candidate bullets across roles over the {args.cap}-bullet cap. "
              f"Use --manifest-only to inspect, or --apply-ids to delete specific ones.")
        return

    accepted_ids = {i.strip() for i in args.apply_ids.split(",") if i.strip()}
    valid_ids = {c["id"] for c in candidates}
    unknown = accepted_ids - valid_ids
    if unknown:
        print(f"ERROR: unknown id(s): {sorted(unknown)}", file=sys.stderr)
        sys.exit(1)

    new_text, deleted = apply_trim(resume_text, candidates, accepted_ids)
    resume_path.write_text(new_text, encoding="utf-8")
    print(f"OK — {args.jd}: deleted {len(deleted)} bullet(s) from {resume_path.relative_to(ROOT)}")
    for c in deleted:
        print(f"  - [{c['role'][:40]}] {c['bullet'][:70]}...")


if __name__ == "__main__":
    main()
