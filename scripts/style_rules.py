#!/usr/bin/env python3
"""
style_rules.py -- one source for the resume writing rules.

Used two ways, so the prompts and the checks cannot drift apart:
  1. Prompt text: Stage 3/4/8 shell scripts embed the output of
       python3 scripts/style_rules.py --prompt stage3|stage4|cover
  2. Deterministic checks: synthesize_resume.py imports pattern_hits(),
     introduced_hits() and check_summary() for Stage 5/6 flags.

Why these rules (measured on 12 real final resumes, 2026-10-30):
  - Summary repeated a Career Highlights metric in 12/12 (the same headline figure in all 12)
  - Summary was 67 to 109 words, 3 to 4 sentences
  - "at scale" appeared in all 12; em dashes in 5 (one had 6)
Pattern categories follow blader/humanizer's public list (MIT): AI
vocabulary, vague significance, superficial "-ing" commentary, em dashes,
rule of three. Rule of three is prompt-only: resume bullets legitimately
list three tools or teams, so a deterministic check would mostly misfire.

CLI:
  python3 scripts/style_rules.py --prompt stage3 --jd JD7    # Stage 3 prompt text for JD7's role
  python3 scripts/style_rules.py --check FILE.md --jd JD7    # Summary warnings + role metric hint
"""
import re
import sys

SUMMARY_MAX_WORDS = 45
SUMMARY_MAX_SENTENCES = 2
CARD_UNCHECK_AT = 2          # pattern hits introduced by one card -> unchecked by default

# (label, regex). Word-boundary, case-insensitive. Kept deliberately short:
# every entry was checked against real Stage 3/4 output so legitimate resume
# verbs ("streamlined", "delivered", "led") are NOT on it.
AI_VOCAB = [
    ("leverage", r"\bleverag(?:e|ed|es|ing)\b"),
    ("spearhead", r"\bspearhead(?:ed|ing|s)?\b"),
    ("robust", r"\brobust\b"),
    ("seamless", r"\bseamless(?:ly)?\b"),
    ("cutting-edge", r"\bcutting[- ]edge\b"),
    ("transformative", r"\btransformative\b"),
    ("pivotal", r"\bpivotal\b"),
    ("synergy", r"\bsynerg(?:y|ies|istic)\b"),
    ("holistic", r"\bholistic(?:ally)?\b"),
    ("delve", r"\bdelv(?:e|ed|ing)\b"),
    ("foster", r"\bfoster(?:ed|ing|s)?\b"),
    ("empower", r"\bempower(?:ed|ing|s)?\b"),
    ("at scale", r"\bat scale\b"),
    ("measurable impact", r"\bmeasurable impact\b"),
    ("proven track record", r"\bproven track record\b"),
    ("results-driven", r"\bresults[- ]driven\b"),
    ("passionate", r"\bpassionate\b"),
    ("world-class", r"\bworld[- ]class\b"),
    ("best-in-class", r"\bbest[- ]in[- ]class\b"),
    ("dynamic", r"\bdynamic\b(?! (?:pricing|routing|scaling|configuration))"),
]
VAGUE_SIGNIFICANCE = [
    ("key/pivotal/crucial role", r"\b(?:key|pivotal|crucial|critical|vital|instrumental) role\b"),
    ("instrumental in", r"\binstrumental in\b"),
    ("testament to", r"\btestament to\b"),
    ("underscore", r"\bunderscor(?:e|es|ed|ing)\b"),
    ("landscape", r"\b(?:evolving|ever-changing|complex|dynamic) landscape\b"),
]
ING_COMMENTARY = [
    ("trailing -ing commentary", r",\s+(?:highlighting|underscoring|showcasing|reflecting|demonstrating|"
                                 r"fostering|cementing|solidifying)\b"),
]
EM_DASH = [("em dash", r"\u2014")]

ALL_PATTERNS = [(lbl, re.compile(rx, re.IGNORECASE)) for lbl, rx in
                AI_VOCAB + VAGUE_SIGNIFICANCE + ING_COMMENTARY + EM_DASH]

