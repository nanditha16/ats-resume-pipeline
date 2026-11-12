#!/usr/bin/env python3
"""
scripts/synthesize_resume.py — Stage 5 of the ATS workflow.

PURPOSE
Stages 2-4 all produce judgments in prose. Nothing in the pipeline turns
those judgments into an actual edited resume — a human was expected to
read three response files and manually retype the accepted changes.
Stage 5 closes that loop: it reads the three existing response files and
writes output/review_resume/{JD}_new_resume.md automatically.

WHY THIS IS DETERMINISTIC, NOT ANOTHER LLM CALL
Stages 2-4 already did every judgment call that requires reasoning: what
the gaps are, whether evidence closes them, how confident that closure
is. Re-asking a model to "now apply your own recommendations" would
re-spend tokens re-deriving a decision that was already made, and Stage 4
is the most expensive stage in the whole pipeline (~$0.44/JD observed,
~78-120K input tokens - see ROADMAP.md session cost example). Stage 5
instead PARSES the structured output Stage 3/4 were already instructed
to produce (### FILE / **Current** / **Paraphrase**, ## Recommendation /
**Confidence**) and applies a fixed decision rule. Cost: $0.00, 0 tokens,
every run.

DECISION RULES (the "final call" logic)
  1. GATE ON STAGE 2 VERDICT.
     Shortlist: NO -> do not synthesize a resume at all. Paraphrasing or
     bolting on new bullets cannot fix a structural mismatch (Stage 2's
     own verdict already says so). Writes a SKIP report only. This is
     also where future runs should save the Stage 3/4 spend that JD1
     burned in this project's history by running those stages anyway.
  2. STAGE 3 PARAPHRASE EDITS -> always auto-applied.
     By construction (ats_recommend_prompt.txt's own constraints) these
     only reword facts already present in the resume - no new claims,
     no new numbers. That is what makes them safe to apply without a
     human in the loop at the synthesis step. Applied via exact-text
     replacement against the full (unchunked) resume, with a normalized
     whitespace/quote fallback for matching across the PDF-derived hard
     line-wraps.
  3. STAGE 4 NEW-MATERIAL RECOMMENDATIONS -> confidence-gated.
     HIGH confidence + a resume section/role Stage 5 can confidently
     locate by name -> inserted as a new bullet under that section.
     HIGH confidence but no confident location match -> appended to a
     clearly labeled "VERIFY BEFORE USE" section at the end, never
     silently guessed into place.
     MEDIUM/LOW confidence or TRUE GAP -> never touches the resume.
     Logged to the report only, exactly as ats_evidence_gap_prompt.txt's
     own instructions say ("candidate must verify before using").
  4. Every decision (applied / skipped / deferred, and why) is written
     to prompts/{JD}_PREP/resp/5_synthesis_report.md so the "why" behind
     the final resume is auditable, not silent.

USAGE
    python3 scripts/synthesize_resume.py JD2
    python3 scripts/synthesize_resume.py JD2 --force   # overwrite existing output

INPUT (all already produced by Stages 2-4 - nothing new to generate):
    prompts/JD_Analysis/{JD}/.chosen_variant
    prompts/{JD}_PREP/resp/ats_prompt_response.txt
    prompts/{JD}_PREP/resp/ats_recommend_prompt_response.txt
    prompts/{JD}_PREP/resp/ats_evidence_gap_response.txt
    output/resume/{chosen_variant}.md

OUTPUT:
    output/review_resume/{JD}_new_resume.md      <- the tailored resume
    prompts/{JD}_PREP/resp/5_synthesis_report.md <- decision log
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import style_rules  # noqa: E402  writing rules shared with the Stage 3/4/8 prompts
import parse_diagnostics  # noqa: E402  explains zero/short parses on the review page
import evidence_index as ev  # noqa: E402  ledger / approved claims / do-not-claim lookups
from datetime import datetime, timezone  # noqa: E402

ROOT = Path(__file__).parent.parent.resolve()
PROMPTS = ROOT / "prompts"
OUTPUT = ROOT / "output"
EVIDENCE_DIR = OUTPUT / "career_wealth_chunk"
# Finalized/merged resumes live in their own subfolder, separate from the
# untouched per-JD base variants in output/resume/ -- keeps "the file you
# review and submit" visually distinct from "the raw variant Stage 0/1
# picked," which used to sit side by side in the same flat output/ dir.
REVIEW_RESUME_DIR = OUTPUT / "review_resume"

KNOWN_SECTIONS = [
    "SUMMARY", "CORE COMPETENCIES", "TECHNICAL STACK", "TOOLS & METHODS",
    "CAREER HIGHLIGHTS", "PROFESSIONAL EXPERIENCE", "AI PLATFORM PROJECT",
    "AI ENABLED PROJECT", "EDUCATION", "CERTIFICATIONS",
]


# ── text normalization (for matching only — never for output) ─────────────
def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return text


def build_norm_map(text: str):
    """Collapse-whitespace normalized copy of text + index map back to original."""
    norm_chars = []
    idx_map = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            norm_chars.append(" ")
            idx_map.append(i)
            while i < n and text[i].isspace():
                i += 1
        else:
            norm_chars.append(c)
            idx_map.append(i)
            i += 1
    return "".join(norm_chars), idx_map


def find_span(resume_text: str, norm_resume: str, idx_map, needle: str):
    """Find `needle` in resume_text, tolerant of line-wrap/whitespace/quote diffs."""
    pos = resume_text.find(needle)
    if pos != -1:
        return pos, pos + len(needle)
    norm_needle, _ = build_norm_map(normalize(needle))
    norm_resume_n = normalize(norm_resume)
    pos = norm_resume_n.find(norm_needle)
    if pos == -1:
        return None
    start = idx_map[pos]
    end = idx_map[pos + len(norm_needle) - 1] + 1
    return start, end


# ── Stage 2 parsing ─────────────────────────────────────────────────────────
def parse_stage2(text: str):
    # Score and verdict come from scripts/ats_parse.py, the single shared
    # parser (Stage 10 plan, D7) -- the local regexes here had drifted from
    # app.py's copies before, which is how a whole-phrase-bolded score
    # ("**ATS Score: 88/100**") ended up shown as null on the Review Board.
    import ats_parse
    verdict = ats_parse.extract_shortlist(text)
    reason_m = re.search(r"\*\*Reason:\*\*\s*(.+?)(?=\n\n|\n---|\Z)", text, re.DOTALL)
    return {
        "score": ats_parse.extract_ats_score(text),
        "verdict": verdict or "UNKNOWN",
        "reason": reason_m.group(1).strip() if reason_m else "(no reason parsed)",
    }


# ── Stage 2's own "Recommended Improvements" + "Missing Keywords" ──────────
# Stage 2 produces a second, independent set of suggestions in prose, plus a
# keyword table. Neither was ever fed into Stage 5 before -- confirmed by
# checking a real run's 5_changes.json, which contained zero entries tracing
# to Stage 2. This section fixes that, but asymmetrically:
#
#   Stage 2's "Skills Section Addition" quotes are complete, standalone
#   lines -- safe to treat as real insertion candidates, same as Stage 4's.
#
#   Stage 2's "Current"/"Improved" pairs (Executive Summary, Experience
#   Improvements) quote the ORIGINAL sentence with a trailing "..." --
#   e.g. "Senior Technical Delivery Lead ... enterprise-scale..." -- a
#   deliberately truncated fragment, not the full sentence Stage 3 quotes
#   verbatim. Doing a find-and-replace on a truncated fragment would splice
#   the new paragraph into the MIDDLE of the old sentence and orphan its
#   tail. These are surfaced as read-only comparison context attached to
#   whichever Stage 3 edit touches the same sentence -- never as their own
#   appliable candidate -- specifically because they can't be verified safe
#   to auto-apply the way Stage 3's exact quotes can.
KEYWORD_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(CRITICAL|HIGH|MEDIUM)\s*\|\s*(✅|❌|Partial[^|]*)\s*\|", re.MULTILINE
)
KEYWORD_WEIGHTS = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}


def parse_stage2_keywords(text: str):
    """Weighted {keyword: weight} for every JD keyword Stage 2 marked NOT
    already present (❌ or Partial) -- these are the terms worth rewarding
    a candidate edit for introducing. Already-✅ keywords score nothing;
    the resume already has them, so using them again isn't a gain."""
    weights = {}
    for kw, priority, present in KEYWORD_ROW_RE.findall(text):
        if present.strip() == "✅":
            continue
        weights[kw.strip().lower()] = KEYWORD_WEIGHTS.get(priority, 1)
    return weights


def score_against_keywords(text: str, keyword_weights: dict) -> int:
    if not text or not keyword_weights:
        return 0
    text_lower = text.lower()
    return sum(weight for kw, weight in keyword_weights.items() if kw in text_lower)


def parse_stage2_suggestions(text: str):
    """Returns (reference_pairs, addition_candidates).
    reference_pairs: [{before, after, reason}] -- truncated-quote Current/
      Improved pairs, attached as read-only context, never auto-applied.
    addition_candidates: recs-shaped dicts (same fields parse_stage4
      produces) built from "Add Section" quoted lines, source="stage2" --
      these ARE real, independently-appliable insertion candidates, since
      each quoted line is complete and standalone."""
    section = extract_section(text, "## RECOMMENDED IMPROVEMENTS", ["\n## PRIORITIZED ACTION PLAN", "\n## SCREENING QUESTIONS"])
    reference_pairs, addition_candidates = [], []
    current_heading = "Stage 2 suggestion"

    for chunk in re.split(r"\n---\n", section):
        heading_m = re.search(r"^#{3,4}\s*(.+)$", chunk, re.MULTILINE)
        if heading_m:
            current_heading = heading_m.group(1).strip()

        reason_m = re.search(r"\*\*Reason:\*\*\s*(.+?)(?=\Z)", chunk, re.DOTALL)
        reason = reason_m.group(1).strip() if reason_m else ""

        current_m = re.search(r"\*\*Current:\*\*\s*\n\*?\"(?P<current>.*?)\"\*?\s*\n", chunk, re.DOTALL)
        improved_m = re.search(r"\*\*Improved:\*\*\s*\n\*?\"(?P<improved>.*?)\"\*?\s*\n", chunk, re.DOTALL)
        if current_m and improved_m:
            reference_pairs.append({
                "before": current_m.group("current").strip(),
                "after": improved_m.group("improved").strip(),
                "reason": reason or current_heading,
            })
            continue

        add_m = re.search(r"\*\*Add Section:\*\*\s*\n(?P<lines>.+?)(?=\*\*Reason:\*\*|\Z)", chunk, re.DOTALL)
        if add_m:
            quote_lines = re.findall(r'\*?"(.*?)"\*?', add_m.group("lines"), re.DOTALL)
            for line in quote_lines:
                addition_candidates.append({
                    "title": current_heading,
                    "gap": current_heading,
                    "evidence": "Stage 2 (ats_prompt_response.txt) — not source-cited like Stage 4",
                    "where": "Technical Stack",
                    "confidence": "N/A (Stage 2 suggestion — not evidence-cited)",
                    "conf_note": "",
                    "addition_text": line.strip(),
                    "source": "stage2",
                })
    return reference_pairs, addition_candidates


# ── Stage 3 parsing ─────────────────────────────────────────────────────────
# NOTE: the model does not always emit the exact heading depth/nesting the
# prompt template asks for — observed variants: "### FILE:" directly above
# Current/Paraphrase/Why (JD2), and "## FILE:" followed by a "### Edit N"
# sub-heading before Current/Paraphrase/Why (JD3). Rather than parse a single
# rigid nesting shape, file headings and Current/Paraphrase/Why triples are
# matched independently, then each triple is attributed to the nearest file
# heading that precedes it in the document. This is the same tolerance
# principle the project already applies in batch_prep.sh's extract_variant()
# (multiple candidate patterns, first-match-wins) — model output format
# drifts run to run even with an unchanged prompt template.
FILE_HEADING_RE = re.compile(r"^#{2,3}\s*FILE:\s*(?P<file>.+?)\s*$", re.MULTILINE)
EDIT_TRIPLE_RE = re.compile(
    r"\*\*Current:\*\*\s*\n?\s*\"?(?P<current>.*?)\"?\s*\n+"
    r"\*\*Paraphrase:\*\*\s*\n?\s*\"?(?P<paraphrase>.*?)\"?\s*\n+"
    r"\*\*Why:\*\*\s*\n?\s*(?P<why>.*?)(?=\n---|\n#{2,3}|\Z)",
    re.DOTALL,
)
# NOTE: \n+ (one or more newlines) between fields, not \s*\n\s*\n (a
# mandatory blank line). A real run put Current/Paraphrase/Why on three
# consecutive lines with NO blank line between them -- the previous
# blank-line-mandatory pattern matched zero edits against a response that
# had 12+ well-formed, correctly-quoted edits sitting right there. \n+
# matches a single newline OR a blank line, so both this run's tighter
# formatting and every earlier run's blank-line-separated formatting work
# through the same pattern.
# NOTE: quotes around Current/Paraphrase text are now OPTIONAL (\"?, not \").
# A real run dropped the "..." quoting convention entirely -- "**Current:**"
# followed by a markdown line-break then several lines of raw unquoted text
# -- and the old quote-mandatory regex matched zero edits for that entire
# response. Since \"? only strips a quote if one is actually there, this
# still works identically against the original quoted format.

