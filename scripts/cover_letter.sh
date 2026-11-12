#!/bin/bash
#
# cover_letter.sh — Build a facts-only cover letter generation prompt.
#
# TWO CALLING MODES:
#   ./scripts/cover_letter.sh JD2
#       "JD-name mode" — auto-resolves everything: JD text, the FINAL
#       reviewed/trimmed resume (not the raw variant), and a short excerpt
#       of Stage 2's own Critical Gaps. This is what the web UI's cover
#       letter button calls.
#   ./scripts/cover_letter.sh "path/to/jd.txt" "output/Resume.md"
#       Original two-argument mode, unchanged, for existing manual use and
#       batch_prep.sh --cover's call site.
#
# WHY THIS DROPPED THE SEMANTIC-RETRIEVAL STEP THE OLD VERSION HAD
# The old version pulled "top-matching sections" from chunks/ via
# retrieve.py. Two problems with that for a cover letter specifically:
#   1. chunks/ reflects whatever variant smart_chunk.sh last processed --
#      NOT the Stage 5/6/7-trimmed final resume. A bullet Stage 7 deleted
#      could still get retrieved and referenced in the letter. That's a
#      resume/cover-letter alignment bug waiting to happen, not a
#      hypothetical one.
#   2. The final resume is already ~2 pages by design (the whole point of
#      Stage 5-7) -- roughly 1,500-2,000 tokens. Retrieval's job was
#      avoiding sending an entire multi-variant resume BANK; there's no
#      bank being sent here, just one already-curated resume. Sending it
#      in full costs about the same as retrieval would have and removes
#      an entire moving part (embedding model load, chunk staleness) that
#      could silently drift from what's actually being submitted.
#
# WHY A SHORT EXCERPT OF STAGE 2'S CRITICAL GAPS GETS INJECTED
# Stage 2 already did the work of identifying what's missing for this
# specific JD -- re-deriving that from scratch here would cost tokens for
# an answer that already exists on disk. A few lines of "these are known
# gaps, address honestly or omit" costs almost nothing and is exactly the
# job-specific signal a generic cover-letter prompt doesn't have.
#
# STILL PROMPT-ONLY: this writes prompts/{JD}_PREP/prom/5_cover_letter_prompt.txt
# (JD-name mode) or prompts/cover_letter_prompt.txt (legacy mode). Nothing
# in this script calls an API -- see scripts/claude_execute.py --stages cover
# for the automated-execution path, which is explicitly override-gated
# (policy/execution_policy.json still classifies this stage "untested").
#
set -e

resolve_jd_text() {
  local jd_name="$1"
  if [ -f "output/${jd_name}.md" ]; then
    cat "output/${jd_name}.md"
  elif [ -f "output/_archive/${jd_name}.md" ]; then
    cat "output/_archive/${jd_name}.md"
  else
    echo "❌ No JD text found at output/${jd_name}.md or output/_archive/${jd_name}.md" >&2
    exit 1
  fi
}

resolve_resume_file() {
  # Deliberately does NOT fall back to the raw variant in output/resume/ --
  # that file has never been through Stage 5/6 review, and silently using
  # it here would violate the whole point of aligning the cover letter to
  # the resume actually being submitted. Consistent with how the PDF and
  # Trim buttons already refuse rather than guess when no merged resume
  # exists yet.
  local jd_name="$1"
  if [ -f "output/review_resume/${jd_name}_new_resume.md" ]; then
    echo "output/review_resume/${jd_name}_new_resume.md"
  elif [ -f "output/${jd_name}_new_resume.md" ]; then
    echo "output/${jd_name}_new_resume.md"
  else
    echo ""
  fi
}

extract_critical_gaps() {
  local jd_name="$1"
  local resp="prompts/${jd_name}_PREP/resp/ats_prompt_response.txt"
  [ -f "$resp" ] || return 0
  awk 'tolower($0) ~ /critical gaps/{flag=1; next} /^---/{if(flag) exit} flag' "$resp" | head -20
}