# Role family -> the 3 business metrics that role is judged on (the role/metric
# table adopted 2026-10-31). Order matters: the ROLE TYPE wins over the
# domain, so "Engineering Manager - Platform" is eng_mgmt, not cloud_platform,
# and "Principal Product Manager, AI" is product, not ai_ml.
ROLE_METRICS = [
    ("product", r"\bproduct (manager|management|owner|lead|leader)\b",
     ["revenue growth", "product adoption", "customer retention"]),
    ("program", r"\bprograms?\b|\bproject manag|\bT?PM\b|\bdelivery (lead|manager)\b",
     ["time to market", "cost efficiency", "risk reduction"]),
    ("eng_mgmt", r"\bengineering manager\b|\bhead of engineering\b|\bdirector\b.*\bengineering\b|\bTLM\b",
     ["engineering productivity", "platform reliability", "cost efficiency"]),
    ("security", r"\bsecurity\b|\bcyber|\biam\b|\bidentity\b",
     ["risk reduction", "financial exposure avoided", "compliance"]),
    ("ai_ml", r"\bai\b|\bml\b|machine learning|\bgenai\b|\bllm",
     ["productivity gains", "revenue impact", "model-driven business value"]),
    ("data", r"\bdata\b|\banalytics\b",
     ["revenue impact", "decision efficiency", "cost savings"]),
    ("supply_chain", r"supply chain|logistics|fulfil?ment",
     ["cost per shipment/order", "on-time delivery", "operational efficiency"]),
    ("digital_transformation", r"digital transformation|\btransformation\b",
     ["cost savings", "productivity improvement", "revenue enablement"]),
    ("cloud_platform", r"\bcloud\b|\bplatform\b|\bsre\b|\bdevops\b",
     ["infrastructure cost optimization", "availability", "scalability"]),
    ("it_infra", r"\bit\b|\binfrastructure\b",
     ["operational cost", "service reliability", "business continuity"]),
]
ROLE_LABELS = {"product": "product management", "program": "technical program management",
               "eng_mgmt": "engineering management", "security": "security leadership",
               "ai_ml": "AI/ML leadership", "data": "data and analytics leadership",
               "supply_chain": "supply chain technology", "digital_transformation": "digital transformation",
               "cloud_platform": "cloud and platform leadership", "it_infra": "IT and infrastructure leadership"}
GENERIC_METRICS = ["time to market", "cost", "risk", "reliability", "revenue"]


def role_metrics(title: str) -> dict:
    """{'family': str|None, 'metrics': [3 metrics]} for a JD title."""
    t = title or ""
    for fam, rx, metrics in ROLE_METRICS:
        flags = 0 if fam == "program" and re.search(r"\bT?PM\b", t) else re.IGNORECASE
        if re.search(rx, t, flags):
            return {"family": fam, "label": ROLE_LABELS[fam], "metrics": metrics}
    return {"family": None, "label": None, "metrics": GENERIC_METRICS}


def jd_title_from(arg: str):
    """JD title from a JD name ('JD7'), a JD file path, or raw JD text."""
    import os
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import jd_meta
    except ImportError:
        return None
    m = re.search(r"\b(JD\d+)\b", os.path.basename(arg or ""))
    if m:
        t = jd_meta.get_meta(m.group(1)).get("role_title")
        if t:
            return t
    text = open(arg, encoding="utf-8", errors="replace").read() if arg and os.path.isfile(arg) else (arg or "")
    return jd_meta._title(text)[0] if text else None


METRIC_RE = re.compile(r"\$\s?\d[\d,.]*\s*[KMB]?\+?|\d[\d,.]*\s*%|\b\d+\+|\b\d+\s?[×x]\b")


def pattern_hits(text: str) -> dict:
    """{label: count} for every banned pattern found in text."""
    out = {}
    for lbl, rx in ALL_PATTERNS:
        n = len(rx.findall(text or ""))
        if n:
            out[lbl] = n
    return out


def introduced_hits(before: str, after: str) -> dict:
    """Patterns the change ADDS. A card is not blamed for wording that was
    already in the base resume; only the increase counts."""
    b, a = pattern_hits(before), pattern_hits(after)
    return {k: a[k] - b.get(k, 0) for k in a if a[k] > b.get(k, 0)}


# ── Summary / Career Highlights ──────────────────────────────────────────────
_SECTION_HEAD = re.compile(r"^(SUMMARY|PROFESSIONAL SUMMARY|CORE COMPETENCIES|SKILLS|TECHNICAL STACK|"
                           r"TECHNICAL SKILLS|TOOLS & METHODS|CAREER HIGHLIGHTS|KEY ACHIEVEMENTS|"
                           r"PROFESSIONAL EXPERIENCE|EXPERIENCE|AI PLATFORM PROJECT|PROJECTS|EDUCATION|"
                           r"CERTIFICATIONS)\s*$", re.IGNORECASE)


