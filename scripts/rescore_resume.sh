#!/bin/bash
#
# rescore_resume.sh — Re-run Stage 2's exact scoring rubric against the
# Stage 5/6/7-finalized resume, to compare ATS alignment before vs. after
# all the review/merge/trim decisions.
#
# DOES NOT CALL ats_optimize.sh. This was the first version's real bug,
# found from a real before/after comparison that showed zero movement
# (and one regression) across multiple JDs: ats_optimize.sh retrieves
# resume context from chunks/ -- "existing chunks/ (built by
# smart_chunk.sh)" per its own comment -- which reflects whatever variant
# was LAST chunked, not the finalized resume this script is supposed to
# be scoring. Worse, chunks/ is a single shared, per-JD-overwritten
# location (prepare_variant.sh archives every other JD's file before
# chunking one) -- so running this for JD7 after JD5 was last processed
# could score JD7's job description against JD5's leftover resume chunks
# entirely. That's not a scoring problem, it's scoring the wrong resume.
#
# Fix: build a self-contained prompt using the exact same evaluation
# rubric/output-format ats_optimize.sh uses (copied verbatim below, for
# methodological consistency with the original Stage 2 score being
# compared against) but with the FULL finalized resume injected directly
# -- no retrieval, no chunks/, no cross-JD contamination possible. This
# is also literally what ats_optimize.sh's own prompt text recommends:
# "If you need full-resume context for accurate scoring, request the
# complete output/*.md file instead."
#
# Usage: ./scripts/rescore_resume.sh JD2
set -e

JD_NAME="$1"
if [ -z "$JD_NAME" ]; then
  echo "❌ Usage: ./scripts/rescore_resume.sh JDx"
  exit 1
fi

JD_MD="output/${JD_NAME}.md"
[ -f "$JD_MD" ] || JD_MD="output/_archive/${JD_NAME}.md"
if [ ! -f "$JD_MD" ]; then
  echo "❌ No JD text found for $JD_NAME (checked output/ and output/_archive/)"
  exit 1
fi
JD_TEXT=$(cat "$JD_MD")

RESUME_FILE="output/review_resume/${JD_NAME}_new_resume.md"
[ -f "$RESUME_FILE" ] || RESUME_FILE="output/${JD_NAME}_new_resume.md"
if [ ! -f "$RESUME_FILE" ]; then
  echo "❌ No finalized resume found for $JD_NAME. Run Review & merge (Stage 5/6) first."
  exit 1
fi
RESUME_TEXT=$(cat "$RESUME_FILE")
RESUME_WORDS=$(wc -w < "$RESUME_FILE" | tr -d ' ')

OUT_DIR="prompts/${JD_NAME}_PREP/prom"
mkdir -p "$OUT_DIR"
OUTPUT_FILE="${OUT_DIR}/9_ats_rescore_prompt.txt"

RUBRIC_CORE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ats_rubric_core.txt"
if [ ! -s "$RUBRIC_CORE" ]; then
  echo "❌ Shared rubric file not found or empty: $RUBRIC_CORE"
  echo "   Shared with ats_optimize.sh (Stage 2) so the two scoring passes"
  echo "   can't silently drift apart."
  exit 1
fi

cat > "$OUTPUT_FILE" << EOF
$(cat "$RUBRIC_CORE")

# Length Control
Default output ≤ 400 words — this is a re-score against an already-
finalized resume, not a fresh optimization pass. Do not propose rewrites;
that was Stage 2's job, not this one.

---

# JOB DESCRIPTION
${JD_TEXT}

---

# FINALIZED RESUME (complete — this is the exact version being scored, not a retrieved excerpt)
${RESUME_TEXT}

---
Now evaluate this resume against the job description following the process
and output format above.
EOF

echo "✅ Re-score prompt saved to $OUTPUT_FILE"
echo "   Scored against: $RESUME_FILE (${RESUME_WORDS} words, full text — no retrieval, no chunks/)"
echo ""
echo "👉 Upload directly to Claude.ai, or run:"
echo "   python3 scripts/claude_execute.py --jd ${JD_NAME} --stages rescore --force"
