"""
ats_parse.py -- the ONE place that reads scores out of Stage 2 / Stage 9
ATS responses (ats_prompt_response.txt, ats_rescore_response.txt).

Why this exists (Stage 10 plan, decision D7): the same regexes used to live
in several places (app.py twice, synthesize_resume.py) and drifted apart.
The concrete failure: one rescore response bolded the whole phrase
("**ATS Score: 88/100**") instead of just the label ("**ATS Score:** 88/100"),
one copy of the regex was fixed and another was not, and the Review Board
showed "Final score: null". Every caller now imports from here, so a format
fix lands everywhere at once.

Deterministic, no dependencies beyond the standard library.
"""
import re

SUBSCORE_FIELDS = {
    "ats_score": "ATS Score",
    "technical_alignment": "Technical Alignment",
    "leadership_alignment": "Leadership Alignment",
    "domain_alignment": "Domain Alignment",
    "interview_probability": "Interview Probability",
}


def _score_re(label: str):
    # Accepts: **Label:** 62/100 | **Label: 62/100** | Label: 62/100 | **Label** 62/100
    return re.compile(rf"\*{{0,2}}{re.escape(label)}:?\*{{0,2}}:?\s*(\d{{1,3}})\s*/\s*100", re.IGNORECASE)


def extract_score(text: str, label: str):
    m = _score_re(label).search(text or "")
    return int(m.group(1)) if m else None


def extract_ats_score(text: str):
    return extract_score(text, "ATS Score")


def extract_subscores(text: str) -> dict:
    return {key: extract_score(text, label) for key, label in SUBSCORE_FIELDS.items()}


def extract_shortlist(text: str):
    """YES / NO / MAYBE, or None. Tolerates '**Shortlist:** MAYBE',
    '**Shortlist: YES**' and 'MAYBE (leaning NO ...)' (first verdict wins)."""
    m = re.search(r"Shortlist:?\**:?\s*\**\s*(YES|NO|MAYBE)\b", text or "", re.IGNORECASE)
    return m.group(1).upper() if m else None


def _section(text: str, heading_word: str):
    """Body of the first markdown heading containing heading_word, up to the
    next heading or '---' rule. Only matches real headings -- an inline
    mention like 'Critical gaps: ...' inside a Reason paragraph is ignored."""
    lines = (text or "").splitlines()
    out, inside = [], False
    for ln in lines:
        if re.match(r"^\s*#{1,4}\s", ln):
            if inside:
                break
            if heading_word.lower() in ln.lower():
                inside = True
            continue
        if inside:
            if re.match(r"^\s*-{3,}\s*$", ln):
                break
            out.append(ln)
    return out if inside else None


def count_critical_gaps(text: str):
    """Number of TOP-LEVEL bullets under the 'Critical Gaps' heading.
    Indented sub-bullets are details of one gap, not separate gaps (a real
    rescore listed 'Missing core engineering depth' with four sub-bullets;
    counting those would quadruple-penalise one gap). None if no section."""
    body = _section(text, "critical gap")
    if body is None:
        return None
    return sum(1 for ln in body if re.match(r"^[-*]\s+\S|^\d+\.\s+\S", ln))


def extract_section_bullets(text: str, heading_word: str, limit: int = 5) -> list:
    """Top-level bullet text under a heading (e.g. 'Strengths'), markup stripped."""
    body = _section(text, heading_word) or []
    bullets = []
    for ln in body:
        m = re.match(r"^[-*]\s+(.*)", ln)
        if m:
            bullets.append(re.sub(r"\*\*|__|\*", "", m.group(1)).strip())
        if len(bullets) >= limit:
            break
    return bullets