def sections(resume_text: str) -> dict:
    """{UPPERCASE heading: body text} for the known top-level headings."""
    out, cur, buf = {}, None, []
    for ln in (resume_text or "").splitlines():
        m = _SECTION_HEAD.match(ln.strip().lstrip("#").strip())
        if m:
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1).upper(), []
        elif cur:
            buf.append(ln)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


def _metrics(text: str) -> set:
    return {re.sub(r"\s+", "", m) for m in METRIC_RE.findall(text or "")}


def summary_text(resume_text: str) -> str:
    s = sections(resume_text)
    return s.get("SUMMARY") or s.get("PROFESSIONAL SUMMARY") or ""


def check_summary(resume_text: str) -> list:
    """Document-level warnings for the Summary. Each: {code, message, detail}.
    Empty list = Summary follows the rules."""
    s = sections(resume_text)
    summ = s.get("SUMMARY") or s.get("PROFESSIONAL SUMMARY") or ""
    high = s.get("CAREER HIGHLIGHTS") or s.get("KEY ACHIEVEMENTS") or ""
    if not summ:
        return []
    warns = []
    words = len(summ.split())
    sentences = len(re.findall(r"[.!?](?=\s|$)", summ))
    if words > SUMMARY_MAX_WORDS:
        warns.append({"code": "summary_long", "message": f"Summary is {words} words (limit {SUMMARY_MAX_WORDS})",
                      "detail": words})
    if sentences > SUMMARY_MAX_SENTENCES:
        warns.append({"code": "summary_sentences",
                      "message": f"Summary has {sentences} sentences (limit {SUMMARY_MAX_SENTENCES})",
                      "detail": sentences})
    proof = _metrics(re.sub(r"\b\d+\+?\s*(?:years?|yrs?)\b", "", summ, flags=re.IGNORECASE))
    if len(proof) > 1:
        warns.append({"code": "summary_metric_count",
                      "message": f"Summary carries {len(proof)} metrics ({', '.join(sorted(proof))}); "
                                 f"keep ONE proof and move the rest to Career Highlights",
                      "detail": sorted(proof)})
    shared = sorted(_metrics(summ) & _metrics(high))
    if shared:
        warns.append({"code": "summary_metric_overlap",
                      "message": f"Summary repeats Career Highlights metric(s): {', '.join(shared)}. "
                                 f"A metric may appear in one place only; keep it in whichever it proves best.",
                      "detail": shared})
    hits = pattern_hits(summ)
    if hits:
        warns.append({"code": "summary_patterns",
                      "message": "Summary uses banned pattern(s): " +
                                 ", ".join(f"{k} x{v}" if v > 1 else k for k, v in hits.items()),
                      "detail": hits})
    return warns


def is_summary_insertion(addition_text: str, where: str) -> bool:
    """Stage 4 insertion aimed at the Summary ('Add to SUMMARY: ...' or a
    'Where on Resume' that names the Summary first)."""
    if re.match(r"\s*\+?\s*Add to (?:the )?(?:PROFESSIONAL )?SUMMARY\b", addition_text or "", re.IGNORECASE):
        return True
    return bool(re.match(r"\s*(?:Professional )?Summary\b", where or "", re.IGNORECASE))


# ── Prompt text ──────────────────────────────────────────────────────────────
def _banned_list() -> str:
    return ", ".join(f'"{lbl}"' for lbl, _ in AI_VOCAB)


def proof_pool_line(resume_text: str) -> str:
    """Which numbers in THIS resume may be the Summary's one proof: only those the evidence ledger verifies.
    On JD9 the model chose 25%, a figure that exists only on LinkedIn."""
    try:
        import evidence_index as ev
        idx = ev.load_index()
        if idx is None or not resume_text:
            return ""
        a = ev.audit_text(resume_text, idx)
    except Exception:
        return ""
    ok = [m["raw"] for m in a["metrics"] if m["verified"]]
    bad = [m["raw"] for m in a["metrics"] if not m["verified"]]
    if ok:
        line = f"\n- Numbers in this resume that the candidate's evidence ledger verifies (the only ones allowed as the Summary's proof): {', '.join(ok)}."
    else:
        line = "\n- No number in this resume is verified by the candidate's evidence ledger, so the Summary carries NO proof number (positioning only)."
    if bad:
        line += f" Do NOT use these as the Summary proof (not backed by the ledger): {', '.join(bad)}."
    return line