STRUCTURAL_GAP_RE = re.compile(
    r"###\s*STRUCTURAL GAPS.*?\n(?P<body>.+?)(?=\n---\n###\s*Summary|\Z)", re.DOTALL
)


# Tolerant Stage 3 field parsing. The Stage 3 PROMPT asks for plain labels
# ("Current: [exact quoted text]"), but this parser used to require bold
# "**Current:**" -- earlier runs only worked because the model happened to
# bold them. A run that followed the prompt literally (plain labels, values in
# backticks) parsed to ZERO edits. Accepts plain, bold, bulleted and
# backtick/quote-wrapped values; EDIT_TRIPLE_RE above stays as the primary
# path so every previously working format is parsed exactly as before.
S3_LABEL_RE = re.compile(r"(?im)^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?(Current|Paraphrase|Why)(?:\*\*)?[ \t]*:(?:\*\*)?[ \t]*")
S3_BOUNDARY_RE = re.compile(r"(?m)^(?:#{1,4}[ \t]|-{3,}[ \t]*$)")


def clean_quote(v: str, fragment_marks: bool = True) -> str:
    """Strip the decoration a model wraps around a quoted resume line so it can
    be located in (or written into) the resume: ``` code fences, '> ' blockquote
    markers, wrapping backticks/quotes, and '...' fragment marks.
    Found by anchoring every real Stage 3 edit against its base resume: JD13's
    quotes all began with '> ' (17 of 17 unanchorable), JD18's and JD20's were
    wrapped in ``` fences (16 and 9 of 9), JD14's ended in '...' -- and the
    apply step skipped them all while still printing OK, so those resumes were
    saved with no Stage 3 edits at all."""
    v = (v or "").strip()
    v = re.sub(r"^```[A-Za-z0-9_-]*[ \t]*\n?", "", v)
    v = re.sub(r"\n?[ \t]*```[ \t]*$", "", v)
    v = re.sub(r"(?m)^[ \t]*>[ \t]?", "", v).strip()
    for _ in range(2):                               # `"text"` or "`text`"
        if len(v) >= 2 and v[0] == v[-1] and v[0] in '`"\'':
            v = v[1:-1].strip()
    if fragment_marks:
        v = re.sub(r"^(?:\u2026|\.\.\.)\s*", "", v)
        v = re.sub(r"\s*(?:\u2026|\.\.\.)$", "", v)
    return v.strip()


def _parse_stage3_tolerant(text: str, file_headings):
    labels = list(S3_LABEL_RE.finditer(text))
    boundaries = [m.start() for m in S3_BOUNDARY_RE.finditer(text)]
    edits, i = [], 0
    while i < len(labels):
        names = [labels[j].group(1).lower() for j in range(i, min(i + 3, len(labels)))]
        if names != ["current", "paraphrase", "why"]:
            i += 1
            continue

        def value(k):
            start = labels[k].end()
            nxt = labels[k + 1].start() if k + 1 < len(labels) else len(text)
            b = next((x for x in boundaries if x >= start), len(text))
            return text[start:min(nxt, b)]

        pos = labels[i].start()
        current_file = "(unknown file)"
        for hpos, hname in file_headings:
            if hpos <= pos:
                current_file = hname
            else:
                break
        edits.append({"current": clean_quote(value(i)), "paraphrase": clean_quote(value(i + 1)),
                      "why": value(i + 2).strip(), "file": current_file})
        i += 3
    return edits


def parse_stage3(text: str):
    file_headings = [(m.start(), m.group("file")) for m in FILE_HEADING_RE.finditer(text)]
    edits = []
    for m in EDIT_TRIPLE_RE.finditer(text):
        pos = m.start()
        current_file = "(unknown file)"
        for hpos, hname in file_headings:
            if hpos <= pos:
                current_file = hname
            else:
                break
        d = m.groupdict()
        d["file"] = current_file
        d["current"] = clean_quote(d["current"])
        d["paraphrase"] = clean_quote(d["paraphrase"])
        edits.append(d)
    if not edits:
        edits = _parse_stage3_tolerant(text, file_headings)
    struct_m = STRUCTURAL_GAP_RE.search(text)
    structural_gaps = struct_m.group("body").strip() if struct_m else ""
    return edits, structural_gaps