JD_NAME_MODE=0
if [ $# -eq 1 ] && [[ "$1" =~ ^JD[0-9]+$ ]]; then
  JD_NAME_MODE=1
  JD_NAME="$1"
  JD_TEXT=$(resolve_jd_text "$JD_NAME")
  RESUME_FILE=$(resolve_resume_file "$JD_NAME")
  if [ -z "$RESUME_FILE" ]; then
    echo "❌ No resume found for $JD_NAME (checked output/review_resume/, output/, and the chosen variant)."
    echo "   Run Review & merge (Stage 5/6) first."
    exit 1
  fi
  CRITICAL_GAPS=$(extract_critical_gaps "$JD_NAME")
  OUTPUT_DIR="prompts/${JD_NAME}_PREP/prom"
  mkdir -p "$OUTPUT_DIR"
  OUTPUT_FILE="${OUTPUT_DIR}/5_cover_letter_prompt.txt"
else
  JD_INPUT="$1"
  RESUME_FILE="$2"
  if [ -z "$JD_INPUT" ] || [ -z "$RESUME_FILE" ]; then
    echo "❌ Usage: ./scripts/cover_letter.sh <JDx | jd_file_or_text> [resume_markdown_path]"
    exit 1
  fi
  if [ -f "$JD_INPUT" ]; then
    JD_TEXT=$(cat "$JD_INPUT")
  elif [[ "$JD_INPUT" == *"/"* || "$JD_INPUT" == *.txt || "$JD_INPUT" == *.md ]]; then
    echo "❌ '$JD_INPUT' looks like a file path but doesn't exist."
    exit 1
  else
    JD_TEXT="$JD_INPUT"
  fi
  if [ ! -f "$RESUME_FILE" ]; then
    echo "❌ Resume file not found: $RESUME_FILE"
    exit 1
  fi
  CRITICAL_GAPS=""
  OUTPUT_FILE="prompts/cover_letter_prompt.txt"
  mkdir -p prompts
fi

RESUME_TEXT=$(cat "$RESUME_FILE")

GAPS_BLOCK=""
if [ -n "$CRITICAL_GAPS" ]; then
  GAPS_BLOCK="

# KNOWN GAPS (from Stage 2's ATS analysis of this exact resume against this exact JD)
Address these honestly if a natural opening exists, or omit them entirely.
Never write around them in a way that implies the gap is closed -- that's
exactly the fabrication risk the constraints above exist to prevent.

${CRITICAL_GAPS}"
fi

# Writing rules shared with Stage 5's checks (scripts/style_rules.py is the
# single source, so what the prompt forbids and what Stage 5 flags match).
STYLE_RULES="$(python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/style_rules.py" --prompt cover --jd "$JD_NAME" 2>/dev/null || true)"
if [ -z "$STYLE_RULES" ]; then
  echo "❌ Could not load writing rules from scripts/style_rules.py (--prompt cover)"
  exit 1
fi

cat > "$OUTPUT_FILE" << INNER_EOF
# Role
You are an executive career writer who writes cover letters for senior
technical and leadership candidates (Engineering Manager, Director, Staff/
Principal Engineer, Technical Program Manager, Platform/Technology
Executive). Your goal is to write a letter a hiring manager actually wants
to finish reading — specific, evidence-based, and free of generic filler —
not a keyword-stuffed restatement of the resume.

# Constraints — DO NOT BREAK THESE
Never:
- Invent experience, employers, titles, metrics, tools, or outcomes not
  present in the resume below. A cover letter claim is easier to probe in
  an interview than a resume bullet — fabrication here is higher risk,
  not lower.
- Claim direct experience in a domain the JD requires if the resume
  below doesn't support it. Name the closest real, adjacent experience
  instead and let it stand on its own merit.