def prompt_block(stage: str, jd_title: str = None, resume_text: str = None) -> str:
    language = f"""# Writing rules (apply to every sentence you propose)
- Prefer the candidate's own wording from the resume and the cited evidence
  files over new phrasing. Reword only as far as the JD language requires.
- No em dashes. Use a comma, semicolon, colon, or period instead.
- Do not use: {_banned_list()}.
- No vague significance: "played a key/pivotal/crucial role", "instrumental in",
  "testament to", "underscores", "evolving landscape".
- No reflexive groups of three (three adjectives or three abstract nouns).
  List only the items that are true and relevant. Exception: Summary
  sentence 1 may name three strengths when each maps to one of the JD's top
  three capabilities.
- No trailing "-ing" commentary that adds opinion rather than fact
  (", highlighting...", ", showcasing...", ", demonstrating...", ", fostering...")."""

    summary = f"""# Summary vs Career Highlights
- SUMMARY: at most {SUMMARY_MAX_SENTENCES} sentences and {SUMMARY_MAX_WORDS} words. Sentence 1 is positioning
  (role, years, domain). Sentence 2 is one hook: the single differentiator
  most relevant to THIS JD.
- The SUMMARY may carry ONE proof metric (a real number already in the
  resume). It must not also appear in CAREER HIGHLIGHTS: a number lives in
  one place only. Every other metric belongs in CAREER HIGHLIGHTS.
- Lead with outcome and ownership, not responsibilities.
- Keywords are relocated, never deleted: if tightening the SUMMARY drops a
  JD keyword, place it in CAREER HIGHLIGHTS or PROFESSIONAL EXPERIENCE."""

    rm = role_metrics(jd_title)
    role_line = (f"\n- Role type: {rm['label']}. The job posting's title is \"{jd_title}\": that is the role you are tailoring FOR, "
                 f"never a title the candidate holds. Frame the proof in one of "
                 f"the business metrics this role is judged on: {', '.join(rm['metrics'])}. Use one only if the "
                 f"resume or evidence has a real number for it; never invent or estimate one."
                 if rm["family"] else
                 f"\n- Frame the proof as a business outcome (for example {', '.join(rm['metrics'])}), "
                 f"using only a real number from the resume or evidence.")
    if stage == "stage3":
        extra = """- Edits to the SUMMARY may only shorten it toward these limits or swap in
  JD terminology; never lengthen it. It may keep or swap its one proof
  metric, but never add a second."""
        return f"{summary}{role_line}{proof_pool_line(resume_text)}\n{extra}\n\n{language}"
    if stage == "stage4":
        extra = """- Never propose new material for the SUMMARY. Put new evidence in CAREER
  HIGHLIGHTS or PROFESSIONAL EXPERIENCE (Where on Resume must name one of
  those). Proposed Additions should reuse the evidence file's own phrasing."""
        return f"{summary}\n{extra}\n\n{language}"
    if stage == "cover":
        return (f"{language}\n- Open the proof with one of the business outcomes this role is judged on "
                f"({', '.join(rm['metrics'])}), only where the resume has a real number for it.")
    raise ValueError(f"unknown stage {stage!r}; use stage3, stage4 or cover")


def _main(argv):
    jd = argv[argv.index("--jd") + 1] if "--jd" in argv and argv.index("--jd") + 1 < len(argv) else None
    title = jd_title_from(jd) if jd else None
    if len(argv) >= 2 and argv[0] == "--prompt":
        rtext = None
        if "--resume" in argv and argv.index("--resume") + 1 < len(argv):
            try:
                rtext = open(argv[argv.index("--resume") + 1], encoding="utf-8", errors="replace").read()
            except OSError:
                pass
        print(prompt_block(argv[1], title, rtext))
        return 0
    if len(argv) >= 2 and argv[0] == "--check":
        warns = check_summary(open(argv[1], encoding="utf-8", errors="replace").read())
        for w in warns:
            print(f"⚠️  {w['message']}")
        if not warns:
            print("✅ Summary follows the rules")
        if title:
            rm = role_metrics(title)
            print(f"   Role: {title} -> {rm['family'] or 'unclassified'}; frame the proof as: {', '.join(rm['metrics'])}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