# ── Stage 4 parsing ──────────────────────────────────────────────────────────
def extract_section(text: str, start_heading: str, end_headings):
    """Case-insensitive on purpose: a real run produced '# PHASE B — NEW
    MATERIAL RECOMMENDATIONS' (all-caps) against a prompt template that
    only specified '## Phase B'. text.find() is case-sensitive and silently
    returned "no section found" for that entire run -- not an exception,
    not a warning, just zero Stage 4 candidates parsed. Case-insensitive
    matching is the fix; matching on the word 'phase b' regardless of
    heading level/caps/dash-style is more robust than matching the exact
    string the template asked for and hoping the model complies exactly."""
    m = re.search(re.escape(start_heading), text, re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    end = len(text)
    for h in end_headings:
        m2 = re.search(re.escape(h), text[start:], re.IGNORECASE)
        if m2:
            end = min(end, start + m2.start())
    return text[start:end]


# NOTE: same format-drift issue as Stage 3. Two observed shapes for a Phase B
# block's heading/title:
#   "## Recommendation N: <title>" + separate "**Gap Addressed:** <gap>" line   (JD2)
#   "### Gap Addressed: <title>"  (title IS the gap, no separate bold field)    (JD3)
# Rather than anchor on the heading shape, each "---"-delimited chunk inside
# Phase B is scanned independently for the bold fields that actually matter
# for the decision rule (Confidence is load-bearing; a chunk with no
# Confidence field is not a recommendation block and is skipped).
def extract_addition_fallback(chunk: str) -> str:
    """Fallback when no '> quoted' lines are found under 'Proposed Addition
    to Resume:'. A real run used a '- dash bullet' under a bolded sub-label
    ('**Career Highlights** (new bullet):') instead of the blockquote
    convention the prompt asked for -- the '>' -only extraction found
    nothing, producing an empty, useless "(no addition text parsed)" card
    even though the actual proposed text was sitting right there. This
    grabs the raw span after the label up to the next bold field, then
    drops any line that is PURELY a structural sub-heading (bold text
    plus, at most, a parenthetical and a colon -- nothing else), keeping
    everything else including bullet markers, which are stripped."""
    m = re.search(
        # \s* (not "[ \t]*\n?"): swallow ALL whitespace after the label, blank lines
        # included. Inline text ("**Proposed Addition:** text") and the original
        # "label, blank line, bold sub-label, bullets" shape (JD14) must both work;
        # consuming only one newline left the sub-label at the start of the body,
        # which ended the match at once and emptied all 12 JD14 additions.
        r"\*\*Proposed Addition(?: to Resume)?:\*\*\s*(?P<body>.*?)(?=\n\*\*[A-Z][^*\n]*:\*\*|\Z)",
        chunk, re.DOTALL,
    )
    if not m:
        return ""
    lines = []
    for ln in m.group("body").splitlines():
        s = ln.strip()
        if not s:
            continue
        if re.fullmatch(r"\*\*[^*]+\*\*\s*(\([^)]*\))?\s*:?", s):
            continue  # pure sub-heading label, e.g. "**Career Highlights** (new bullet):"
        s = s.lstrip("-").lstrip(">").strip().strip('"')
        if s:
            lines.append(s)
    return " ".join(lines).strip()


# A "Proposed Addition" that is really a note to the candidate. JD1's Stage 4 wrote, at HIGH confidence,
# "(No new material needed; already covered extensively in current resume. Verify Technical Stack ...)" and it became a
# pre-ticked insertion card: a hands-off apply would have pasted that instruction into the resume.
NO_ADDITION_RE = re.compile(r"^\W*(?:no new (?:material|content|addition|text|bullet)s?\b|no addition\b|nothing (?:new )?to add\b|"
                            r"already (?:covered|present|well covered|on (?:the )?resume|in (?:the )?resume)\b|"
                            r"none (?:needed|required)\b|not needed\b|n/?a\b|verify\b)", re.IGNORECASE)

PLACEMENT_NOTE_RE = re.compile(r"^\s*\*[^*\n]{3,300}:\*\s+(?=\S)")
# A plain-text instruction before the bullet, then the bullet in italics: JD6's Stage 4 wrote all six insertions as
# "Add to Professional Experience > Employer A/Employer A (new bullet): *Led ...*", and approving one would have written the
# instruction and the asterisks into the resume.
ADD_INSTR_RE = re.compile(r"^\W*(?:add|insert|append|place|put)\b[^:\n]{0,200}:\s*(?=\S)", re.IGNORECASE)
# An instruction to change an EXISTING line, not text to add (JD7 card 31: 'Add "Microsoft SQL Server / Azure SQL" to the
# existing list: "<the whole list>"'). Appliable only by hand, so it is listed, not turned into a card.
EDIT_INSTR_RE = re.compile(r"^\W*(?:add|append|insert|replace|swap|change|update|reword)\b[^:\n]{0,200}\b(?:to|in|into|of) the "
                           r"(?:existing|current|above|same) (?:list|line|bullet|section|row|sentence)\b", re.IGNORECASE)


def _unwrap_whole(text: str) -> str:
    """Emphasis or quotation marks around a WHOLE proposed bullet are formatting, never resume content: JD2, JD12 and JD13 had 22 additions
    written as *Led ...* or *"Designed ..."*, which would have put literal asterisks and quotes into the resume. A bold LABEL at the front
    ('**Platform Reliability:** Achieved ...') is left alone: a single leading mark, with no other mark in the text or one at the very end,
    is what marks a wrapped bullet (a response cut off mid-bullet loses the closing mark)."""
    t = (text or "").strip()
    if t[:1] in "*_" and not t.startswith(("**", "__")) and (t[-1:] in "*_" or not re.search(r"[*_]", t[1:])):
        t = re.sub(r"^[*_]\s*", "", t)
        t = re.sub(r"\s*[*_]$", "", t)
    q = re.fullmatch(r"[\"\u201c](.+)[\"\u201d]", t, re.S)
    if q and not re.search(r"[\"\u201c\u201d]", q.group(1)):
        t = q.group(1).strip()
    return t


def is_edit_instruction(text: str) -> bool:
    """True when the 'addition' is an instruction to change an existing line. An italic placement line may come first
    ('*Technical Stack section, Data / Messaging line:*  Add "X" to the existing list: ...'), so it is looked past."""
    t = (text or "").strip()
    m = PLACEMENT_NOTE_RE.match(t)
    if m:
        t = t[m.end():].strip()
    return bool(EDIT_INSTR_RE.match(t))


def strip_leading_placement_note(text: str) -> str:
    """Remove an italic placement instruction glued to the front of a proposed
    addition, e.g. '*Add bullet under "Employer A" experience block:* Managed
    program budgets...' or '*Experience section (Employer A / Agency):* Led...'.
    The model puts these on their own line before the bullet; the parser joined
    them, so the review card showed the instruction as part of the resume text
    (18 of 82 real recommendations across JD17/18/20) and approving it would
    have pasted the instruction into the resume. Where to place it is already
    captured in 'Where on Resume'.
    Deliberately narrow: only an italic span that ENDS WITH ':*' and is followed
    by at least 5 more words. An italicised bullet (all of JD13's, which have
    nothing after the closing '*') is real content and is left untouched."""
    text = text or ""
    m = PLACEMENT_NOTE_RE.match(text)
    if m and len((text[m.end():]).split()) >= 5:
        text = text[m.end():].strip()
    text = _unwrap_whole(text)
    m = ADD_INSTR_RE.match(text)
    if m and len(text[m.end():].split()) >= 5:
        text = text[m.end():].strip()
        # The bullet itself is wrapped in italics or bold. A response cut off mid-bullet (JD6) loses the closing mark, so strip
        # a leading mark and, if present, a trailing one.
        text = re.sub(r"^(?:\*{1,2}|_)\s*", "", text)
        text = re.sub(r"\s*(?:\*{1,2}|_)$", "", text)
        quoted = re.fullmatch(r"[\"\u201c](.+)[\"\u201d]", text.strip(), re.S)     # JD23: *"Implemented ..."* leaves the quotes
        if quoted and not re.search(r"[\"\u201c\u201d]", quoted.group(1)):
            text = quoted.group(1).strip()
    return text


def find_phase_b(text: str):
    """Start offset of the Phase B section, or None. Matches any markdown
    heading line containing 'Phase B', not the literal '# Phase B': a real
    run titled it '# Evidence-Based Gap Resolution -- Phase B: New Material
    Recommendations', which the literal match never found, so that JD's six
    recommendations silently never reached the review page (found by the
    parse diagnostics: 6 Confidence blocks in the file, 0 parsed). Prefers a
    heading that is Phase B only; a combined 'Phase A & Phase B' title is
    used only if nothing better exists."""
    heads = [m for m in re.finditer(r"(?im)^#{1,3}[^\n]*\bPhase B\b[^\n]*$", text)]
    pure = [m for m in heads if not re.search(r"\bPhase A\b", m.group(0), re.IGNORECASE)]
    chosen = (pure or heads or [None])[0]
    if chosen is not None:
        return chosen.end()
    m = re.search(r"# Phase B", text, re.IGNORECASE)
    return m.end() if m else None


def _split_by_confidence(chunk: str):
    """Split a block holding several recommendations at each Confidence line.
    JD9's response numbered its recommendations ('**1. Gap Addressed:** ...') and
    put no '---' between them, so five recommendations arrived as ONE block:
    only the first was read and its text came out empty. A block with a single
    Confidence line is returned untouched, so every older format parses as before."""
    ends = [m.end() for m in re.finditer(r"(?m)^\*\*Confidence:\*\*[^\n]*", chunk)]
    if len(ends) < 2:
        return [chunk]
    parts, start = [], 0
    for e in ends:
        parts.append(chunk[start:e])
        start = e
    return parts


def parse_stage4_flags(text: str) -> list:
    """Items under the Stage 4 'Flags' section (evidence-vs-resume conflicts: dates, unsupported figures, do-not-claim
    wording). JD1's page showed no conflicts list although its response had '# Flags: Conflicts Between Evidence and
    Current Resume', for two reasons found by testing: the OLD parser took the FIRST heading containing 'flag' (a Phase A
    row can mention it) instead of the section, and it only read '-' bullets. Now: the LAST heading naming Flags (or, failing
    that, conflicts or discrepancies), and bullets, numbered items, table rows and bold-lead paragraphs are all read.
    Markdown is stripped and the list is capped so a long section cannot swamp the page."""
    heads = list(re.finditer(r"(?im)^#{1,3}[^\n]*$", text))
    def is_section(m, words, allow_end=False):
        # The heading must BE the section, not merely mention the word. Phase A/B rows are headed "## Gap N: ..." or
        # "## Gap Addressed: ..." and can mention anything ("... and resolve conflicts"), so they are never a section.
        h = re.sub(r"^#{1,3}\s*(?:\d+[.)]\s*)?", "", m.group(0)).strip()
        if re.match(r"(?:gap\b|recommendation\b)", h, re.I):
            return False
        return bool(re.match(rf"(?:{words})\b", h, re.I) or re.search(rf"\b(?:{words})s?\s*[:\u2014\-]", h, re.I)
                    or (allow_end and re.search(rf"\b(?:{words})s?\s*$", h, re.I)))
    cand = [m for m in heads if is_section(m, "flags?", allow_end=True)] or \
           [m for m in heads if re.match(r"(?:evidence\s+)?(?:conflicts?|discrepanc\w*)\b",
                                         re.sub(r"^#{1,3}\s*(?:\d+[.)]\s*)?", "", m.group(0)).strip(), re.I)
            and is_section(m, "conflicts?|discrepanc\\w*")]
    if not cand:
        return []
    start = cand[-1].end()
    body = text[start:]
    nxt = re.search(r"(?m)^#{1,3}\s", body)
    lines = (body[:nxt.start()] if nxt else body).splitlines()
    items, cur = [], None

    def flush():
        nonlocal cur
        if cur:
            items.append(cur)
        cur = None

    for i, ln in enumerate(lines):
        t = ln.strip()
        if not t or t.endswith(":") and not t.startswith(("-", "*", "|")):
            flush()
            continue
        if t.startswith("|"):
            flush()
            cells = [c.strip() for c in t.strip("|").split("|")]
            sep = all(re.fullmatch(r"[:\-\s]*", c) for c in cells)
            nxt_sep = i + 1 < len(lines) and re.fullmatch(r"[|:\-\s]+", lines[i + 1].strip() or "x")
            if not sep and not nxt_sep:                      # skip the separator row and the header row above it
                items.append(" | ".join(c for c in cells if c))
            continue
        b = re.match(r"^(?:[-*\u2022]|\d+[.)])\s+(.*)", t)
        # Lines of ONE conflict ("CONFLICT 1: ...", then "Current Resume: ...", "Evidence: ...", "Action Required: ...") belong
        # together. JD1's page listed each conflict as four rows (12 rows, exactly the cap), so a fourth conflict would have
        # been cut off. A new item starts at a bullet, a numbered item, "CONFLICT n"/"Flag n", or a bold lead that is not a
        # sub-field label.
        subfield = re.match(r"^(?:\*\*)?(?:current resume|current|evidence|action required|action|resume|source|fix|recommendation|verify)\b", t, re.I)
        new_item = re.match(r"^(?:\*\*|(?:conflict|flag|issue)\s*\d+)", t, re.I) and not subfield
        if b:
            flush()
            cur = b.group(1)
        elif cur and (ln[:1] in (" ", "\t") or subfield or not new_item):
            cur += " " + t                                   # continuation of the same item
        else:
            flush()
            cur = t
    flush()
    # 1500, not 600: a conflict's "Action Required" is the end of it and was being cut mid-sentence.
    clean = lambda x: re.sub(r"\*\*|__|`", "", x).strip()[:1500]
    return [c for c in (clean(i) for i in items) if len(c) > 15 and not re.match(r"^\W*end of\b", c, re.I)][:12]


def _norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def annotate_flags(flags: list, resume_text: str) -> list:
    """Check what Stage 4's Flags actually assert, because they are model-written. One real list had a conflict that was
    right and verifiable (all its quotes were verbatim in the evidence file) and one that was wrong twice over: it said the
    Summary was 99 words (it was 83) and that a file 'confirms' two figures (the file lists them as a do-not-claim
    and a design target). Returns [{text, checks}] where each check is a one-line verdict."""
    idx = ev.load_index()
    corpus = ""
    if ev.EVIDENCE.exists():
        for p in ev.EVIDENCE.glob("*.md"):
            t = p.read_text(encoding="utf-8", errors="replace")
            if not re.search(r"linkedin\.com/in/", t[:1500], re.I):
                corpus += " " + _norm_text(t)
    res_norm = _norm_text(resume_text)
    titles = ev.role_title_report(resume_text)
    date_conflicts = ev.role_date_conflicts(resume_text)
    out = []
    for f in flags:
        checks = []
        quoted = [seg for k, seg in enumerate(re.split(r'["\u201c\u201d]', f)) if k % 2 == 1 and len(seg.strip()) >= 25]
        good, bad = 0, []
        for q in quoted:
            long_quote = len(_norm_text(q)) >= 40        # a short quoted phrase is usually a loose paraphrase, not a claim
            for frag in re.split(r"\.\.\.|\u2026", q):
                n = _norm_text(frag)
                if len(n) < 15:
                    continue
                if n in corpus or n in res_norm:
                    good += 1
                elif long_quote:
                    bad.append(frag.strip()[:70])
        if bad:
            checks.append(f'quote not found in your evidence or resume: "{bad[0]}"' + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else ""))
        if good:
            checks.append(f"{good} quoted line(s) verified: word for word in your evidence or resume")
        if idx is not None and re.search(r"\b(?:confirms?|confirmed|supports?|supported|verif(?:ies|ied)|backs?|backed)\b", f, re.I):
            un = [f"{m['raw']} ({ev.STATUS_TEXT[m['status']]})" for m in ev.audit_text(f, idx)["metrics"] if not m["verified"]]
            if un:
                checks.append("says the evidence confirms figures that your ledger does not back: " + ", ".join(un[:4]))
        # A claim that something is ABSENT from the resume, checked against the resume. A real list said "the
        # error-rate claim does not currently appear (good)" while it was in a bullet (and the same page's audit listed it).
        if re.search(r"\b(?:does not (?:currently )?appear|do not appear|not (?:currently )?(?:present|appear)|is not (?:in|on) the resume|"
                     r"not (?:explicitly )?(?:stated|listed|mentioned|shown)|absent)\b", f, re.I):
            have = {k for k, _ in ev.tokens(ev.strip_years(resume_text), metric_only=True)}
            present = []
            for k, raw in ev.tokens(ev.strip_years(f), metric_only=True):
                if k in have and raw not in present:
                    present.append(raw)
            if present:
                checks.append("says this does not appear in your resume, but it does: " + ", ".join(present[:4]) + " is in your resume")
        # A claim that a date range MATCHES the evidence when the date check says the evidence does not support it (JD4's Stage 4
        # said "Project X role (Jan 2025-Present) matching Project_X.md"; the file has records only from Oct 2025).
        if re.search(r"\b(?:match(?:es|ing)?|consistent|aligned?|confirm(?:s|ed)?|verified)\b", f, re.I):
            claimed = set(ev.date_ranges(f))
            for dc in date_conflicts:
                if dc["pair"] in claimed:
                    checks.append(f"says {dc['resume']} matches your evidence, but {dc['file']} says {dc['evidence']}")
                    break
        # A "conflict" over dates that are in fact identical to the evidence (JD1: Employer B, Jan 2017 to Dec 2018 on both sides).
        ev_pairs = {(e["start"], e["end"]) for e in ev.evidence_role_dates() if e["kind"] != "records only from"}
        res_pairs = set(ev.date_ranges(resume_text))
        for a_, b_ in ev.date_ranges(f):
            if (a_, b_) in ev_pairs and (a_, b_) in res_pairs:
                checks.append(f"your resume already says {ev._fmt(a_)} to {ev._fmt(b_)}, which matches your evidence: not a conflict")
                break
        # A job title is a per-variant CHOICE from the menu in the evidence's title row, not a single right answer: show the menu
        # next to a title flag instead of leaving it to read as a verdict.
        for t in titles:
            if t["resume_title"].lower() in f.lower():
                how = {"listed": "is one of the titles your evidence lists",
                       "listed, official differs": f"is the corporate title in your evidence, which also states the official HR title is {t['official'][0] if t['official'] else 'different'}",
                       "not listed": "is not one of the titles your evidence lists"}[t["status"]]
                menu = "; ".join(t["options"][:3] + (["corporate: " + " / ".join(t["corporate"])] if t["corporate"] else []))
                checks.append(f"your resume's {t['role'][:28]} title \"{t['resume_title'][:50]}\" {how}. Titles are a per-variant choice; options: {menu}")
                break
        m = re.search(r"Summary is (\d+) words?", f, re.I)
        if m:
            actual = len(style_rules.summary_text(resume_text).split())
            if actual and abs(actual - int(m.group(1))) > 2:
                checks.append(f"says the Summary is {m.group(1)} words; it is {actual}")
        out.append({"text": f, "checks": checks})
    return out


def parse_stage4(text: str):
    start = find_phase_b(text)
    phase_b = ""
    if start is not None:
        end = len(text)
        for h in ["\n# True Gaps", "\n# Evidence Files Not Used", "\n# Summary of Findings"]:
            m2 = re.search(re.escape(h), text[start:], re.IGNORECASE)
            if m2:
                end = min(end, start + m2.start())
        phase_b = text[start:end]
    recs = []
    blocks = [part for raw in re.split(r"\n---\n", phase_b) for part in _split_by_confidence(raw)]
    for chunk in blocks:
        chunk = chunk.strip()
        if not chunk:
            continue
        conf_m = re.search(r"\*\*Confidence:\*\*\s*(HIGH|MEDIUM|LOW)\s*(.*)", chunk, re.DOTALL)
        if not conf_m:
            continue  # not a recommendation block (e.g. leading prose before first ---)

        rec_m = re.search(r"##\s*Recommendation\s*\d+:\s*(.+)", chunk)
        gap_heading_m = re.search(r"#{2,3}\s*Gap Addressed:\s*(.+)", chunk)
        gap_field_m = re.search(r"\*\*(?:\d+\.\s*)?Gap Addressed:\*\*\s*(.+)", chunk)

        title = rec_m.group(1).strip() if rec_m else None
        gap = gap_field_m.group(1).strip() if gap_field_m else None
        if gap_heading_m:
            title = title or gap_heading_m.group(1).strip()
            gap = gap or gap_heading_m.group(1).strip()
        title = title or "(untitled recommendation)"
        gap = gap or title

        evidence_m = re.search(r"\*\*Evidence Source:\*\*\s*(.+)", chunk)
        where_m = re.search(r"\*\*Where on Resume:\*\*\s*(.+)", chunk)
        quote_lines = [
            ln.strip().lstrip(">").strip().strip('"')
            for ln in chunk.splitlines() if ln.strip().startswith(">")
        ]
        addition_text = " ".join(quote_lines).strip()
        if not addition_text:
            # A real run used "**New Bullet:**" followed by the proposed
            # sentence in plain double quotes on the next line, rather
            # than the blockquote convention the prompt asked for. Without
            # this, the generic fallback below stops at this exact field
            # boundary and never reaches it -- worse, it was wrongly
            # harvesting the "**Location:** CAREER HIGHLIGHTS or ..." line
            # that sits just before it, since that line has real content
            # after the label and so isn't a "pure sub-heading" the
            # fallback's filter would exclude. That produced a real,
            # visible bug: cards showing "(untitled recommendation)" with
            # a location suggestion where the actual proposed text should
            # be -- confirmed against a real response file, not assumed.
            new_bullet_m = re.search(r'\*\*New Bullet:\*\*\s*\n"(?P<body>.*?)"\s*\n', chunk, re.DOTALL)
            if new_bullet_m:
                addition_text = new_bullet_m.group("body").strip()
        if not addition_text:
            addition_text = extract_addition_fallback(chunk)
        edit_instruction = is_edit_instruction(addition_text)
        addition_text = strip_leading_placement_note(addition_text)

        recs.append({
            "title": title,
            "gap": gap,
            "evidence": evidence_m.group(1).strip() if evidence_m else "(not parsed)",
            "where": where_m.group(1).strip() if where_m else "",
            "confidence": conf_m.group(1),
            "conf_note": conf_m.group(2).strip(),
            "addition_text": addition_text,
            "source": "stage4",
            "no_addition": bool(NO_ADDITION_RE.match(addition_text.strip())) or edit_instruction,
            "edit_instruction": edit_instruction,
        })
    return recs


