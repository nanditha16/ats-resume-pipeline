#!/bin/bash
#
# batch_prep.sh — Automates the manual prompt-generation workflow for
#                 1-25 JDs against the resume bank.
#
# EXACT SEQUENCE (mirrors the manual workflow):
#
#   SEQUENCE 1 (first run):
#     For each JDx.txt in input/other/ → run variant_rank.sh →
#     write prompts/JD_Analysis/JDx/variant_rank_prompt.txt
#
#   SEQUENCE 2 (human gate — YOU do this manually):
#     Upload each prompt to Claude.ai.
#     Paste response into prompts/JD_Analysis/JDx/variant_rank_prompt_response.txt
#     Then run: ./scripts/batch_prep.sh --continue
#
#   SEQUENCE 3-5 (--continue):
#     Read response → determine fit + chosen variant → group JDs by variant.
#     For each variant group, for each JD:
#       1. Copy JDx.md to output/
#       2. prepare_variant.sh <chosen_resume.md> output/JDx.md
#       3. smart_chunk.sh (wipes chunks/, chunks only this JD + variant)
#       4. rm -f chunks/JDx_part_*.md (keep only resume chunks)
#       5. Run Stage 2 (ats_optimize), Stage 3 (ats_recommend), Stage 4 (ats_evidence_gap)
#       6. Move all *prompt.txt from prompts/ to prompts/JDx_PREP/prom/
#       7. Clean output/JDx.md, output/<variant>.md
#       8. Repeat for next JD in this variant group, then next variant group
#
# USAGE:
#   ./scripts/batch_prep.sh              # Sequence 1: generate variant_rank prompts
#   ./scripts/batch_prep.sh --continue   # Sequences 3-5: generate stages 2-4 after responses pasted
#   ./scripts/batch_prep.sh --status     # Dashboard only
#   ./scripts/batch_prep.sh --jd JD5    # Single JD (either mode)
#
# INPUT:
#   input/other/JDx.txt                     ← paste JDs here
#   output/resume/*.md                      ← resume variants (from router.sh)
#   output/evidence/*.md                   ← evidence, whole files (from ingest_evidence.sh)
#
# OUTPUT:
#   prompts/JD_Analysis/JDx/
#     variant_rank_prompt.txt               ← upload to Claude.ai
#     variant_rank_prompt_response.txt      ← paste response here
#   prompts/JDx_PREP/
#     prom/
#       1_variant_rank_prompt.txt
#       2_ats_prompt.txt
#       3_ats_recommend_prompt.txt
#       4_ats_evidence_gap_prompt.txt
#     resp/
#       variant_rank_prompt_response.txt
#
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
log()  { echo -e "${CYAN}$*${NC}"; }
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()  { echo -e "${RED}❌ $*${NC}"; }
hdr()  { echo -e "${BOLD}${CYAN}$*${NC}"; }

# ── Parse flags ───────────────────────────────────────────────────────────────
MODE="first"
SINGLE_JD=""
for arg in "$@"; do
  case "$arg" in
    --continue) MODE="continue" ;;
    --status)   MODE="status" ;;
    --force)    FORCE="1" ;;
    --jd)       shift; SINGLE_JD="${1:-}" ;;
    JD[0-9]*)   SINGLE_JD="$arg" ;;
  esac
done

# ── Verify prerequisites ──────────────────────────────────────────────────────
check_prereqs() {
  if [ ! -d "output/resume" ] || [ -z "$(ls output/resume/*.md 2>/dev/null)" ]; then
    err "No resume variants in output/resume/. Run ./run.sh first."
    exit 1
  fi
}

# ── Collect JDs to process ────────────────────────────────────────────────────
collect_jds() {
  if [ -n "$SINGLE_JD" ]; then
    echo "$SINGLE_JD" | sed 's/\.txt$//;s/\.md$//'
  else
    find input/other -maxdepth 1 -name "JD*.txt" -print0 2>/dev/null | \
      while IFS= read -r -d '' f; do basename "$f" .txt; done | sort -V
  fi
}

# ── Find response file (accept multiple naming conventions) ───────────────────
find_response() {
  local dir="$1"
  for name in "variant_rank_prompt_response.txt" \
              "1_variant_rank_prompt_response.txt" \
              "variant_rank_response.txt"; do
    [ -f "${dir}/${name}" ] && echo "${dir}/${name}" && return
  done
  echo ""
}


