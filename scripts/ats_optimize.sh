#!/bin/bash
#
# ats_optimize.sh — Build a full ATS/recruiter/hiring-manager evaluation prompt,
# mirroring the executive-resume-ats-optimization-specialist skill structure.
#
# Given a job description (text file or inline string) and a converted resume
# markdown file, this:
#   1. Runs two-stage semantic retrieval to pull the resume sections most
#      relevant to the JD (instead of dumping the entire resume into context)
#   2. Wraps those sections in a structured evaluation prompt that produces:
#      ATS scoring, recruiter verdict, gap analysis (critical/moderate/
#      presentation/ATS), missing keywords table, prioritized rewrites,
#      and an action plan — all under a strict no-fabrication constraint.
#
# This is prompt-only: paste prompts/ats_prompt.txt into Claude.ai (or any
# LLM) to
# get the analysis. Nothing in this script calls an API.
#
# Usage:
#   ./scripts/ats_optimize.sh "path/to/jd.txt" "output/Resume.md"
#   ./scripts/ats_optimize.sh "Senior TPM role requiring..." "output/Resume.md"
#
set -e

JD_INPUT="$1"
RESUME_FILE="$2"

if [ -z "$JD_INPUT" ] || [ -z "$RESUME_FILE" ]; then
  echo "❌ Usage: ./scripts/ats_optimize.sh <jd_file_or_text> <resume_markdown_path>"
  exit 1
fi

# ✅ Accept either a path to a JD file or raw JD text on the command line.
# Guard against the common mistake of pointing this at a path that LOOKS
# like a file but doesn't exist (e.g. typo, or pointing at the pre-router
# input/ path instead of the converted output/ path) — without this check,
# such a typo silently falls through to "treat the path string itself as
# the JD text," producing a nonsense prompt with no error.
if [ -f "$JD_INPUT" ]; then
  JD_TEXT=$(cat "$JD_INPUT")
elif [[ "$JD_INPUT" == *"/"* || "$JD_INPUT" == *.txt || "$JD_INPUT" == *.md ]]; then
  echo "❌ '$JD_INPUT' looks like a file path but doesn't exist."
  echo "   If this JD was routed through input/other/, check for it at"
  echo "   output/$(basename "${JD_INPUT%.*}").md (router converts to .md)."
  exit 1
else
  JD_TEXT="$JD_INPUT"
fi

# ✅ Force UTF-8 explicitly. ROOT CAUSE of the mojibake seen in earlier runs:
# python:3.12-slim (this project's base image) often has no UTF-8 locale
# configured (LANG/LC_ALL unset or "C"/"POSIX"). When that's the case,
# Python's sys.stdin.read() silently decodes incoming bytes using the
# ambient locale encoding instead of UTF-8 — corrupting non-ASCII characters
# (em-dashes, smart quotes) the moment they cross from bash into Python,
# before any "fix" logic can run. PYTHONIOENCODING forces UTF-8 regardless
# of container locale, which is the actual fix — the previous multi-chain
# decode "repair" was solving the wrong layer of the problem.
JD_TEXT=$(PYTHONIOENCODING=utf-8 python3 -c "
import sys
text = sys.stdin.read()
print(text, end='')
" <<< "$JD_TEXT")

if [ ! -f "$RESUME_FILE" ]; then
  echo "❌ Resume file not found: $RESUME_FILE"
  exit 1
fi

echo "🧠 Retrieving resume sections relevant to this JD..."

# ✅ Stage 1: semantic retrieval against existing chunks/ (built by smart_chunk.sh)
#    Use the JD text itself as the query so retrieval is JD-driven, not keyword-driven.
RETRIEVAL_START=$(date +%s)
# Write JD text to a temp file to avoid argument-length/quoting issues
# when passing large multi-line text through shell arguments or docker exec.
JD_TMPFILE=$(mktemp /tmp/jd_query_XXXXXX.txt)
printf '%s' "$JD_TEXT" > "$JD_TMPFILE"

if [ -f "/.dockerenv" ]; then
  # Inside Docker — run directly, read query from file
  python3 - "$JD_TMPFILE" << 'PYEOF_WRAPPER'
import sys
from pathlib import Path
# Read query from file path passed as arg to avoid arg length limits
query_file = sys.argv[1]
query = Path(query_file).read_text(encoding="utf-8")
sys.argv[1] = query  # patch argv so retrieve.py works unchanged
exec(open("/app/scripts/retrieve.py").read())
PYEOF_WRAPPER
else
  # On host — copy temp file into container and run
  docker cp "$JD_TMPFILE" markitdown:/tmp/jd_query.txt
  docker exec markitdown python3 - /tmp/jd_query.txt << 'PYEOF_WRAPPER'
import sys
from pathlib import Path
query_file = sys.argv[1]
query = Path(query_file).read_text(encoding="utf-8")
sys.argv[1] = query
exec(open("/app/scripts/retrieve.py").read())
PYEOF_WRAPPER
fi
rm -f "$JD_TMPFILE"
RETRIEVAL_END=$(date +%s)
echo "⏱️  Retrieval took $((RETRIEVAL_END - RETRIEVAL_START))s"

if [ ! -f "prompts/semantic_prompt.txt" ]; then
  echo "❌ Retrieval failed — prompts/semantic_prompt.txt not produced"
  exit 1
fi

# ✅ Pull just the "RELEVANT CONTEXT" section back out of retrieve.py's output
RELEVANT_SECTIONS=$(awk '/RELEVANT CONTEXT:/{flag=1; next} flag' prompts/semantic_prompt.txt)

mkdir -p prompts
OUTPUT_FILE="prompts/ats_prompt.txt"

RUBRIC_CORE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ats_rubric_core.txt"
if [ ! -s "$RUBRIC_CORE" ]; then
  echo "❌ Shared rubric file not found or empty: $RUBRIC_CORE"
  echo "   This is scripts/ats_optimize.sh's own scoring rubric, shared with"
  echo "   rescore_resume.sh (Stage 9) so the two can't silently drift apart."
  exit 1
fi

cat > "$OUTPUT_FILE" << EOF
$(cat "$RUBRIC_CORE")

## Recommended Improvements
### Executive Summary
[Improved version]
### Experience Improvements
Current: [Original]
Improved: [Rewrite]
Reason: [Recruiter rationale]

## Prioritized Action Plan
1. Highest impact
2. Medium impact
3. Nice to have

# Length Control
Default output ≤ 600 words. Do not rewrite the full resume unless explicitly
requested. Prioritize the top 20% of improvements that drive 80% of
interview outcomes.

---

# JOB DESCRIPTION
${JD_TEXT}

---

# RESUME SECTIONS RETRIEVED AS MOST RELEVANT (ranked by semantic similarity)
NOTE: these are the top-matching excerpts only, not the full resume. If you
need full-resume context for accurate scoring, request the complete output/*.md
file instead.

${RELEVANT_SECTIONS}

---
Now evaluate this resume against the job description following the process
and output format above.
EOF

echo "✅ ATS evaluation prompt saved to $OUTPUT_FILE"
echo ""
echo "👉 Upload $OUTPUT_FILE directly to Claude.ai as a file attachment."
echo "   (pbcopy/paste was confirmed to corrupt em-dashes and smart quotes"
echo "   on the clipboard round trip — file upload preserves UTF-8 correctly.)"