# ── Apply Stage 3 edits ──────────────────────────────────────────────────────
def apply_edits(resume_text: str, edits):
    applied, skipped = [], []
    for e in edits:
        norm_resume, idx_map = build_norm_map(resume_text)
        span = find_span(resume_text, norm_resume, idx_map, e["current"])
        if span is None:
            skipped.append({**e, "reason": "current text not found in base resume"})
            continue
        start, end = span
        resume_text = resume_text[:start] + e["paraphrase"] + resume_text[end:]
        applied.append(e)
    return resume_text, applied, skipped


# ── Insert Stage 4 HIGH-confidence new content ──────────────────────────────
def is_role_header(line: str) -> bool:
    """A resume experience-block header line, e.g. 'Technical Lead - Acme Corp
    (via Agency) Toronto, ON | Jan 2019 - Jun 2022'. Detected by shape
    (has a '|' date field with a 19xx/20xx year or 'Present') rather than by
    a rigid title/company regex, because company names legitimately contain
    punctuation (parentheses, commas) that breaks a naively strict pattern --
    an earlier version missed one employer's line for exactly this reason, which
    let a new bullet spill past its block into the next one."""
    return "|" in line and bool(re.search(r"\b(19|20)\d{2}\b|Present", line))


def extract_role_anchors(resume_text: str):
    """Dynamically pull (line_idx, anchor_name) pairs from the resume itself
    -- company names from experience role lines, plus exact section headers --
    rather than guessing from JD-side vocabulary. Matching against names the
    resume itself contains is far less prone to incidental-word false
    positives than free-text keyword scanning (see locate_anchor docstring)."""
    lines = resume_text.split("\n")
    anchors = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in KNOWN_SECTIONS:
            anchors.append((i, stripped))
            continue
        if is_role_header(stripped):
            before_pipe = stripped.split("|")[0]
            # Company name is conventionally the segment after the LAST
            # " - " before the date field (title may itself contain " / ").
            rest = before_pipe.split(" - ")[-1].strip()
            anchors.append((i, rest))
    return anchors, lines


def strip_markup(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).strip()


def is_near_duplicate(resume_text: str, addition_text: str) -> bool:
    """Stage 4 sometimes proposes REPOSITIONING existing text (e.g. 'move
    this sentence to the top of the Summary') rather than genuinely NEW
    content, even under 'Proposed Addition to Resume'. Inserting those as a
    fresh bullet duplicates content already on the resume. Detected here by
    checking whether a long normalized substring of the proposed text is
    already present verbatim in the resume -- if so this is a reposition/
    emphasis suggestion, not new material, and should go to human review
    instead of being auto-inserted as a duplicate bullet."""
    norm_add, _ = build_norm_map(normalize(strip_markup(addition_text)))
    norm_resume, _ = build_norm_map(normalize(resume_text))
    window = 45
    if len(norm_add) < window:
        return norm_add.lower() in norm_resume.lower()
    for start in range(0, len(norm_add) - window, window):
        if norm_add[start:start + window].lower() in norm_resume.lower():
            return True
    return False


def locate_anchor(resume_text: str, anchors, lines, where: str):
    """Match `where` against anchor names the resume itself provides (company
    names from real role lines, or exact section headers) -- NOT against
    free-text keywords pulled from the recommendation's own wording. An
    earlier version matched incidental words (e.g. 'platform') against the
    wrong role line ('...AI-enabled digital health platform') because it
    scanned addition_text for any capitalized word. Anchoring only on names
    that exist verbatim in both `where` and the resume itself removes that
    false-positive class; ambiguous cases correctly fall through to None
    (-> human review) rather than guessing."""
    where_lower = where.lower()
    best = None  # (match_len, line_idx)
    for i, name in anchors:
        if name in KNOWN_SECTIONS:
            if name.lower() in where_lower:
                candidate = (len(name), i)
            else:
                continue
        else:
            # Company/role anchors: try the longest leading word-prefix of
            # the anchor (before any parenthetical) that appears in `where`,
            # e.g. anchor "Acme Corp (via Agency) Toronto, ON" tries
            # "Acme Corp (via Agency) Toronto, ON", "Acme Corp (via Agency)
            # Toronto,", ... down to "Acme Corp", down to "Ac". Only
            # candidates of 4+ chars count, so a single short token like
            # "Ac" can't anchor on its own, but "Acme Corp" or "Globex" can --
            # these are proper nouns pulled from the resume's own role
            # lines, not generic vocabulary, so the false-positive risk
            # seen with free-text keyword scanning doesn't apply here.
            core = name.split("(")[0].strip()
            words = core.split()
            match_len = 0
            for n_words in range(len(words), 0, -1):
                prefix = " ".join(words[:n_words])
                if len(prefix) >= 4 and prefix.lower() in where_lower:
                    match_len = len(prefix)
                    break
            if match_len == 0:
                continue
            candidate = (match_len, i)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return None
    anchor_line_idx = best[1]
    j = anchor_line_idx + 1
    while j < len(lines):
        s = lines[j].strip()
        if s in KNOWN_SECTIONS or is_role_header(s):
            break
        j += 1
    return sum(len(l) + 1 for l in lines[:j])


def insert_high_confidence(resume_text: str, recs, force: bool = False):
    """force=True bypasses the confidence gate (used by the Stage 6 web
    review flow, where a human already looked at the specific text and
    approved it) but keeps the near-duplicate and anchor-location checks --
    those aren't policy gates, they're correctness checks that apply
    regardless of who approved the change."""
    inserted, deferred = [], []
    for r in recs:
        if not force and r["confidence"] != "HIGH":
            deferred.append({**r, "reason": f"confidence={r['confidence']} — needs human review, not auto-applied"})
            continue
        if not r["addition_text"]:
            deferred.append({**r, "reason": "no quoted addition text parsed"})
            continue
        if is_near_duplicate(resume_text, r["addition_text"]):
            deferred.append({**r, "reason": "proposed text substantially duplicates existing resume content — "
                                             "likely a reposition/emphasis suggestion, not new material"})
            continue
        anchors, lines = extract_role_anchors(resume_text)
        anchor = locate_anchor(resume_text, anchors, lines, r["where"])
        clean_text = strip_markup(r["addition_text"])
        bullet = f"• {clean_text}\n"
        if anchor is None:
            deferred.append({**r, "reason": "no confident section/role match in 'Where on Resume' — "
                                             "see VERIFY BEFORE USE section"})
            continue
        resume_text = resume_text[:anchor] + bullet + resume_text[anchor:]
        inserted.append(r)
    return resume_text, inserted, deferred


def append_review_section(resume_text: str, deferred_for_review):
    """Deferred HIGH-confidence items with no anchor still deserve visibility —
    append them under a clearly labeled section rather than dropping them."""
    no_anchor = [
        d for d in deferred_for_review
        if "no confident section/role match" in d.get("reason", "")
    ]
    if not no_anchor:
        return resume_text
    block = ["\n\nEVIDENCE-SOURCED ADDITIONS — VERIFY BEFORE USE\n"
             "(HIGH-confidence Stage 4 findings Stage 5 could not confidently place — "
             "move each into the right section above after verifying, then delete this block.)"]
    for d in no_anchor:
        block.append(f"• [{d['gap']}] {d['addition_text']} (source: {d['evidence']})")
    return resume_text + "\n".join(block) + "\n"


# ── Manifest layer — shared between CLI (Stage 5 default policy) and the ──
# Stage 6 web review UI (explicit per-change approve/reject). Building this
# as data (id + type + before/after) rather than baking the accept/reject
# decision into apply_edits/insert_high_confidence directly means both
# call sites reuse the exact same parsing and placement logic — the web UI
# can't drift from what the CLI does, because it IS the same code with a
# different `accepted_ids` filter applied before calling it.
def word_count(text: str) -> int:
    return len(text.split()) if text else 0


def find_alt_suggestion(before_text: str, reference_pairs):
    """Does any Stage 2 reference pair overlap this Stage 3 edit's source
    sentence? Stage 2's quotes are truncated (trailing '...'), so this
    checks whether Stage 2's (stripped) fragment is a normalized substring
    of Stage 3's full quote -- the direction that actually occurs in
    practice, since Stage 2 quotes less text than Stage 3, not more."""
    norm_before = normalize(before_text).lower()
    for ref in reference_pairs:
        frag = ref["before"].rstrip(".").rstrip(".").strip()
        frag = re.sub(r"\.{2,}\s*$", "", frag).strip()  # drop trailing "..."
        norm_frag = normalize(frag).lower()
        if len(norm_frag) >= 15 and norm_frag in norm_before:
            return ref
    return None


# ── Verification: metric grounding, identity-drift, topical overlap ────────
# All three checks below are pure text matching against files already on
# disk (Stage 4's own cited evidence files, the resume itself) -- zero LLM
# calls, zero added cost. They exist because an independent manual review
# of a real Stage 3/4 batch found two fabricated metrics (a "40%" that
# never appears in the evidence corpus, a "500K+" that appears nowhere at
# all) and a systemic pattern (many small, individually-defensible edits
# that together rewrite the candidate's job title) -- neither of which the
# per-card review UI or the existing near-duplicate check could surface on
# their own. Confirmed against the real evidence corpus and the real
# fabricated numbers before being wired in here, not assumed to work.

METRIC_RE = re.compile(
    r"\$\s?\d[\d,]*\.?\d*\s*[KMB]?\+?"   # dollar amounts: $4.5M, $150,000, $1B+
    r"|\d[\d,]*\.?\d*\s*%"                # percentages: 60%, 99.99%
    r"|\d[\d,]*\.?\d*\s*[KMB]\+?\b"       # bare K/M/B counts: 500K+, 2M
    r"|\d[\d,]*\.?\d*\+"                  # plain plus-counts: 15+, 8+
)


def extract_metrics(text: str) -> list:
    """Numbers shaped like the kind of padded metric Stage 4 tends to
    fabricate: percentages, dollar figures, K/M/B or +-suffixed counts.
    Deliberately excludes plain unsuffixed numbers (years, team sizes) --
    lower fabrication risk, and including them would flood every insertion
    with false positives on numbers nobody's actually worried about."""
    return [m.strip() for m in METRIC_RE.findall(text)]


def extract_metrics_with_context(text: str):
    """Each metric paired with a few significant words from its own
    sentence/clause. Needed because a bare digit-sequence match against a
    whole evidence file is too loose: testing this against the real
    corpus found Career_Wealth.md contains the literal string "40%" eight
    separate times, for eight unrelated claims (market velocity, downtime,
    reconciliation effort -- never onboarding), which would have made a
    plain substring check call a fabricated "40% onboarding" claim
    "grounded" just because SOME "40%" exists somewhere in a 134KB file.
    Pairing each metric with nearby context words and requiring both to
    co-occur closes that hole without needing anything semantic."""
    results = []
    for clause in re.split(r"[.;]\s+", text):
        metrics = extract_metrics(clause)
        if not metrics:
            continue
        words = set(re.findall(r"[a-z]{5,}", clause.lower()))
        for m in metrics:
            results.append((m, words))
    return results


def _metric_grounded_in(metric: str, context_words: set, texts: list, window: int = 150, min_shared: int = 2) -> bool:
    """True if `metric` appears in any of `texts` with at least
    `min_shared` context words within `window` characters either side.
    Tightened to 2 shared words / 150 chars (down from an initial 1
    word / 300 chars) after testing against the real fabricated "40%"
    claim: Career_Wealth.md's dense, multi-thousand-character project
    blocks are information-rich enough that a single common word
    ("through," "service," "tooling") will coincidentally sit within 300
    characters of almost any number purely by chance. Requiring two
    co-occurring words is a real precision/recall tradeoff, not a free
    win -- biased deliberately toward flagging a genuinely-grounded claim
    for a human's second look (mild friction) over calling a fabricated
    one "verified" (the actual danger this check exists to prevent)."""
    metric_lower = metric.lower()
    for text in texts:
        lower = text.lower()
        start = 0
        while True:
            idx = lower.find(metric_lower, start)
            if idx == -1:
                break
            span = lower[max(0, idx - window): idx + window]
            hits = sum(1 for w in context_words if w in span)
            if hits >= min_shared:
                return True
            start = idx + 1
    return False