# ── Extract chosen variant from Claude response ───────────────────────────────
# Returns basename (no .md) of first candidate that resolves to a real file.
# Uses temp file to avoid subshell-return bug in bash/zsh piped while loops.
extract_variant() {
  local resp_file="$1"
  [ -f "$resp_file" ] || { echo ""; return; }

  local TMP_CANDS
  TMP_CANDS=$(mktemp)

  # Candidate 1: bold .md on Top Pick lines — **Nanditha_Murthy_Resume_X.md**
  grep -i "top pick" "$resp_file" | head -3 | \
    grep -oE '\*\*[^*]+\.md\*\*' | sed 's/\*\*//g; s/\.md$//' \
    >> "$TMP_CANDS" 2>/dev/null || true

  # Candidate 2: rank-1 table row | 1 | FileName.md |
  grep -E '^\| *1 *\|' "$resp_file" | head -1 | \
    awk -F'|' '{gsub(/ /,"",$3); print $3}' | sed 's/\.md$//' \
    >> "$TMP_CANDS" 2>/dev/null || true

  # Candidate 3: bold keyword on Top Pick line without .md extension
  grep -i "top pick" "$resp_file" | head -3 | \
    grep -oE '\*\*[^*]+\*\*' | sed 's/\*\*//g; s/\.md$//' \
    >> "$TMP_CANDS" 2>/dev/null || true

  # Candidate 4: any bold .md filename anywhere in file
  grep -oE '\*\*[^*]+\.md\*\*' "$resp_file" | \
    sed 's/\*\*//g; s/\.md$//' \
    >> "$TMP_CANDS" 2>/dev/null || true

  # ── NEW: split-prompt response format ──────────────────────────────────────
  # The new format writes the variant name as plain text on the line immediately
  # after "### Top Pick" (no bold, no .md). E.g.:
  #   ### Top Pick
  #   Deloitte_2. Strongest client-facing...
  # Extract the word(s) before the first period/comma/space on that line.

  # Candidate 5: plain text line after "### Top Pick" heading (new split-prompt format)
  # Handles: "### Top Pick\nDeloitte_2. Strongest client-facing..."
  grep -A2 "^### Top Pick" "$resp_file" 2>/dev/null | \
    grep -v "^### Top Pick" | grep -v "^--$" | grep -v "^$" | head -1 | \
    sed 's/[., ].*//' | tr -d ' ' \
    >> "$TMP_CANDS" 2>/dev/null || true

  # Candidate 6: "Top Pick" line followed by variant name on same or next line
  # Handles: "**Top Pick:** Deloitte_2" or "Top Pick — Deloitte_2"
  grep -i "top pick" "$resp_file" | head -3 | \
    sed 's/.*[Pp]ick[^a-zA-Z0-9_-]*//; s/[., ].*//' | \
    grep -v "^$" \
    >> "$TMP_CANDS" 2>/dev/null || true

  # Candidate 7: rank-1 table row — extract first word segment (handles "Deloitte / Deloitte_2 (tied)")
  # Takes the first slash-separated or space-separated token from column 3
  grep -E '^\| *1 *\|' "$resp_file" | head -1 | \
    awk -F'|' '{print $3}' | \
    sed 's/^ *//; s/ .*//' | sed 's/\/.*//' | tr -d ' ' | sed 's/\.md$//' \
    >> "$TMP_CANDS" 2>/dev/null || true
  # ── end new candidates ─────────────────────────────────────────────────────

  # Try each — return first that resolves to a real file
  local RESULT=""
  while IFS= read -r cand; do
    cand=$(echo "$cand" | tr -d ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -z "$cand" ] && continue
    local resolved
    resolved=$(resolve_variant_file "$cand")
    if [ -n "$resolved" ]; then
      RESULT=$(basename "$resolved" .md)
      break
    fi
  done < "$TMP_CANDS"
  rm -f "$TMP_CANDS"

  # Fallback: variant_pick.py reads the response like a person (tied ranks such as
  # "| 1 (tie) |", a "## Top Pick" section naming the variant in prose). Only consulted
  # when every pattern above found nothing, so responses that already worked are
  # unaffected. Its note (for example which variant it tied with) goes to stderr.
  if [ -z "$RESULT" ]; then
    local HP
    HP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/variant_pick.py"
    [ -f "$HP" ] || HP="scripts/variant_pick.py"
    [ -f "$HP" ] && RESULT=$(python3 "$HP" "$resp_file" "output/resume" | head -1)
  fi
  echo "$RESULT"
}
# ── Resolve variant keyword to actual file in output/resume/ ──────────────────
# Claude may say "Pfizer", "Anthropic", or a full filename with/without spaces.
resolve_variant_file() {
  local KEYWORD="$1"
  [ -z "$KEYWORD" ] && echo "" && return

  # Strategy 1: exact glob match
  local MATCH
  MATCH=$(ls "output/resume/"*"${KEYWORD}"*.md 2>/dev/null | head -1)
  [ -n "$MATCH" ] && echo "$MATCH" && return

  # Strategy 2: case-insensitive grep (handles space vs no-space differences)
  MATCH=$(ls output/resume/*.md 2>/dev/null | grep -i "$KEYWORD" | head -1)
  [ -n "$MATCH" ] && echo "$MATCH" && return

  # Strategy 3: strip spaces/underscores/hyphens from both sides and compare
  # e.g. keyword "Walmart_staff_R-2506210" matches "Walmart_staff_ R-2506210"
  local NORM_KEY
  NORM_KEY=$(echo "$KEYWORD" | tr -d ' _-' | tr '[:upper:]' '[:lower:]')
  MATCH=$(ls output/resume/*.md 2>/dev/null | while IFS= read -r f; do
    NORM_F=$(basename "$f" .md | tr -d ' _-' | tr '[:upper:]' '[:lower:]')
    echo "$NORM_F $f"
  done | grep "${NORM_KEY}" | awk '{print $NF}' | head -1)
  [ -n "$MATCH" ] && echo "$MATCH" && return

  # Strategy 4: last segment match (just the company/role part after last underscore)
  local LAST_PART
  LAST_PART=$(echo "$KEYWORD" | sed 's/.*[_-]//' | tr '[:upper:]' '[:lower:]')
  if [ ${#LAST_PART} -gt 3 ]; then
    MATCH=$(ls output/resume/*.md 2>/dev/null | grep -i "$LAST_PART" | head -1)
    [ -n "$MATCH" ] && echo "$MATCH" && return
  fi

  echo ""
}

# ── Check if response indicates POOR FIT ─────────────────────────────────────
is_poor_fit() {
  local f="$1"
  [ -f "$f" ] && grep -qi "POOR FIT" "$f"
}

# ── Cost gate: has a Stage 2 response already come back with Shortlist: NO? ──
# batch_prep.sh never calls an LLM itself (every stage here only assembles a
# prompt FILE — see execution_policy.json's "generate_prompt_file_only"
# default), so there's no token cost inside this script to save. What this
# gate actually avoids is a MANUAL waste: the README tells users Stage 2-4
# prompts "can be uploaded in parallel," which means someone can spend a
# Claude.ai upload on Stage 3/4 before ever reading Stage 2's verdict. If a
# Stage 2 response already exists on disk (from a prior run of this JD) and
# it says NO, Stage 3/4 prompt generation is skipped so there's nothing
# tempting to upload. First-time runs (no Stage 2 response yet) always
# proceed — the verdict isn't known yet, so nothing can be gated on it.
find_stage2_response() {
  local prep_dir="$1"
  for f in "${prep_dir}/resp/ats_prompt_response.txt"; do
    [ -f "$f" ] && echo "$f" && return
  done
  echo ""
}

is_stage2_no() {
  local f="$1"
  [ -f "$f" ] && grep -qiE '\*\*Shortlist:\*\*[[:space:]]*NO\b' "$f"
}

# ══════════════════════════════════════════════════════════════════════════════
# SEQUENCE 1 — Generate variant_rank prompts for all JDs
# ══════════════════════════════════════════════════════════════════════════════
run_sequence1() {
  local JD_LIST
  JD_LIST=$(collect_jds)
  local JD_COUNT
  JD_COUNT=$(echo "$JD_LIST" | grep -c . || true)

  hdr ""
  hdr "════════════════════════════════════════"
  hdr "  SEQUENCE 1 — Variant Rank Prompts"
  hdr "  Found $JD_COUNT JD(s) to process"
  hdr "════════════════════════════════════════"
  echo ""

  while IFS= read -r JD_NAME; do
    [ -z "$JD_NAME" ] && continue
    local ANALYSIS_DIR="prompts/JD_Analysis/${JD_NAME}"
    local PROM_FILE="${ANALYSIS_DIR}/variant_rank_prompt.txt"

    # Skip if already generated — but first verify it's non-empty/complete
    # A partial run can leave a truncated file; remove it so we regenerate cleanly
    if [ -f "$PROM_FILE" ]; then
      local PROM_SIZE
      PROM_SIZE=$(wc -c < "$PROM_FILE" | tr -d ' ')
      if [ "${FORCE:-}" != "1" ] && [ "$PROM_SIZE" -gt 500 ]; then
        ok "$JD_NAME — variant_rank prompt already exists (skip, FORCE=1 to regen)"
        continue
      else
        warn "$JD_NAME — existing prompt is empty or too small (${PROM_SIZE} bytes), regenerating..."
        rm -f "$PROM_FILE"
      fi
    fi

    log "Processing: $JD_NAME"

    # Ensure JD is converted to MD in output/
    local JD_MD="output/${JD_NAME}.md"
    if [ ! -f "$JD_MD" ]; then
      if [ -f "input/other/${JD_NAME}.txt" ]; then
        log "  Converting ${JD_NAME}.txt → ${JD_MD}"
        ./scripts/router.sh "input/other/${JD_NAME}.txt" 2>/dev/null | \
          grep -E "(Saved|Routing|complete)" || true
      else
        err "  No JD file found: input/other/${JD_NAME}.txt or ${JD_MD}"
        continue
      fi
    fi

    if [ ! -f "$JD_MD" ]; then
      err "  Conversion failed — $JD_MD not found"
      continue
    fi

    # Run variant_rank.sh → produces prompts/variant_rank_prompt.txt
    mkdir -p "$ANALYSIS_DIR"
    ./scripts/variant_rank.sh "$JD_MD" "output/resume" || {
      err "  variant_rank.sh failed for $JD_NAME — see error above"
      continue
    }

    # Move prompt to JD_Analysis/JDx/
    if [ -f "prompts/variant_rank_prompt.txt" ]; then
      mv "prompts/variant_rank_prompt.txt" "$PROM_FILE"
      ok "  → ${PROM_FILE}"
    else
      err "  variant_rank_prompt.txt not produced"
    fi

    echo ""
  done <<< "$JD_LIST"

  echo ""
  ok "Sequence 1 complete."
  echo ""
  echo -e "  ${BOLD}NEXT STEPS:${NC}"
  echo "  1. For each JD, upload to Claude.ai:"
  echo "       prompts/JD_Analysis/JDx/variant_rank_prompt.txt"
  echo "  2. Paste Claude's response into:"
  echo "       prompts/JD_Analysis/JDx/variant_rank_prompt_response.txt"
  echo "  3. Run: ./scripts/batch_prep.sh --continue"
  echo ""
}

# ══════════════════════════════════════════════════════════════════════════════
# SEQUENCE 3-5 — Generate Stages 2-4 after variant responses are pasted
# ══════════════════════════════════════════════════════════════════════════════
run_sequence3() {
  local JD_LIST
  JD_LIST=$(collect_jds)

  hdr ""
  hdr "════════════════════════════════════════════"
  hdr "  SEQUENCES 3-5 — Stages 2-4 Generation"
  hdr "════════════════════════════════════════════"
  echo ""

  # ── Pass 1: read responses, build variant→JD map using temp files ────────────
  # (bash 3 / zsh compatible — no associative arrays)
  local MAP_DIR
  MAP_DIR=$(mktemp -d)
  trap "rm -rf $MAP_DIR" RETURN

  local POOR_FIT_LIST=""
  local NO_RESP_LIST=""
  local ALREADY_DONE_LIST=""

  while IFS= read -r JD_NAME; do
    [ -z "$JD_NAME" ] && continue
    local ANALYSIS_DIR="prompts/JD_Analysis/${JD_NAME}"
    local PREP_DIR="prompts/${JD_NAME}_PREP"

    # Already fully done?
    if [ -f "${PREP_DIR}/prom/4_ats_evidence_gap_prompt.txt" ] && \
       [ "${FORCE:-}" != "1" ]; then
      ALREADY_DONE_LIST="$ALREADY_DONE_LIST $JD_NAME"
      continue
    fi

    # Find response
    local RESP_FILE
    RESP_FILE=$(find_response "$ANALYSIS_DIR")
    [ -z "$RESP_FILE" ] && RESP_FILE=$(find_response "${PREP_DIR}/resp")

    if [ -z "$RESP_FILE" ]; then
      NO_RESP_LIST="$NO_RESP_LIST $JD_NAME"
      continue
    fi

    # Poor fit?
    if is_poor_fit "$RESP_FILE"; then
      POOR_FIT_LIST="$POOR_FIT_LIST $JD_NAME"
      local NO_DIR="prompts/JD_Analysis/${JD_NAME}_NO"
      if [ ! -d "$NO_DIR" ]; then
        mkdir -p "$NO_DIR"
        local PROM_FILE="${ANALYSIS_DIR}/variant_rank_prompt.txt"
        [ -f "$PROM_FILE" ] && cp "$PROM_FILE" "${NO_DIR}/variant_rank_prompt.txt"
        cp "$RESP_FILE" "${NO_DIR}/variant_rank_prompt_response.txt"
        warn "$JD_NAME → POOR FIT → ${NO_DIR}"
      fi
      continue
    fi

    # Extract variant keyword
    local VARIANT_KEY
    VARIANT_KEY=$(extract_variant "$RESP_FILE")
    local OVERRIDE_FILE="${ANALYSIS_DIR}/.chosen_variant"
    [ -z "$VARIANT_KEY" ] && [ -f "$OVERRIDE_FILE" ] && \
      VARIANT_KEY=$(cat "$OVERRIDE_FILE" | tr -d '[:space:]')

    if [ -z "$VARIANT_KEY" ]; then
      warn "$JD_NAME — Cannot extract variant. Set: echo 'Anthropic' > ${ANALYSIS_DIR}/.chosen_variant"
      NO_RESP_LIST="$NO_RESP_LIST ${JD_NAME}(no_variant)"
      continue
    fi

    # Resolve to actual file
    local VARIANT_FILE
    VARIANT_FILE=$(resolve_variant_file "$VARIANT_KEY")
    if [ -z "$VARIANT_FILE" ]; then
      warn "$JD_NAME — Variant '$VARIANT_KEY' not found in output/resume/"
      warn "  Set: echo 'ExactName' > ${ANALYSIS_DIR}/.chosen_variant"
      NO_RESP_LIST="$NO_RESP_LIST ${JD_NAME}(not_found:${VARIANT_KEY})"
      continue
    fi

    local VARIANT_BASENAME
    VARIANT_BASENAME=$(basename "$VARIANT_FILE" .md)

    # Write to map files: MAP_DIR/VARIANT_BASENAME contains list of JDs
    echo "$JD_NAME" >> "${MAP_DIR}/${VARIANT_BASENAME}.jds"
    # Write JD→varfile mapping
    echo "$VARIANT_FILE" > "${MAP_DIR}/${JD_NAME}.varfile"

  done <<< "$JD_LIST"

  # Report skipped
  for jd in $ALREADY_DONE_LIST; do ok "$jd — all prompts already generated (skipping)"; done
  for jd in $NO_RESP_LIST; do warn "Skipped: $jd"; done
  for jd in $POOR_FIT_LIST; do warn "$jd — POOR FIT"; done

  if [ -z "$(ls "${MAP_DIR}"/*.jds 2>/dev/null)" ]; then
    echo ""
    warn "Nothing to process. Paste Claude.ai responses into:"
    warn "  prompts/JD_Analysis/JDx/variant_rank_prompt_response.txt"
    echo ""
    return
  fi

  # ── Pass 2: process each variant group ────────────────────────────────────
  echo ""
  hdr "Variant groups to process:"
  for map_file in "${MAP_DIR}"/*.jds; do
    local variant=$(basename "$map_file" .jds)
    log "  $variant → $(tr '\n' ' ' < "$map_file")"
  done
  echo ""

  for map_file in "${MAP_DIR}"/*.jds; do
    local VARIANT_BASENAME
    VARIANT_BASENAME=$(basename "$map_file" .jds)
    local JD_LIST_FOR_VARIANT
    JD_LIST_FOR_VARIANT=$(cat "$map_file")
    # Get variant file from first JD in group
    local FIRST_JD
    FIRST_JD=$(head -1 "$map_file")
    local VARIANT_FILE
    VARIANT_FILE=$(cat "${MAP_DIR}/${FIRST_JD}.varfile" 2>/dev/null || echo "")

    hdr "── Variant: $VARIANT_BASENAME ──"
    log "  JDs in this group:$JD_LIST_FOR_VARIANT"
    echo ""

    while IFS= read -r JD_NAME; do
      [ -z "$JD_NAME" ] && continue
      local ANALYSIS_DIR="prompts/JD_Analysis/${JD_NAME}"
      local PREP_DIR="prompts/${JD_NAME}_PREP"
      local RESP_FILE
      RESP_FILE=$(find_response "$ANALYSIS_DIR")
      [ -z "$RESP_FILE" ] && RESP_FILE=$(find_response "${PREP_DIR}/resp")
      # Per-JD variant file (all should be same in group, but read from map)
      local VARIANT_FILE_FOR_JD
      VARIANT_FILE_FOR_JD=$(cat "${MAP_DIR}/${JD_NAME}.varfile" 2>/dev/null || echo "$VARIANT_FILE")

      local JD_MD="output/${JD_NAME}.md"

      log "  ── $JD_NAME ──"

      # Ensure JD MD exists in output/
      if [ ! -f "$JD_MD" ]; then
        if [ -f "input/other/${JD_NAME}.txt" ]; then
          log "    Converting ${JD_NAME}.txt..."
          ./scripts/router.sh "input/other/${JD_NAME}.txt" 2>/dev/null | \
            grep -E "(Saved|Routing|complete)" || true
        fi
        if [ ! -f "$JD_MD" ]; then
          # Try output/JD/ subdirectory (some users keep JDs there)
          [ -f "output/JD/${JD_NAME}.md" ] && cp "output/JD/${JD_NAME}.md" "$JD_MD"
        fi
        if [ ! -f "$JD_MD" ]; then
          err "    Cannot find $JD_MD — skipping $JD_NAME"
          continue
        fi
      fi

      # STEP 1: Stage 1.5 — prepare_variant.sh (isolates variant + JD in output/)
      log "    Step 1.5: prepare_variant.sh $VARIANT_FILE $JD_MD"
      ./scripts/prepare_variant.sh "$VARIANT_FILE_FOR_JD" "$JD_MD" 2>/dev/null && \
        ok "    Variant isolated" || warn "    prepare_variant.sh warning (continuing)"

      # STEP 2: smart_chunk.sh (wipes chunks/, chunks only this JD + variant)
      log "    Step 2: smart_chunk.sh"
      ./scripts/smart_chunk.sh 2>/dev/null && \
        ok "    Chunks created" || warn "    smart_chunk.sh warning"

      # STEP 3: Remove JD chunks (keep only resume chunks)
      log "    Step 3: rm chunks/${JD_NAME}_part_*.md"
      rm -f "chunks/${JD_NAME}_part_"*.md 2>/dev/null || true
      local CHUNK_COUNT
      CHUNK_COUNT=$(ls chunks/*.md 2>/dev/null | wc -l | tr -d ' ')
      ok "    $CHUNK_COUNT resume chunks ready"

      # STEP 4: Stage 2 — ATS optimize
      # ats_optimize.sh expects: <jd_file> <resume_file_path>
      # After prepare_variant.sh, the resume is copied into output/
      local RESUME_IN_OUTPUT="output/${VARIANT_BASENAME}.md"
      log "    Step 4: Stage 2 — ats_optimize ($RESUME_IN_OUTPUT)"
      if [ -f "$RESUME_IN_OUTPUT" ]; then
        ./scripts/ats_optimize.sh "$JD_MD" "$RESUME_IN_OUTPUT" 2>/dev/null &&           ok "    Stage 2 done" || warn "    Stage 2 failed — check ats_optimize.sh"
      else
        warn "    Stage 2 skipped — $RESUME_IN_OUTPUT not found (prepare_variant may have failed)"
      fi

      # ── COST GATE ── (see is_stage2_no() above for why this lives here)
      local EXISTING_S2_RESP
      EXISTING_S2_RESP=$(find_stage2_response "$PREP_DIR")
      if [ -z "$EXISTING_S2_RESP" ]; then
        EXISTING_S2_RESP=$(find_stage2_response "${ANALYSIS_DIR}")
      fi
      local SKIP_S3_S4=0
      if [ "${FORCE:-}" != "1" ] && is_stage2_no "$EXISTING_S2_RESP"; then
        SKIP_S3_S4=1
      fi

      # STEP 5: Stage 3 — ATS recommend
      if [ "$SKIP_S3_S4" = "1" ]; then
        warn "    Step 5: Stage 3 SKIPPED — existing Stage 2 response says Shortlist: NO"
        warn "      (nothing to upload for this JD — set FORCE=1 to generate anyway)"
      else
        log "    Step 5: Stage 3 — ats_recommend"
        ./scripts/ats_recommend.sh "$JD_MD" "$VARIANT_BASENAME" 2>/dev/null && \
          ok "    Stage 3 done" || warn "    Stage 3 failed — check ats_recommend.sh"
      fi

      # STEP 6: Stage 4 — Evidence gap
      if [ "$SKIP_S3_S4" = "1" ]; then
        warn "    Step 6: Stage 4 SKIPPED — existing Stage 2 response says Shortlist: NO"
      else
        log "    Step 6: Stage 4 — ats_evidence_gap"
        ./scripts/ats_evidence_gap.sh "$JD_MD" "$VARIANT_BASENAME" 2>/dev/null && \
          ok "    Stage 4 done" || warn "    Stage 4 failed — check ats_evidence_gap.sh"
      fi

      # STEP 7: Move all *prompt.txt from prompts/ to prompts/JDx_PREP/prom/
      mkdir -p "${PREP_DIR}/prom"
      log "    Step 7: Collecting prompts → ${PREP_DIR}/prom/"

      # Move Stage 2-4 prompts produced by the scripts
      for src_name in \
          "ats_prompt.txt:2_ats_prompt.txt" \
          "ats_recommend_prompt.txt:3_ats_recommend_prompt.txt" \
          "ats_evidence_gap_prompt.txt:4_ats_evidence_gap_prompt.txt" \
          "semantic_prompt.txt:1.5_semantic_prompt.txt"; do
        local SRC="${src_name%%:*}"
        local DST="${src_name##*:}"
        if [ -f "prompts/${SRC}" ]; then
          mv "prompts/${SRC}" "${PREP_DIR}/prom/${DST}"
          ok "      prompts/${SRC} → ${PREP_DIR}/prom/${DST}"
        fi
      done

      # Copy Stage 0/1 prompt into PREP dir. The response stays single-sourced
      # in ${ANALYSIS_DIR}/variant_rank_prompt_response.txt — jd_status() reads
      # from there only now, so no PREP/resp/ copy is made.
      local PROM1="${ANALYSIS_DIR}/variant_rank_prompt.txt"
      [ -f "$PROM1" ] && cp "$PROM1" "${PREP_DIR}/prom/1_variant_rank_prompt.txt"

      # Write .chosen_variant record
      echo "$VARIANT_BASENAME" > "${ANALYSIS_DIR}/.chosen_variant"

      # STEP 8: Clean output/ — remove JD md and variant copy
      # (prepare_variant.sh copied variant into output/; remove it)
      local VARIANT_COPY_IN_OUTPUT="output/${VARIANT_BASENAME}.md"
      [ -f "$VARIANT_COPY_IN_OUTPUT" ] && rm -f "$VARIANT_COPY_IN_OUTPUT" && \
        log "    Cleaned: output/${VARIANT_BASENAME}.md"

      # Leave JD_MD in output/ for user visibility, but note it's done
      # (user can rm manually or we note it)
      echo ""
      ok "  ✅ $JD_NAME complete → ${PREP_DIR}/prom/"
      echo ""

    done < "$map_file"  # end JD loop for this variant
    echo ""
  done  # end variant group loop
}

# ══════════════════════════════════════════════════════════════════════════════
# STATUS DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
show_status() {
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "  BATCH PREP STATUS DASHBOARD"
  echo "═══════════════════════════════════════════════════════════"
  printf "  %-20s %-14s %-8s %-8s %-8s %-22s\n" \
    "JD" "S0/1 P/R" "S2" "S3" "S4" "Status"
  echo "───────────────────────────────────────────────────────────"

  # Collect all JDs from JD_Analysis and PREP dirs
  local ALL_JDS=""
  for d in prompts/JD_Analysis/JD*; do
    [ -d "$d" ] || continue
    JD=$(basename "$d" | sed 's/_NO.*//' | sed 's/_PREP.*//' | sed 's/_Applied.*//')
    [[ "$JD" =~ ^JD[0-9] ]] && ALL_JDS="$ALL_JDS $JD"
  done
  for d in prompts/JD*_PREP; do
    [ -d "$d" ] || continue
    JD=$(basename "$d" _PREP)
    [[ "$JD" =~ ^JD[0-9] ]] && ALL_JDS="$ALL_JDS $JD"
  done
  ALL_JDS=$(echo "$ALL_JDS" | tr ' ' '\n' | sort -uV | grep -v '^$')

  while IFS= read -r JD; do
    [ -z "$JD" ] && continue
    local ADIR="prompts/JD_Analysis/${JD}"
    local PDIR="prompts/${JD}_PREP"

    local P1="❌" R1="❌" P2="❌" P3="❌" P4="❌"
    local STATUS="⏳ Need S0/1 prompt"

    # Check Stage 0/1 prompt
    [ -f "${ADIR}/variant_rank_prompt.txt" ] && P1="✅"
    [ -f "${PDIR}/prom/1_variant_rank_prompt.txt" ] && P1="✅"

    # Check Stage 0/1 response
    RESP=$(find_response "$ADIR")
    [ -z "$RESP" ] && RESP=$(find_response "${PDIR}/resp")
    [ -n "$RESP" ] && R1="✅"

    # Check if POOR FIT
    [ -n "$RESP" ] && is_poor_fit "$RESP" && STATUS="🚫 POOR FIT" && P1="✅" && R1="✅"

    # Check Stages 2-4
    # Accept both numbered (new) and unnumbered (old) prompt filenames
    { [ -f "${PDIR}/prom/2_ats_prompt.txt" ] || [ -f "${PDIR}/prom/ats_prompt.txt" ]; } && P2="✅"
    { [ -f "${PDIR}/prom/3_ats_recommend_prompt.txt" ] || [ -f "${PDIR}/prom/ats_recommend_prompt.txt" ]; } && P3="✅"
    { [ -f "${PDIR}/prom/4_ats_evidence_gap_prompt.txt" ] || [ -f "${PDIR}/prom/ats_evidence_gap_prompt.txt" ]; } && P4="✅"

    # Determine status
    if [ "$R1" = "❌" ] && [ "$P1" = "✅" ]; then
      STATUS="⏳ Need S0/1 response"
    elif [ "$R1" = "✅" ] && [ "$P4" = "❌" ] && [[ "$STATUS" != *"POOR"* ]]; then
      STATUS="⏳ Run --continue"
    elif [ "$P4" = "✅" ]; then
      STATUS="✅ All prompts ready"
    fi

    # Cost gate: Stage 2 came back but said NO — Stage 3/4 were intentionally
    # not generated (see is_stage2_no() in run_sequence3). Distinct from
    # "⏳ Run --continue" so it doesn't read as still-pending work.
    local S2_RESP_FILE
    S2_RESP_FILE=$(find_stage2_response "$PDIR")
    if is_stage2_no "$S2_RESP_FILE" && [ "$P3" = "❌" ]; then
      STATUS="🚫 Stage 2: NO — S3/4 gated (cost saved)"
    fi

    # Check _NO folders
    [ -d "prompts/JD_Analysis/${JD}_NO" ] && STATUS="🚫 POOR FIT (archived)"

    printf "  %-20s %-14s %-8s %-8s %-8s %-22s\n" \
      "$JD" "${P1}/${R1}" "$P2" "$P3" "$P4" "$STATUS"
  done <<< "$ALL_JDS"

  echo "═══════════════════════════════════════════════════════════"
  echo ""
  echo "  CHOSEN VARIANTS:"
  # Check both JD_Analysis/ and PREP dirs for .chosen_variant
  for jd_dir in prompts/JD_Analysis/JD*/ prompts/JD*_PREP/; do
    [ -d "$jd_dir" ] || continue
    local raw
    raw=$(basename "$jd_dir")
    local jd
    jd=$(echo "$raw" | sed 's/_NO.*//;s/_Applied.*//;s/_PREP//')
    [[ "$jd" =~ ^JD[0-9] ]] || continue
    local cv_file="${jd_dir}.chosen_variant"
    [ -f "$cv_file" ] && printf "  %-20s → %s\n" "$jd" "$(cat "$cv_file")"
  done | sort -u
  echo ""
  echo "  NEXT ACTIONS:"
  echo "  1. Upload prompts/JD_Analysis/JDx/variant_rank_prompt.txt to Claude.ai"
  echo "  2. Paste response → prompts/JD_Analysis/JDx/variant_rank_prompt_response.txt"
  echo "  3. Run: ./scripts/batch_prep.sh --continue"
  echo ""
}

# ── Cover letter (Stage 6) ────────────────────────────────────────────────────
if [ "${1:-}" = "--cover" ] && [ -n "${2:-}" ]; then
  JD_NAME="$2"
  PREP_DIR="prompts/${JD_NAME}_PREP"
  JD_MD="output/${JD_NAME}.md"
  [ ! -f "$JD_MD" ] && JD_MD="output/JD/${JD_NAME}.md"
  RESUME_MD=""
  for candidate in "output/review_resume/${JD_NAME}_new_resume.md" \
                   "output/${JD_NAME}_updated_resume.md" \
                   "output/Nanditha_Murthy_Resume_${JD_NAME}.md"; do
    [ -f "$candidate" ] && RESUME_MD="$candidate" && break
  done
  if [ -z "$RESUME_MD" ]; then
    warn "No updated resume MD found. Convert your PDF first:"
    warn "  docker exec markitdown markitdown '/output/YOUR.pdf' -o '/output/YOUR.md'"
    exit 1
  fi
  mkdir -p "${PREP_DIR}/prom"
  ./scripts/cover_letter.sh "$JD_MD" "$RESUME_MD" 2>/dev/null && \
    mv "prompts/cover_letter_prompt.txt" "${PREP_DIR}/prom/5_cover_letter_prompt.txt" && \
    ok "Cover letter → ${PREP_DIR}/prom/5_cover_letter_prompt.txt"
  exit 0
fi

# ── Main dispatch ─────────────────────────────────────────────────────────────
case "$MODE" in
  first)
    check_prereqs
    run_sequence1
    show_status
    ;;
  continue)
    check_prereqs
    run_sequence3
    show_status
    ;;
  status)
    show_status
    ;;
esac
