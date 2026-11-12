#!/bin/bash
#
# ats_recommend.sh — Stage 3 of the multi-variant ATS workflow.
#
# Takes a JD and a VARIANT NAME, and produces a prompt that maps
# recommended edits to SPECIFIC chunk files belonging ONLY to that
# variant — rather than vague prose suggestions, and rather than
# accidentally mixing chunks from multiple resumes.
#
# WHY THE VARIANT NAME IS REQUIRED (not optional):
# A real run showed chunks/ can end up containing chunks from ALL 19
# variants at once (e.g. if smart_chunk.sh was ever run against
# output/resume/*.md directly, or multiple variants were copied to
# output/ without clearing between runs). Earlier versions of this
# script blindly globbed chunks/*.md, which — when that happens —
# silently mixes unrelated resumes into one "recommendation" prompt
# with no warning. Requiring an explicit variant name and verifying
# EXACTLY ONE variant matches it closes that failure mode instead of
# relying on manual discipline to never let it happen.
#
# IMPORTANT — fit-awareness: this script only proposes PARAPHRASE edits.
# If Stage 0 (variant_rank.sh) found POOR FIT for the JD you're running
# this against, paraphrasing chunk text will not close that gap — a
# structural domain/sector mismatch isn't fixable by wording. Run this
# only after Stage 0/1 found GOOD FIT or PARTIAL FIT, on the variant
# Stage 1 identified as the top pick.
#
# Workflow (per project convention — manual variant selection):
#   1. ./scripts/variant_rank.sh "<jd>" "output/resume"   → Stage 0/1: fit
#      gate + ranking. STOP here if Stage 0 = POOR FIT.
#   2. ./scripts/prepare_variant.sh "output/resume/<chosen>.md" "output/<jd>.md"
#      → clears output/ down to ONLY the chosen variant + JD (archives
#      everything else to output/_archive/, doesn't delete). This step
#      exists because smart_chunk.sh chunks EVERY .md file in output/ —
#      skipping this step is what caused chunks/ to end up with 20+ mixed
#      variants in a real run.
#   3. ./scripts/smart_chunk.sh                            → chunks it
#   4. ./scripts/ats_optimize.sh "<jd>" "output/<variant>.md"  → Stage 2 score
#   5. ./scripts/ats_recommend.sh "<jd>" "<variant_name>"  → Stage 3 (this)
#
# Usage:
#   ./scripts/ats_recommend.sh "path/to/jd.txt" "TPM_grow_therapy"
#   ./scripts/ats_recommend.sh "Senior TPM role requiring..." "TPM_grow_therapy"
#
# <variant_name> should be a substring unique to your chosen variant's
# chunk filenames (e.g. the part after "Nanditha_Murthy_Resume_" and
# before "_part_N.md"). Run with no second argument to see what variant
# names are currently detectable in chunks/.
#
set -e

JD_INPUT="$1"
VARIANT_NAME="$2"

if [ -z "$JD_INPUT" ]; then
  echo "❌ Usage: ./scripts/ats_recommend.sh <jd_file_or_text> <variant_name>"
  exit 1
fi

if [ ! -d "chunks" ] || [ -z "$(ls -A chunks/*.md 2>/dev/null)" ]; then
  echo "❌ No chunk files found in chunks/. Run ./scripts/smart_chunk.sh first"
  echo "   (after copying your chosen resume variant into output/)."
  exit 1
fi