def evidence_check(before: str, after: str, hint: str = ""):
    """What a change ADDS, judged against the evidence index (Number Ledger, Approved Claim
    Copy, Do-Not-Claim tables). None when no index has been built (callers then fall back to
    the legacy text search so nothing silently passes)."""
    idx = ev.load_index()
    if idx is None:
        return None
    return ev.check_introduced(before, after, idx, hint=hint)


def _group_dnc(hits: list) -> list:
    """One entry per rule with how many lines tripped it and one example line, so the same rule is not listed twice."""
    g = {}
    for h in hits:
        e = g.setdefault(h["rule"], {"file": h["file"], "claim": h["claim"], "say_instead": h["say_instead"],
                                     "count": 0, "example": h["text"]})
        e["count"] += 1
    return sorted(g.values(), key=lambda e: -e["count"])


def cited_evidence_files(field: str) -> list:
    """File names a Stage 4 'Evidence Source' field cites, resolved loosely against the real files."""
    return [cited for cited, _actual in ev.resolve_cited_files(field)]


def stage4_staleness(s4_path, recs) -> dict:
    """Was the Stage 4 response written against evidence that has since changed? Two independent signals:
    the response file is older than the evidence's last content change, and/or at least half of the files it
    cites no longer exist. Found on JD9: its cards cited Employer_A_part_5.md and similar chunk names from
    the old corpus, while the audit banner on the same page was already computed from the new evidence."""
    idx = ev.load_index()
    if idx is None or not s4_path or not Path(s4_path).exists():
        return {"indexed": idx is not None, "stale": False}
    changed = datetime.fromisoformat(idx.get("changed_at") or idx["built_at"])
    resp_at = datetime.fromtimestamp(Path(s4_path).stat().st_mtime, tz=timezone.utc)
    pairs = [p for r in recs for p in ev.resolve_cited_files(r.get("evidence", ""))]
    cited = sorted({c for c, _ in pairs})
    missing = sorted({c for c, actual in pairs if actual is None})
    older = resp_at < changed
    return {"indexed": True, "stale": bool(older or (cited and len(missing) * 2 >= len(cited))),
            "response_older_than_evidence": older, "response_at": resp_at.isoformat(timespec="minutes"),
            "evidence_changed_at": changed.isoformat(timespec="minutes"),
            "cited": len(cited), "missing_count": len(missing), "missing": missing[:10]}


def summary_proof_report(resume_text: str, jd_name: str) -> dict:
    """Verified numbers the Summary could use as its ONE proof, for the metrics this role is judged on."""
    idx = ev.load_index()
    if idx is None:
        return {"indexed": False}
    title = style_rules.jd_title_from(jd_name) if jd_name else None
    rm = style_rules.role_metrics(title)
    cands = ev.proof_candidates(rm["metrics"], idx, style_rules.sections(resume_text).get("CAREER HIGHLIGHTS", ""))
    return {"indexed": True, "role": rm.get("label"), "metrics": rm["metrics"], "candidates": cands,
            "none_for": [m for m in rm["metrics"] if not any(c["for"] == m for c in cands)]}


def evidence_audit_report(resume_text: str) -> dict:
    """The whole resume against the evidence: unbacked numbers and do-not-claim hits.
    Document-level, shown once on the review page (a base variant's own claims are not the
    fault of any one card)."""
    idx = ev.load_index()
    if idx is None:
        return {"indexed": False}
    a = ev.audit_text(resume_text, idx)
    return {"indexed": True, "built_at": idx["built_at"],
            "unbacked": [m for m in a["metrics"] if not m["verified"]],
            "verified": [m["raw"] for m in a["metrics"] if m["verified"]],
            "dnc": a["dnc"][:12], "dnc_total": len(a["dnc"]), "dnc_groups": _group_dnc(a["dnc"])}


def check_metric_grounding(addition_text: str, evidence_field: str):
    """For every metric-shaped number in a Stage 4 insertion, confirm it
    appears NEAR at least one context word from its own sentence, in the
    evidence file(s) Stage 4 itself cited (checked first), falling back to
    the whole evidence corpus (a weaker signal -- Stage 4 may have cited
    the wrong file for a real number) only if the cited file doesn't have
    it. Returns (grounded: bool, ungrounded: list) -- the caller downgrades
    default_accepted when ungrounded is non-empty, regardless of Stage 4's
    own confidence label, since that label means "the model said so," not
    "verified against source.\""""
    metrics_with_context = extract_metrics_with_context(addition_text)
    if not metrics_with_context:
        return True, []

    # Backticked (`File.md`) or bare (File.md): JD9's Stage 4 cited bare names, so
    # the backtick-only pattern found no cited file and every check fell through
    # to the weaker whole-corpus search.
    cited_files = re.findall(r"([A-Za-z0-9][\w.\- ]*?\.md)", evidence_field.replace("`", " "))
    cited_files = [f.split()[-1] for f in cited_files if f.strip()]
    cited_texts = []
    for fname in cited_files:
        p = EVIDENCE_DIR / fname
        if p.exists():
            cited_texts.append(p.read_text(errors="replace"))

    corpus_texts = None  # lazily loaded only if a cited-file check misses
    ungrounded = []
    for metric, context_words in metrics_with_context:
        if _metric_grounded_in(metric, context_words, cited_texts):
            continue
        if corpus_texts is None:
            corpus_texts = []
            if EVIDENCE_DIR.exists():
                corpus_texts = [f.read_text(errors="replace") for f in EVIDENCE_DIR.glob("*.md")]
        if _metric_grounded_in(metric, context_words, corpus_texts):
            continue
        ungrounded.append(metric)
    return (len(ungrounded) == 0), ungrounded


IDENTITY_DRIFT_PAIRS = [
    {
        "label": "Technical Program Manager → Product Manager",
        # Broadened after a real independent review caught a genuine gap:
        # "End-to-End Product Delivery Ownership... platform product
        # programs" and "large-scale platform product programs" both
        # reframe "technical program(s)" as "product program(s)"/"product
        # delivery" -- the same identity-shift risk as swapping the title
        # outright, just phrased through the word "program" itself rather
        # than "manager". The original 4-phrase list missed both because
        # neither contains "product manager", "product management",
        # "product strategy", or "product roadmap" literally.
        "toward": ["product manager", "product management", "product strategy",
                   "product roadmap", "product program", "product delivery"],
    },
]
IDENTITY_DRIFT_THRESHOLD = 3


def scan_identity_drift(stage3_changes: list) -> list:
    """Batch-level check across ALL Stage 3 edits in one run: a title/
    identity-sensitive phrase pattern that shows up as a SERIES of small,
    individually-defensible keyword tweaks -- invisible to a one-card-at-
    a-time review, only visible once you count across the whole batch.
    No new parsing: reuses each edit's already-computed before/after text.
    Deliberately a short, curated list, not a general "detect drift"
    engine -- a string-presence count generalizes only as far as the
    pairs actually named here."""
    warnings = []
    for pair in IDENTITY_DRIFT_PAIRS:
        hits = []
        for c in stage3_changes:
            before_lower = c["before"].lower()
            after_lower = c["after"].lower()
            # Checked PER PHRASE, not collapsed into one any()/not-any() --
            # a real edit (the Core Competencies swap) had "Product
            # Delivery" already present in `before` while "Product
            # Management" was the genuinely new phrase in the same line.
            # The original any()/not-any() logic let the pre-existing
            # phrase mask the new one and silently dropped this edit from
            # the flagged list -- found by re-testing after broadening the
            # phrase set, not assumed to still work.
            newly_introduced = [p for p in pair["toward"] if p in after_lower and p not in before_lower]
            if newly_introduced:
                hits.append(c["id"])
        if len(hits) >= IDENTITY_DRIFT_THRESHOLD:
            lead_phrase = pair["toward"][0]
            warnings.append({
                "label": pair["label"],
                "count": len(hits),
                "change_ids": hits,
                "message": (
                    f"{len(hits)} edits in this batch add \"{lead_phrase}\"-style language. "
                    f"Review whether this reflects real experience before accepting all of "
                    f"them -- individually each looks like a reasonable keyword match; "
                    f"together they rewrite what job you're claiming to have held."
                ),
            })
    return warnings


TOPICAL_OVERLAP_WARN_THRESHOLD = 0.20


def topical_overlap_score(resume_text: str, addition_text: str):
    """Word-set (Jaccard) similarity between a proposed insertion and the
    single highest-overlapping existing bullet already in the resume --
    NOT the resume as one blob, which dilutes the signal past
    meaningfulness (a small addition's words are a tiny fraction of a
    whole resume's vocabulary). Same technique trim_review.py already uses
    for bullet scoring, reused rather than reinvented. Deliberately a SOFT
    signal surfaced as a warning, never an auto-defer: verified against a
    real near-duplicate pair (two differently-worded OKR-tracking bullets)
    that scored 0.212 -- well under the 0.35 the existing hard duplicate
    check effectively requires, so this catches a real gap, but shouldn't
    be trusted with the hard check's certainty. Returns (score, matched
    bullet text) so the review card can show what it's flagged against."""
    addition_text = re.sub(r"\(cid:\d+\)", " ", addition_text or "")      # PDF bullet artifact: "(cid:127)" is not the number 127
    resume_text = re.sub(r"\(cid:\d+\)", " ", resume_text or "")
    add_words = set(re.findall(r"[a-z]{4,}", strip_markup(addition_text).lower()))
    if not add_words:
        return 0.0, ""
    lines = [l.strip() for l in resume_text.split("\n")]
    # Compare against each line AND each line merged with the next one --
    # a pragmatic fix for hard-wrapped source text (confirmed on the real
    # Meta variant: a bullet split across two physical lines scored lower
    # than it should have, because half its words were sitting on the
    # line after the one being compared). Not a general-purpose logical-
    # line merger (this variant has no bullet markers at all, so "where
    # does one claim end and the next begin" isn't cleanly answerable) --
    # just enough to stop a 2-line wrap from silently halving a real match.
    candidates = []
    for i, s in enumerate(lines):
        if not s or s in KNOWN_SECTIONS or is_role_header(s):
            continue
        candidates.append(s.lstrip("•").strip())
        if i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and nxt not in KNOWN_SECTIONS and not is_role_header(nxt):
                candidates.append((s + " " + nxt).lstrip("•").strip())

    best_score, best_bullet, best_shared = 0.0, "", 0
    for s_clean in candidates:
        if len(s_clean) < 20:
            continue
        bullet_words = set(re.findall(r"[a-z]{4,}", s_clean.lower()))
        if not bullet_words:
            continue
        score = len(add_words & bullet_words) / len(add_words | bullet_words)
        # Same number AND at least three shared content words is the same claim worded differently. Jaccard alone misses
        # it when a long addition restates a short bullet (a long addition vs "Standardized feed processing
        # across 127 exchanges": shared 127, exchanges, feed, processing; the saved resume ended up with both bullets).
        nums_a = {n for n in re.findall(r"\b\d{2,}\b", addition_text) if not re.fullmatch(r"(?:19|20)\d\d", n)}
        shared_n = len(add_words & bullet_words)
        if nums_a & set(re.findall(r"\b\d{2,}\b", s_clean)) and shared_n >= 3:
            score = max(score, 0.30)
        # among equal scores keep the bullet that shares the most words, so the card names the real duplicate
        if score > best_score or (score == best_score and score > 0 and
                                  (shared_n > best_shared or (shared_n == best_shared and len(s_clean) < len(best_bullet)))):
            best_shared = shared_n
            best_score, best_bullet = score, s_clean
    # "(cid:127)" is a PDF-conversion bullet artifact in most base variants;
    # cosmetic only, stripped so the review card shows readable text.
    return best_score, re.sub(r"\(cid:\d+\)\s*", "", best_bullet).strip()


HEDGE_RE = re.compile(
    r"\blikely\b|\bprobably\b|\bpresumably\b|\bmay have\b|\bmight have\b"
    r"|\bpossibly\b|\bappears to\b|\bcould have\b|\bseems to\b|\bassuming\b",
    re.IGNORECASE,
)


def check_hedge_language(why_text: str):
    """True if the model's OWN justification for a Stage 3 edit contains
    hedge language. Found by testing against a real batch: an edit that
    inserted "Python" into a historical job justified itself with "likely
    used at Employer F given the timeframe" -- an admitted guess presented as a
    resume fact. If the tool's own reasoning hedges, that's not something
    to infer from the proposed text, it's stated outright. Tested against
    87 real edits across 6 JDs: exactly one hit, and it was this exact
    case -- zero false positives elsewhere, the highest-precision check
    built so far. Stage 3 only: Stage 4's "reason" field is built from
    "gap — where", not a free-text justification, so there's nothing to
    scan for hedging there."""
    m = HEDGE_RE.search(why_text)
    return (m is not None), (m.group() if m else None)