- State or imply citizenship, visa status, work authorization, security
  clearance (current or eligible), or willingness to relocate/travel
  UNLESS that exact fact is explicitly stated in the resume below. This
  applies even when the JD requires it and even when the role's context
  makes a particular status seem likely (e.g. a federal/government role
  implying US citizenship) — an inferred or assumed status is exactly
  the kind of claim that can misrepresent the candidate on paper before
  they've had a chance to address it themselves. If the resume is silent
  on this, leave it out entirely; do not guess in either direction.
- Try to touch every requirement in the JD. This letter's job is to earn
  a 15-minute conversation, not to relitigate the whole resume — trying
  to address recruiter, technical, and leadership requirements all at
  once is exactly what makes a letter read as long and generic instead
  of sharp and specific.
- Use generic openers like "I am excited to apply for..." or "I believe I
  would be a great fit..." without immediately backing the claim with a
  specific, verifiable fact from the resume.
- Restate the resume's bullet points verbatim in paragraph form.

Always:
- Pick ONE, at most TWO, of the most JD-relevant facts from the resume
  and develop just those with context the resume doesn't have room for
  (why it mattered, what changed because of it). Everything else the
  candidate has done stays in the resume and the interview, not in this
  letter — resist the pull to mention a third or fourth accomplishment
  "while you're at it."
- Open with something specific to this company/role drawn from the JD
  text below (a product, a stated priority, a named team) — not a
  generic statement that could apply to any employer.
- Preserve all numbers exactly as stated in the resume (years,
  percentages, dollar amounts, team sizes).
- Keep it to 3 short paragraphs, 200-250 words total. If a draft comes in
  longer, the fix is cutting a whole accomplishment, not trimming
  adjectives — a longer letter signals the writer didn't prioritize, and
  a hiring manager skimming 50 applications reads the first two sentences
  of each; everything after that needs to earn its place.
- Close with a direct, low-friction call to action (e.g. availability for
  a conversation) rather than a restatement of enthusiasm.
- If the JD has a requirement with no corresponding resume fact, do not
  paper over it in the letter — either omit it or address it honestly in
  one clause (e.g. "while my background is in X rather than Y, the
  underlying skill of Z transfers directly"). One such acknowledgment
  maximum per letter; more than that reads as apologizing instead of
  pitching.
${GAPS_BLOCK}

${STYLE_RULES}

# Output Format
Write the letter only — no preamble, no explanation of choices, no
placeholder brackets except [Hiring Manager Name] and [Date] if unknown.
Use a standard business letter structure: greeting, 2-3 body paragraphs,
closing line, sign-off with the candidate's name from the resume.

After the letter, add a short section titled "NOTES FOR THE CANDIDATE"
listing: (1) any placeholder the candidate must fill in, (2) any JD
requirement the letter did not address because no supporting resume fact
was found.

---

# JOB DESCRIPTION
${JD_TEXT}

---

# FINALIZED RESUME (this is the exact version being submitted — reference only what's here)
${RESUME_TEXT}

---
Now write the cover letter following the constraints and output format
above.
INNER_EOF

echo "✅ Cover letter prompt saved to $OUTPUT_FILE"
if [ "$JD_NAME_MODE" = "1" ]; then
  echo "   Resume used: $RESUME_FILE"
  [ -n "$CRITICAL_GAPS" ] && echo "   Included Stage 2's Critical Gaps for this exact JD/resume pair."
fi
echo ""
echo "👉 Upload $OUTPUT_FILE directly to Claude.ai as a file attachment,"
echo "   or run: python3 scripts/claude_execute.py --jd ${JD_NAME:-<JD>} --stages cover --ignore-gate"
echo "   (cover letter automation is explicit-override-only — see policy/execution_policy.json)"
echo "   (Use file upload, not copy/paste — clipboard round-trips have"
echo "   been shown to corrupt non-ASCII characters like em-dashes.)"