# Detect distinct variant names present in chunks/ by stripping the
# "_part_N.md" suffix from each filename — used both for the "no variant
# given" help message and to verify the requested name resolves to
# exactly one variant.
DETECTED_VARIANTS=$(ls chunks/*.md | xargs -n1 basename | sed -E 's/_part_[0-9]+\.md$//' | sort -u)

if [ -z "$VARIANT_NAME" ]; then
  echo "❌ Usage: ./scripts/ats_recommend.sh <jd_file_or_text> <variant_name>"
  echo ""
  echo "   Variant names currently detectable in chunks/:"
  echo "$DETECTED_VARIANTS" | sed 's/^/     /'
  echo ""
  echo "   chunks/ contains $(echo "$DETECTED_VARIANTS" | wc -l | tr -d ' ') distinct variant(s)."
  echo "   Pass one of the names above as the second argument."
  exit 1
fi

# Glob only chunks matching the requested variant name.
shopt -s nullglob
MATCHING_CHUNKS=(chunks/*"${VARIANT_NAME}"*.md)
shopt -u nullglob

if [ ${#MATCHING_CHUNKS[@]} -eq 0 ]; then
  echo "❌ No chunks found matching variant name '$VARIANT_NAME'."
  echo ""
  echo "   Variant names currently detectable in chunks/:"
  echo "$DETECTED_VARIANTS" | sed 's/^/     /'
  exit 1
fi

# Verify the matched chunks all belong to exactly ONE variant — guards
# against a variant name that's a substring of multiple different
# variants (e.g. "Walmart" would match Walmart_Distinguished,
# Walmart_Principal, AND Walmart_staff simultaneously).
MATCHED_DISTINCT_VARIANTS=$(printf '%s\n' "${MATCHING_CHUNKS[@]}" | xargs -n1 basename | sed -E 's/_part_[0-9]+\.md$//' | sort -u)
MATCHED_COUNT=$(echo "$MATCHED_DISTINCT_VARIANTS" | wc -l | tr -d ' ')

if [ "$MATCHED_COUNT" -gt 1 ]; then
  echo "❌ '$VARIANT_NAME' matches MULTIPLE distinct variants — refusing to"
  echo "   proceed rather than guess which one you meant:"
  echo "$MATCHED_DISTINCT_VARIANTS" | sed 's/^/     /'
  echo ""
  echo "   Use a more specific name that uniquely identifies one variant."
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

echo "✅ Resolved to exactly one variant: $MATCHED_DISTINCT_VARIANTS (${#MATCHING_CHUNKS[@]} chunk file(s))"
echo "⚠️  Reminder: this script proposes paraphrase-level edits only."
echo "   If Stage 0 (variant_rank.sh) found POOR FIT for this JD, skip"
echo "   this step — wording changes won't close a structural domain gap."
echo ""
echo "📦 Building chunk inventory for recommendation mapping..."

CHUNK_INVENTORY=""
for f in "${MATCHING_CHUNKS[@]}"; do
  filename=$(basename "$f")
  content=$(cat "$f")
  CHUNK_INVENTORY="${CHUNK_INVENTORY}

### FILE: ${filename}
${content}"
done

mkdir -p prompts
OUTPUT_FILE="prompts/ats_recommend_prompt.txt"

# Writing rules shared with Stage 5's checks (scripts/style_rules.py is the
# single source, so what the prompt forbids and what Stage 5 flags match).
STYLE_RULES="$(python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/style_rules.py" --prompt stage3 --jd "$JD_INPUT" --resume "output/resume/${VARIANT_NAME}.md" 2>/dev/null || true)"
if [ -z "$STYLE_RULES" ]; then
  echo "❌ Could not load writing rules from scripts/style_rules.py (--prompt stage3)"
  exit 1
fi

cat > "$OUTPUT_FILE" << EOF
# Role
You are continuing an ATS resume evaluation. In a prior pass, this resume
was scored against the job description below (ATS Match, Technical
Alignment, Leadership Alignment, Domain Alignment, gaps, missing
keywords) and the resume bank was already gated for fundamental fit
(domain/sector/stakeholder type) — this step assumes that gate passed.
Your job now is narrower and more concrete: turn remaining findings into
file-level, line-level edit instructions for this ONE chosen variant
(${MATCHED_DISTINCT_VARIANTS}).

# Boundary — what paraphrasing CAN and CANNOT fix
Paraphrasing can close PRESENTATION gaps: a real fact exists in the
resume but isn't phrased in the JD's terminology, isn't emphasized, or is
buried. Paraphrasing CANNOT close STRUCTURAL gaps: the JD requires a
type of experience (a sector, a stakeholder type, a scale of
responsibility, a credential) that has no genuine counterpart anywhere
in the chunks below. If you encounter a structural gap while doing this
task, do not stretch a loosely-related chunk to cover it — name it under
"GAPS — NO MATCHING CHUNK" instead. Stretching a chunk to imply
experience that isn't there is worse than leaving the gap visible,
because it sets up a claim the candidate can't actually back up in an
interview.

# Task
For each recommendation you would make to improve this resume's match to
the JD:
1. Identify the SPECIFIC chunk file (from the inventory below) that
   contains the relevant section.
2. Quote the exact current text from that file.
3. Propose a minimal paraphrase — reword/reorder/re-emphasize using the
   JD's terminology where the underlying fact genuinely matches. Do not
   add any skill, tool, metric, employer, or outcome not already present
   in that chunk.
4. If a JD requirement has no corresponding fact in ANY chunk below, say
   so explicitly under "GAPS — NO MATCHING CHUNK" rather than inventing
   one or forcing a weak chunk to stand in for it.

# Constraints — DO NOT BREAK THESE
- Every edit must cite the exact filename it applies to.
- Every edit must preserve all numbers exactly (years, percentages,
  dollar amounts, team sizes).
- Minimal paraphrase only — this is not a rewrite-everything pass. If a
  chunk already matches the JD's language well, say so and leave it alone
  rather than changing it for the sake of changing it.
- Do not propose a paraphrase for a structural gap (see Boundary above).
  A reworded sentence that implies experience the candidate doesn't have
  is a worse outcome than an honestly named gap.
- TITLE AND SENIORITY: never change the candidate's own title or seniority
  words (do not turn "Senior" into "Principal" or "Leader" into "Manager",
  and do not add Director, Head or VP). The job posting's title names the
  target role only.
- OWNERSHIP LEVEL: keep the level of ownership the original line claims. Do
  not turn "coordinated", "facilitated" or "supported" into "led", "owned" or
  "directed", and do not turn a count of one thing (15+ APIs) into a count of
  another (initiatives, programs).
- NO NEW AUDIENCES OR TOOLS: do not add a customer, hospital, clinic, patient,
  investor or regulator that the original line does not name. In the
  Technical Stack section, only reorder or relabel existing items; never add one.
- Do not paste JD wording into a line where it does not already describe what
  was done.
- LENGTH DISCIPLINE: a resume has a hard page limit; every word added
  here is a word that has to come from somewhere else later. Your
  Paraphrase should be word-count-neutral versus Current wherever
  possible — substitute JD terminology for existing words rather than
  appending a new clause on top of the original sentence. If a genuine
  improvement requires net-new words, keep the addition to single digits
  and say in Why why a substitution alone couldn't cover it. A Paraphrase
  that is meaningfully longer than Current should be the exception you
  can justify, not the default shape of every edit.

${STYLE_RULES}

# Output Format

## Edits by File
For each chunk file that needs a change:

### FILE: [filename]
Current: [exact quoted text]
Paraphrase: [minimal reworded version]
Why: [which JD requirement/keyword this addresses]

Rules for these three fields:
- Current must be copied character for character from the chunk, even when you
  are replacing a whole section such as the Summary. Never describe it (for
  example never write "the full summary").
- Paraphrase must be the complete replacement text, ready to paste. Never
  describe the change (for example never write "same items, reordered"; write
  the reordered line itself).
- Do not start or end Current or Paraphrase with an ellipsis, and do not wrap
  them in code fences or blockquote markers.
- Use one Current / Paraphrase / Why group per edit, each label at the start
  of its own line.

## Chunks Requiring No Change
List filenames that already match the JD well — explicitly confirming
"leave as-is" prevents unnecessary edits.

## GAPS — NO MATCHING CHUNK
JD requirements with no supporting fact in any chunk below, including
any that are structural (not fixable by paraphrase) rather than
presentational. Distinguish the two explicitly if both types appear.

---

# JOB DESCRIPTION
${JD_TEXT}

---

# CHUNK INVENTORY — variant: ${MATCHED_DISTINCT_VARIANTS} (built by smart_chunk.sh)
${CHUNK_INVENTORY}

---
Now produce the file-level edit recommendations following the format
above.
EOF

echo "✅ Recommendation prompt saved to $OUTPUT_FILE"
echo ""
echo "👉 Upload $OUTPUT_FILE directly to Claude.ai as a file attachment."
echo "   (Use file upload, not copy/paste — clipboard round-trips have"
echo "   been shown to corrupt non-ASCII characters like em-dashes.)"