def _ngrams(text: str, n: int) -> set:
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower())
    return set(" ".join(words[i:i + n]) for i in range(len(words) - n + 1))


PHRASE_REPEAT_MIN_CARDS_3GRAM = 3
PHRASE_REPEAT_MIN_CARDS_2GRAM = 4


def scan_repeated_phrases(changes: list, resume_text: str) -> list:
    """Batch-level check: a phrase genuinely new to this resume (absent
    from the original base text) that shows up in several different
    proposed changes. Verified against six real JDs before being wired in:
    caught "across multi-cloud environments" (3-4 cards), "owned
    architectural decisions...including api design" (3-4 cards, matching
    the single most-flagged pattern in an independent review of that
    batch), "platform roadmap" (4 cards), "with clear slos" (3 cards) --
    all zero-cost text matching, no LLM call. 3-word phrases are checked
    at a lower threshold (3+ cards) since a 3-gram match is a stronger,
    more specific signal; 2-word phrases need a stricter threshold (4+)
    since shorter phrases recur more often by chance alone. Overlapping
    n-grams that flag the exact same set of cards (e.g. "across multi-
    cloud" and "across multi-cloud environments" both pointing at the same
    three cards) are reported once, not once per n-gram.
    Known gap, not handled here: a single word repeated inside a
    DIFFERENT surrounding phrase each time (e.g. "portfolio" used 9 times
    in 9 different two-word contexts) isn't caught by phrase matching at
    all -- confirmed on real JD13 data, where the word existed twice in
    the base resume and this check can't see that 2-to-9 amplification,
    only "was this exact phrase in the base resume at all." That needs
    frequency comparison, not presence matching, and isn't built yet."""
    base_2gram = _ngrams(resume_text, 2)
    base_3gram = _ngrams(resume_text, 3)

    phrase_cards_3, phrase_cards_2 = defaultdict(set), defaultdict(set)
    for c in changes:
        text = c.get("after", "")
        if not text:
            continue
        for ng in _ngrams(text, 3):
            if ng not in base_3gram:
                phrase_cards_3[ng].add(c["id"])
        for ng in _ngrams(text, 2):
            if ng not in base_2gram:
                phrase_cards_2[ng].add(c["id"])

    warnings, seen_card_sets = [], []
    for ng, ids in sorted(phrase_cards_3.items(), key=lambda x: (-len(x[1]), -len(x[0]), x[0])):   # ties: deterministic, not set order
        if len(ids) >= PHRASE_REPEAT_MIN_CARDS_3GRAM and ids not in seen_card_sets:
            seen_card_sets.append(ids)
            warnings.append({"phrase": ng, "change_ids": sorted(ids)})
    for ng, ids in sorted(phrase_cards_2.items(), key=lambda x: (-len(x[1]), -len(x[0]), x[0])):
        if len(ids) >= PHRASE_REPEAT_MIN_CARDS_2GRAM and ids not in seen_card_sets:
            seen_card_sets.append(ids)
            warnings.append({"phrase": ng, "change_ids": sorted(ids)})
    return warnings


# ── Edits whose Current/Paraphrase is a DESCRIPTION, not text ────────────────
# JD9's Stage 3 response said  Current: the full 5-sentence SUMMARY (about 75 words)
# and  Paraphrase: same items, reordered: Google Project Management Certificate
# first, then ...  The prompt asks for "exact quoted text", so these are format
# violations, but both are unambiguous and checkable. Each resolver verifies its
# own claim against the resume and gives up (the card is then blocked with a
# reason) rather than guess. Resolved text is shown in the diff, so the human
# approves exactly what will be written.
_SECTION_NAMES = {"professional summary": "SUMMARY", "summary": "SUMMARY", "core competencies": "CORE COMPETENCIES",
                  "competencies": "CORE COMPETENCIES", "technical stack": "TECHNICAL STACK",
                  "career highlights": "CAREER HIGHLIGHTS", "certifications": "CERTIFICATIONS"}
DESCRIBED_SECTION_RE = re.compile(
    r"^\W*(?:the\s+)?(?:full|entire|whole|complete)\b[^\n]{0,60}?\b("
    + "|".join(sorted(_SECTION_NAMES, key=len, reverse=True)) + r")\b[^\n]{0,80}$", re.IGNORECASE)
REORDER_RE = re.compile(r"\b(?:same items|re-?order(?:ed)?|re-?sequenc\w*)\b", re.IGNORECASE)
_REORDER_STOP = {"the", "two", "three", "certifications", "certification", "first", "then", "and", "items"}


def _looks_like_text(paraphrase: str) -> bool:
    return len(paraphrase.split()) >= 5 and not re.match(r"(?i)^\s*(same|unchanged|no change)\b", paraphrase)


def _resolve_section_anchor(e: dict, resume_text: str):
    """'the full 5-sentence SUMMARY (about 75 words)' -> the real Summary text.
    Returns (body, note, counts_agree). The section NAME is unambiguous, so the
    edit is made applicable; but the model's own sentence/word counts are
    checked against the real section. JD9's said 5 sentences / ~75 words and the
    real Summary is 4 / 83. A disagreement means the model may have been looking
    at a different text, so the card is shown with the real before-text but is
    NOT pre-checked (resolution_unverified)."""
    m = DESCRIBED_SECTION_RE.match(e["current"])
    if not m or not _looks_like_text(e["paraphrase"]):
        return None
    name = _SECTION_NAMES[m.group(1).lower()]
    body = style_rules.sections(resume_text).get(name)
    if not body:
        return None
    actual_s, actual_w = len(re.findall(r"[.!?](?=\s|$)", body)), len(body.split())
    problems = []
    n = re.search(r"(\d+)[- ]sentence", e["current"], re.IGNORECASE)
    if n and actual_s != int(n.group(1)):
        problems.append(f"{n.group(1)} sentences stated, {actual_s} actual")
    w = re.search(r"(?:about|~|approximately)\s*(\d+)\s*words", e["current"], re.IGNORECASE)
    if w and abs(actual_w - int(w.group(1))) > max(8, 0.2 * int(w.group(1))):
        problems.append(f"{w.group(1)} words stated, {actual_w} actual")
    note = (f"Current was a description (\"{e['current'].strip(' .')}\"); matched to the whole "
            f"{name.title()} section ({actual_s} sentences, {actual_w} words)")
    if problems:
        note += ". Not pre-checked because the model's own counts disagree (" + "; ".join(problems) + \
                "): confirm the before-text below is the section it meant"
    return body, note, not problems


def _resolve_reorder(e: dict):
    """'same items, reordered: A first, then B, then the two SAFe ...' -> the
    actual reordered line. Only accepted if every item is used exactly once and
    the result is a pure reorder (identical words)."""
    cur = re.sub(r"\s+", " ", e["current"]).strip()
    if not REORDER_RE.search(e["paraphrase"]) or " \u00b7 " not in cur:
        return None
    items = [x.strip() for x in cur.split(" \u00b7 ")]
    desc = e["paraphrase"].split(":", 1)[1] if ":" in e["paraphrase"] else e["paraphrase"]
    phrases = [p.strip(" .") for p in re.split(r",?\s*\bthen\b\s*", desc) if p.strip(" .")]
    used, ordered = set(), []
    for ph in phrases:
        toks = [t for t in re.findall(r"[A-Za-z0-9]{3,}", ph.lower()) if t not in _REORDER_STOP]
        hits = [i for i, it in enumerate(items) if i not in used and toks and all(t in it.lower() for t in toks)]
        if not hits:
            return None
        used.update(hits)
        ordered += hits
    if sorted(ordered) != list(range(len(items))):
        return None
    new = " \u00b7 ".join(items[i] for i in ordered)
    if sorted(re.findall(r"\w+", new.lower())) != sorted(re.findall(r"\w+", cur.lower())):
        return None
    return new, "Paraphrase was a description (\"" + e["paraphrase"].strip(" .")[:70] + "\"); rebuilt as a pure reorder of the current items"


def resolve_edits(edits, resume_text: str):
    """Make intelligible-but-unanchorable Stage 3 edits applicable (see above).
    Edits that already anchor, or that no resolver can verify, pass through unchanged."""
    out = []
    norm_resume, idx_map = build_norm_map(resume_text)
    for e in edits:
        e = dict(e)
        if find_span(resume_text, norm_resume, idx_map, e["current"]) is None:
            r = _resolve_section_anchor(e, resume_text)
            if r:
                e["current"], e["resolution"], counts_agree = r
                if not counts_agree:
                    e["resolution_unverified"] = True
        if REORDER_RE.search(e["paraphrase"]):
            r = _resolve_reorder(e)
            if r:
                e["paraphrase"], note = r
                e["resolution"] = (e.get("resolution", "") + " | " + note).strip(" |")
        out.append(e)
    return out


def _style_flag(change: dict) -> None:
    """Banned writing patterns this card ADDS (style_rules.py, same list the
    Stage 3/4 prompts forbid). Badge on any hit; unchecked by default at
    style_rules.CARD_UNCHECK_AT or more. Calibrated on 215 real cards: 23 had
    a hit (17 were em dashes), 1 reached the uncheck threshold."""
    hits = style_rules.introduced_hits(change.get("before", ""), change.get("after", ""))
    if not hits:
        return
    change["style_hits"] = hits
    if sum(hits.values()) >= style_rules.CARD_UNCHECK_AT:
        change["default_accepted"] = False
        change["style_flag"] = "Adds several banned writing patterns; reword before accepting"


def summary_report(resume_text: str, edits, recs, manifest, s2_additions=None, jd_name: str = None) -> dict:
    """Summary rules on the base resume and on the resume a hands-off run
    would produce (default-checked cards applied). Document-level: shown as a
    banner, never unchecks a card by itself."""
    title = style_rules.jd_title_from(jd_name) if jd_name else None
    defaults = [c["id"] for c in manifest if c.get("default_accepted")]
    projected = apply_manifest(resume_text, edits, recs, defaults, s2_additions)[0] if manifest else resume_text
    return {
        "limits": {"words": style_rules.SUMMARY_MAX_WORDS, "sentences": style_rules.SUMMARY_MAX_SENTENCES},
        "base": style_rules.check_summary(resume_text),
        "after_defaults": style_rules.check_summary(projected),
        "base_words": len(style_rules.summary_text(resume_text).split()),
        "after_defaults_words": len(style_rules.summary_text(projected).split()),
        "role": {"title": title, **style_rules.role_metrics(title)},
    }


def compute_manifest(edits, recs, s2_additions=None, s2_reference=None, s2_keywords=None, resume_text=""):
    """One dict per candidate change, independent of any accept/reject
    decision. `default_accepted` mirrors Stage 5's own auto-apply policy:
      - Stage 3 paraphrase edits: always (exact-quote, reword-only, safe)
      - Stage 4 HIGH-confidence insertions: yes
      - Stage 2 insertions and anything else: no, always human-reviewed --
        Stage 2's suggestions aren't evidence-cited the way Stage 3/4 are
    `ats_score` is a weighted count of Stage 2's own MISSING KEYWORDS this
    change's proposed text would introduce (CRITICAL=3/HIGH=2/MEDIUM=1) --
    the closest thing to "which version is more applicable to this JD's ATS
    hit" that can be computed without asking a model to judge relevance."""
    s2_additions = s2_additions or []
    s2_reference = s2_reference or []
    s2_keywords = s2_keywords or {}
    all_recs = list(recs) + list(s2_additions)

    _norm_resume, _idx_map = build_norm_map(resume_text) if resume_text else ("", [])
    changes = []
    for i, e in enumerate(edits):
        alt = find_alt_suggestion(e["current"], s2_reference)
        change = {
            "id": f"p3-{i}",
            "type": "paraphrase",
            "source": "stage3",
            "file": e["file"],
            "before": e["current"],
            "after": e["paraphrase"],
            "reason": e["why"].strip(),
            "confidence": "N/A (reword of existing fact only)",
            "ats_score": score_against_keywords(e["paraphrase"], s2_keywords),
            "word_delta": word_count(e["paraphrase"]) - word_count(e["current"]),
            "default_accepted": True,
        }
        if e.get("resolution"):
            change["resolution"] = e["resolution"]
        if e.get("resolution_unverified"):
            change["default_accepted"] = False
        if resume_text and find_span(resume_text, _norm_resume, _idx_map, e["current"]) is None:
            # Apply would skip this edit ("current text not found") while the run
            # still printed OK. Say so on the card instead, and don't pre-check it.
            change["default_accepted"] = False
            change["anchor_flag"] = ("The quoted Current text is not in the resume, so this cannot be applied "
                                     "automatically. The model may have quoted a line from a different variant")
        idx_ = ev.load_index()
        if idx_ is not None:
            bad_acr = ev.unsupported_acronyms(e["current"], e["paraphrase"], resume_text, idx_)
            if bad_acr:
                # Unchecked wherever it lands: GCP in a Technical Stack is as much a made-up claim as EHR in a Summary.
                change["unsupported_terms"] = bad_acr
                change["unsupported_blocking"] = True
                change["default_accepted"] = False
        if idx_ is not None:
            tc = ev.title_change(e["current"], e["paraphrase"], resume_text) if resume_text else None
            if tc:
                change["title_change"] = {"title": tc[0], "why": tc[1]}
                change["default_accepted"] = False
            sp = find_span(resume_text, _norm_resume, _idx_map, e["current"]) if resume_text else None
            ssp = ev.skills_section_span(resume_text) if resume_text else None
            if sp and ssp and ssp[0] <= sp[0] < ssp[1]:
                new_tools = [t for t in ev.unverified_skills(e["paraphrase"]) if t.lower() not in {x.lower() for x in ev.unverified_skills(e["current"])}]
                if new_tools:
                    change["unsupported_tools"] = new_tools
                    change["default_accepted"] = False
            if sp:
                aud, aud_file = ev.introduced_audience_terms(e["current"], e["paraphrase"], resume_text, sp[0])
                if aud:
                    change["unsupported_audience"] = aud
                    change["unsupported_audience_file"] = aud_file
                    change["default_accepted"] = False
            elsewhere = (resume_text[:sp[0]] + " " + resume_text[sp[1]:]) if sp else None
            drops = ev.dropped_specifics(e["current"], e["paraphrase"], elsewhere)
            if drops:
                change["drops"] = drops
            kept_nums, kept_dnc = ev.carried_problems(e["current"], e["paraphrase"], idx_, hint=e.get("file", ""))
            if kept_nums:
                change["keeps_unbacked"] = kept_nums
            if kept_dnc:
                change["keeps_dnc"] = [{"file": h["file"], "claim": h["claim"], "say_instead": h["say_instead"]} for h in kept_dnc]
                if ev.load_policy().get("untick_when_keeping_dnc"):
                    change["default_accepted"] = False
        ev3 = evidence_check(e["current"], e["paraphrase"], hint=e.get("file", ""))
        if ev3 and (ev3["problems"] or ev3["dnc"]):
            change["metric_status"] = ev3["metrics"]
            change["dnc_hits"] = ev3["dnc"]
            if ev3["problems"]:
                change["grounded"] = False
                change["ungrounded_numbers"] = [p["label"] for p in ev3["problems"]]
            if ev3["problems"] or (ev3["dnc"] and ev.load_policy()["dnc_unchecks_card"]):
                change["default_accepted"] = False
        hedge_found, hedge_phrase = check_hedge_language(e["why"])
        if hedge_found:
            change["default_accepted"] = False
            change["hedge_flag"] = hedge_phrase
        _style_flag(change)
        # Note only (does not uncheck): real manifests showed default-checked
        # edits growing the Summary, e.g. 78 -> 97 words, against the 45-word rule.
        if change["word_delta"] > 0 and normalize(e["current"])[:60] in normalize(style_rules.summary_text(resume_text)):
            change["summary_growth"] = change["word_delta"]
        if alt:
            change["alt_suggestion"] = {
                "source": "stage2",
                "after": alt["after"],
                "reason": alt["reason"],
                "ats_score": score_against_keywords(alt["after"], s2_keywords),
                "word_delta": word_count(alt["after"]) - word_count(e["current"]),
                "note": "Stage 2 proposed a larger rewrite here. Not auto-appliable: "
                        "its quote is a truncated fragment, not the full sentence, so a "
                        "safe find-and-replace can't be computed. Shown for comparison only.",
            }
        changes.append(change)

    # A pattern flagged across the whole batch is stronger evidence than
    # any single card looks like on its own -- once scan_identity_drift
    # confirms a real pattern (not just one edit that happens to mention
    # "product"), un-checking those specific cards by default is the
    # consistent behavior with how a Stage 4 grounding failure already
    # downgrades default_accepted. A passive warning banner elsewhere on
    # the page, with the flagged cards still sitting checked, means a
    # hands-off Apply click would still apply every one of them -- which
    # defeats the point of having caught the pattern at all.
    flagged_ids = {cid for w in scan_identity_drift(changes) for cid in w["change_ids"]}
    for c in changes:
        if c["id"] in flagged_ids:
            c["default_accepted"] = False
            c["pattern_flag"] = "Part of a batch-wide identity-drift pattern — see the warning above before accepting"

    for i, r in enumerate(all_recs):
        source = r.get("source", "stage4")
        if r.get("no_addition"):
            continue        # a note, not resume text (ids are index-based, so skipping keeps every other id stable)
        addition_text = strip_markup(r["addition_text"]) if r["addition_text"] else ""
        if not addition_text:
            # Even the fallback extraction (extract_addition_fallback) found
            # nothing -- this candidate has no actual text to show or apply.
            # A card reading "(no addition text parsed)" is pure clutter to
            # a human reviewing 20+ cards; skip it rather than surface a
            # candidate that can never be usefully approved. The gap/where
            # info isn't lost -- it's still in the Phase A section of the
            # raw ats_evidence_gap_response.txt if anyone needs to check.
            continue

        default_accepted = source == "stage4" and r["confidence"] == "HIGH" and bool(addition_text)

        ev_res = evidence_check("", addition_text, hint=f"{r.get('evidence', '')} {r.get('where', '')}")
        if ev_res is None:                       # no index yet: legacy text search over the old chunk dir
            grounded, ungrounded_numbers = check_metric_grounding(addition_text, r.get("evidence", ""))
        else:
            grounded = not ev_res["problems"]
            ungrounded_numbers = [p["label"] for p in ev_res["problems"]]
        if not grounded:
            default_accepted = False
        dnc = ev_res["dnc"] if ev_res else []
        if dnc and ev.load_policy()["dnc_unchecks_card"]:
            default_accepted = False
        spans = None
        if ev_res is not None:
            roles4 = ev.cited_roles(r.get("evidence", ""))
            if len(roles4) >= 2 and not re.search(r"highlights|summary|competenc", f"{r.get('where', '')} {r.get('gap', '')}", re.I):
                spans = [ev.role_label(x) for x in roles4]
                default_accepted = False
        stale_citation = None
        if ev_res is not None:                   # index exists
            pairs = ev.resolve_cited_files(r.get("evidence", ""))
            cites, gone = [c for c, _ in pairs], [c for c, actual in pairs if actual is None]
            if cites and len(gone) == len(cites):
                stale_citation = (f"Cites {', '.join(gone)}, which is not in your current evidence. This recommendation "
                                  f"was written against older evidence: re-run Stage 4")
                default_accepted = False

        overlap_score, overlap_match = topical_overlap_score(resume_text, addition_text)
        possible_duplicate = overlap_score >= TOPICAL_OVERLAP_WARN_THRESHOLD
        # When the card restates a line that is ITSELF the problem, say so: a card can be the compliant wording of a claim,
        # while the existing bullet carries an unbacked number and sits near a do-not-claim row. Only when the
        # card does not carry the same problem (a card that repeats the same unbacked numbers must not say "replace it").
        duplicate_hint = None
        idx4 = ev.load_index()
        if possible_duplicate and overlap_match and idx4 is not None:
            on_card = {m["raw"] for m in ev.audit_text(addition_text, idx4)["metrics"] if not m["verified"]}
            bad = [m["raw"] for m in ev.audit_text(overlap_match, idx4)["metrics"] if not m["verified"] and m["raw"] not in on_card]
            card_rules = {h["rule"] for h in ev.dnc_hits(addition_text, idx4)}
            dn = [h for h in ev.dnc_hits(overlap_match, idx4) if h["rule"] not in card_rules]
            bits = ([f"carries {', '.join(bad[:3])}, which your evidence does not back"] if bad else []) + \
                   ([f"conflicts with your do-not-claim list (\"{dn[0]['claim'][:60]}\")"] if dn else [])
            if bits:
                duplicate_hint = "The existing line " + " and ".join(bits) + ". Consider replacing it with this card instead of adding both."

        changes.append({
            "id": f"p4-{i}",
            "type": "insertion",
            "source": source,
            "file": r.get("evidence", ""),
            "before": "",
            "after": addition_text,
            "reason": f"{r['gap'].strip()} — {r['where'].strip()}",
            "confidence": r["confidence"],
            "ats_score": score_against_keywords(addition_text, s2_keywords),
            "word_delta": word_count(addition_text),
            "default_accepted": default_accepted,
            "grounded": grounded,
            "ungrounded_numbers": ungrounded_numbers,
            "metric_status": ev_res["metrics"] if ev_res else None,
            "dnc_hits": dnc,
            "stale_citation": stale_citation,
            "spans_roles": spans,
            "duplicate_hint": duplicate_hint,
            "possible_duplicate": possible_duplicate,
            "duplicate_overlap_score": round(overlap_score, 2) if possible_duplicate else None,
            "duplicate_match_snippet": overlap_match if possible_duplicate else None,
        })
        if style_rules.is_summary_insertion(addition_text, r.get("where", "")):
            changes[-1]["default_accepted"] = False
            changes[-1]["summary_flag"] = ("Adds new material to the SUMMARY. Highlights own the metrics; "
                                           "place this in Career Highlights or Experience instead")
        _style_flag(changes[-1])

    # Repeated-phrase check runs across the WHOLE batch (Stage 3 + Stage 4
    # together), unlike the identity-drift scan above which is Stage-3-only
    # by design -- verified on real data that the single most-flagged
    # repeated phrase in one batch spanned three Stage 3 cards AND one
    # Stage 4 insertion, so checking only one type would have missed it.
    repeat_flagged_ids = {cid for w in scan_repeated_phrases(changes, resume_text) for cid in w["change_ids"]}
    for c in changes:
        if c["id"] in repeat_flagged_ids and c.get("default_accepted"):
            c["default_accepted"] = False
            c["repetition_flag"] = "This phrasing repeats across several other proposed changes — see the warning above before accepting"

    return changes


def apply_manifest(resume_text: str, edits, recs, accepted_ids, s2_additions=None):
    """Apply exactly the changes named in `accepted_ids` (a set/list of
    manifest ids) and nothing else. accepted_ids == every default_accepted
    id from compute_manifest() reproduces the plain Stage 5 CLI behavior
    exactly -- that equivalence is what keeps the web review flow from
    silently doing something the CLI wouldn't."""
    all_recs = list(recs) + list(s2_additions or [])
    accepted_ids = set(accepted_ids)
    accepted_edits = [e for i, e in enumerate(edits) if f"p3-{i}" in accepted_ids]
    accepted_recs = [r for i, r in enumerate(all_recs) if f"p4-{i}" in accepted_ids]

    resume_text, applied, skipped = apply_edits(resume_text, accepted_edits)
    # force=True: a human already approved this specific text in Stage 6,
    # so the confidence gate (which exists to protect an unattended run)
    # no longer applies -- but anchor-location and near-duplicate checks
    # still run, because those catch placement/formatting problems that
    # approval doesn't fix.
    resume_text, inserted, deferred = insert_high_confidence(resume_text, accepted_recs, force=True)
    resume_text = append_review_section(resume_text, deferred)
    return resume_text, applied, skipped, inserted, deferred


# ── Report ───────────────────────────────────────────────────────────────────
def build_report(jd_name, variant, s2, applied, skipped, inserted, deferred, structural_gaps, skip_mode=False):
    lines = [f"# Stage 5 Synthesis Report — {jd_name}", ""]
    lines.append(f"**Chosen variant:** {variant}")
    lines.append(f"**Stage 2 ATS Score:** {s2['score']}/100")
    lines.append(f"**Stage 2 Shortlist Verdict:** {s2['verdict']}")
    lines.append(f"**Tokens spent by Stage 5:** 0 (deterministic — no LLM call)")
    lines.append("")

    if skip_mode:
        lines.append("## Outcome: SKIPPED — no resume synthesized")
        lines.append("")
        lines.append(
            "Stage 2 verdict is NO. Paraphrase edits (Stage 3) and evidence-sourced "
            "additions (Stage 4) can only strengthen presentation of real experience — "
            "neither can close a structural mismatch Stage 2 already flagged. Generating "
            "a resume for this JD would waste the candidate's time on a doomed "
            "application. Stage 2's own reasoning:"
        )
        lines.append("")
        lines.append(f"> {s2['reason']}")
        lines.append("")
        lines.append(
            "**Process note:** if this gate had existed before Stage 3/4 ran for this "
            "JD, their cost would have been avoided entirely — Stage 3/4 together are "
            "typically the most expensive part of a per-JD run. Wire this gate in "
            "*before* Stage 3/4 execution for future JDs, not just before Stage 5."
        )
        return "\n".join(lines)

    lines.append("## Outcome: resume synthesized")
    lines.append("")
    lines.append(f"- Stage 3 edits applied: {len(applied)}")
    lines.append(f"- Stage 3 edits skipped (text not found): {len(skipped)}")
    lines.append(f"- Stage 4 items auto-inserted (HIGH confidence + located): {len(inserted)}")
    lines.append(f"- Stage 4 items deferred (needs human review): {len(deferred)}")
    lines.append("")

    if applied:
        lines.append("## Applied — Stage 3 paraphrase edits")
        for e in applied:
            lines.append(f"- **{e['file']}**: \"{e['current'][:70]}...\" → \"{e['paraphrase'][:70]}...\"")
            lines.append(f"  - Why: {e['why'].strip()}")
        lines.append("")

    if skipped:
        lines.append("## Skipped — Stage 3 edits (review manually)")
        for e in skipped:
            lines.append(f"- **{e['file']}**: {e['reason']}")
            lines.append(f"  - Current text Stage 3 quoted: \"{e['current'][:100]}...\"")
        lines.append("")

    if inserted:
        lines.append("## Inserted — Stage 4 new content (HIGH confidence)")
        for r in inserted:
            lines.append(f"- **{r['title'].strip()}** (gap: {r['gap'].strip()})")
            lines.append(f"  - Added: {r['addition_text'][:150]}...")
            lines.append(f"  - Evidence: {r['evidence'].strip()}")
        lines.append("")

    if deferred:
        lines.append("## Deferred — Stage 4 items NOT applied (human review required)")
        for r in deferred:
            lines.append(f"- **{r['title'].strip()}** — {r['reason']}")
            lines.append(f"  - Confidence: {r['confidence']} | Evidence: {r['evidence'].strip()}")
        lines.append("")

    if structural_gaps:
        lines.append("## Known structural gaps (not fixable by this stage — cover letter / interview prep)")
        lines.append(structural_gaps.strip())
        lines.append("")

    return "\n".join(lines)


# ── Shared loader — used by both the CLI (main, below) and the web app's ───
# Stage 6 routes, so path resolution and the "which files must exist" rule
# lives in exactly one place.
class JDInputsError(Exception):
    pass


def load_jd_inputs(jd_name: str):
    analysis_dir = PROMPTS / "JD_Analysis" / jd_name
    prep_dir = PROMPTS / f"{jd_name}_PREP"
    resp_dir = prep_dir / "resp"

    chosen_variant_file = analysis_dir / ".chosen_variant"
    if not chosen_variant_file.exists():
        raise JDInputsError(f"{chosen_variant_file} not found. Run batch_prep.sh --continue first.")
    variant = chosen_variant_file.read_text().strip()

    s2_file = resp_dir / "ats_prompt_response.txt"
    s3_file = resp_dir / "ats_recommend_prompt_response.txt"
    s4_file = resp_dir / "ats_evidence_gap_response.txt"
    for f in (s2_file, s3_file, s4_file):
        if not f.exists():
            raise JDInputsError(f"missing {f}. Stage 5/6 requires Stages 2, 3, and 4 responses.")

    resume_file = OUTPUT / "resume" / f"{variant}.md"
    if not resume_file.exists():
        raise JDInputsError(f"base resume not found at {resume_file}")

    s2_text = s2_file.read_text(encoding="utf-8", errors="replace")
    s2 = parse_stage2(s2_text)
    s2_keywords = parse_stage2_keywords(s2_text)
    s2_reference, s2_additions = parse_stage2_suggestions(s2_text)
    edits, structural_gaps = parse_stage3(s3_file.read_text(encoding="utf-8", errors="replace"))
    recs = parse_stage4(s4_file.read_text(encoding="utf-8", errors="replace"))
    resume_text = resume_file.read_text(encoding="utf-8", errors="replace")
    edits = resolve_edits(edits, resume_text)
    stage4_flags = parse_stage4_flags(s4_file.read_text(encoding="utf-8", errors="replace"))

    return {
        "stage4_flags": stage4_flags,
        "s4_path": str(s4_file),
        "jd_name": jd_name,
        "variant": variant,
        "resp_dir": resp_dir,
        "resume_text": resume_text,
        "s2": s2,
        "s2_keywords": s2_keywords,
        "s2_reference": s2_reference,
        "s2_additions": s2_additions,
        "edits": edits,
        "recs": recs,
        "structural_gaps": structural_gaps,
        "out_file": REVIEW_RESUME_DIR / f"{jd_name}_new_resume.md",
        "report_file": resp_dir / "5_synthesis_report.md",
        "manifest_file": resp_dir / "5_changes.json",
    }


# ── Main ─────────────────────────────────────────────────────────────────────
# Three modes, all sharing load_jd_inputs/compute_manifest/apply_manifest so
# the CLI default, a manifest-only preview, and the Stage 6 web review UI can
# never compute three different answers for the same JD:
#   (no flags)      full Stage 5 run, default accept policy — unchanged CLI behavior
#   --manifest-only print the candidate-change manifest as JSON, write nothing
#                    (this is what web/app.py's Stage 6 "load diffs" call runs)
#   --apply-ids     write the resume using EXACTLY the given change ids
#                    (this is what web/app.py's Stage 6 "apply approved" call runs)
def main():
    ap = argparse.ArgumentParser(description="Stage 5 — synthesize final tailored resume from Stage 2-4 responses.")
    ap.add_argument("jd", help="JD name, e.g. JD2")
    ap.add_argument("--force", action="store_true", help="Overwrite existing output/review_resume/{JD}_new_resume.md")
    ap.add_argument("--ignore-gate", action="store_true",
                     help="Synthesize a resume even if Stage 2's verdict is NO (explicit override)")
    ap.add_argument("--manifest-only", action="store_true",
                     help="Print the candidate-change manifest as JSON to stdout; write nothing to disk. "
                          "Used by the Stage 6 web review UI to render the diff view.")
    ap.add_argument("--apply-ids", default=None,
                     help="Comma-separated change ids (from --manifest-only output) to apply, replacing the "
                          "default accept policy entirely. Used by the Stage 6 web review UI's Apply button.")
    args = ap.parse_args()

    try:
        ctx = load_jd_inputs(args.jd)
    except JDInputsError as e:
        if args.manifest_only:
            print(json.dumps({"ok": False, "error": str(e)}))
            return
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    jd_name, variant, resp_dir = ctx["jd_name"], ctx["variant"], ctx["resp_dir"]
    resume_text, s2 = ctx["resume_text"], ctx["s2"]
    edits, recs, structural_gaps = ctx["edits"], ctx["recs"], ctx["structural_gaps"]
    s2_keywords, s2_reference, s2_additions = ctx["s2_keywords"], ctx["s2_reference"], ctx["s2_additions"]
    out_file, report_file, manifest_file = ctx["out_file"], ctx["report_file"], ctx["manifest_file"]

    gated = s2["verdict"] == "NO" and not args.ignore_gate

    if args.manifest_only:
        manifest = [] if gated else compute_manifest(edits, recs, s2_additions, s2_reference, s2_keywords, resume_text)
        pattern_warnings = [] if gated else scan_identity_drift([c for c in manifest if c["type"] == "paraphrase"])
        repetition_warnings = [] if gated else scan_repeated_phrases(manifest, resume_text)
        summary = summary_report(resume_text, edits, recs, manifest, s2_additions, jd_name)
        diagnostics = parse_diagnostics.diagnose(jd_name, edits, recs, s2)
        print(json.dumps({
            "ok": True,
            "jd": jd_name,
            "variant": variant,
            "gated": gated,
            "s2": s2,
            "base_word_count": word_count(resume_text),
            "changes": manifest,
            "pattern_warnings": pattern_warnings,
            "repetition_warnings": repetition_warnings,
            "summary": summary,
            "diagnostics": diagnostics,
            "stage4_flags": annotate_flags(ctx.get("stage4_flags", []), resume_text),
            "date_conflicts": ev.role_date_conflicts(resume_text),
            "titles": [t for t in ev.role_title_report(resume_text) if t["status"] != "listed"],
            "unverified_skills": (lambda sp: ev.unverified_skills(resume_text[sp[0]:sp[1]]) if sp else [])(ev.skills_section_span(resume_text)),
            "evidence_audit": evidence_audit_report(resume_text),
            "evidence_staleness": stage4_staleness(ctx.get("s4_path"), recs),
            "summary_proof": summary_proof_report(resume_text, jd_name),
            "stage4_notes": [{"gap": r.get("gap") or r.get("title"), "text": r["addition_text"][:220],
                              "kind": "edit" if r.get("edit_instruction") else "covered"}
                             for r in recs if r.get("no_addition")],
        }, indent=2))
        return

    # ── GATE ── (full-run and --apply-ids modes both respect it unless overridden)
    if gated:
        report = build_report(jd_name, variant, s2, [], [], [], [], "", skip_mode=True)
        report_file.write_text(report, encoding="utf-8")
        print(f"SKIPPED — {jd_name}: Stage 2 verdict is NO. No resume written.")
        print(f"  Report: {report_file.relative_to(ROOT)}")
        if out_file.exists() and not args.force:
            print(f"  Note: {out_file.relative_to(ROOT)} already exists from a prior run — left untouched.")
        return

    if out_file.exists() and not args.force and args.apply_ids is None:
        print(f"ERROR: {out_file} already exists. Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    manifest = compute_manifest(edits, recs, s2_additions, s2_reference, s2_keywords, resume_text)
    pattern_warnings = scan_identity_drift([c for c in manifest if c["type"] == "paraphrase"])
    if pattern_warnings:
        print("⚠️  Pattern warning(s) — a batch-level check across all Stage 3 edits found:", file=sys.stderr)
        for w in pattern_warnings:
            print(f"   {w['label']}: {w['message']}", file=sys.stderr)
        print("", file=sys.stderr)
    repetition_warnings = scan_repeated_phrases(manifest, resume_text)
    if repetition_warnings:
        print("⚠️  Repetition warning(s) — a phrase new to this resume shows up in several proposed changes:", file=sys.stderr)
        for w in repetition_warnings:
            print(f"   \"{w['phrase']}\" — {len(w['change_ids'])} cards: {', '.join(w['change_ids'])}", file=sys.stderr)
        print("", file=sys.stderr)
    if args.apply_ids is not None:
        accepted_ids = [i.strip() for i in args.apply_ids.split(",") if i.strip()]
        valid_ids = {c["id"] for c in manifest}
        unknown = [i for i in accepted_ids if i not in valid_ids]
        if unknown:
            print(f"ERROR: unknown change id(s) not in this JD's manifest: {unknown}", file=sys.stderr)
            sys.exit(1)
    else:
        accepted_ids = [c["id"] for c in manifest if c["default_accepted"]]

    new_text, applied, skipped, inserted, deferred = apply_manifest(
        resume_text, edits, recs, accepted_ids, s2_additions
    )

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(new_text, encoding="utf-8")
    report = build_report(jd_name, variant, s2, applied, skipped, inserted, deferred, structural_gaps)
    report_file.write_text(report, encoding="utf-8")
    manifest_file.write_text(
        json.dumps({"jd": jd_name, "variant": variant, "changes": manifest}, indent=2),
        encoding="utf-8",
    )

    print(f"OK — {jd_name}: {len(applied)} edits applied, {len(skipped)} skipped, "
          f"{len(inserted)} additions inserted, {len(deferred)} deferred for review.")
    print(f"  Resume: {out_file.relative_to(ROOT)}")
    print(f"  Report: {report_file.relative_to(ROOT)}")
    print(f"  Manifest (for Stage 6 review UI): {manifest_file.relative_to(ROOT)}")
    audit = evidence_audit_report(new_text)
    if not audit["indexed"]:
        print("⚠️  Evidence index not built: metric and do-not-claim checks were skipped. Run ./scripts/ingest_evidence.sh")
    else:
        print(f"Evidence audit of the saved resume: {len(audit['unbacked'])} number(s) not backed by a ledger, "
              f"{audit['dnc_total']} do-not-claim hit(s).")
        for m in audit["unbacked"][:8]:
            print(f"   {m['raw']:9} x{m['count']}  {ev.STATUS_TEXT[m['status']]}")
        for g in audit["dnc_groups"][:4]:
            print(f"   DO-NOT-CLAIM x{g['count']} ({g['file']}): {g['claim'][:80]}  |  say instead: {g['say_instead'][:80]}")
    for dc in ev.role_date_conflicts(new_text):
        print(f"⚠️  Dates: {dc['role'][-45:]} says {dc['resume']}; {dc['file']} says {dc['evidence']}")
    final_warns = style_rules.check_summary(new_text)
    rm = style_rules.role_metrics(style_rules.jd_title_from(jd_name))
    if final_warns:
        print("⚠️  Summary check on the saved resume (rewrite by hand; keywords move to Highlights/Experience, not deleted):")
        for w in final_warns:
            print(f"   {w['message']}")
        print(f"   Frame the one proof as: {', '.join(rm['metrics'])}"
              f"{' (' + rm['label'] + ' role)' if rm['label'] else ''}")
    else:
        print("✅ Summary check: within limits, no metric shared with Career Highlights")


if __name__ == "__main__":
    main()
