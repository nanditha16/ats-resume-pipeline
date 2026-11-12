#!/bin/bash
# One-time script: renames old unnumbered prompt files to numbered convention
# ats_prompt.txt          → 2_ats_prompt.txt
# ats_recommend_prompt.txt → 3_ats_recommend_prompt.txt
# ats_evidence_gap_prompt.txt → 4_ats_evidence_gap_prompt.txt
# semantic_prompt.txt      → 1.5_semantic_prompt.txt
# variant_rank_prompt.txt  → 1_variant_rank_prompt.txt

cd "$(dirname "$0")/.." 2>/dev/null || true

RENAMED=0; SKIPPED=0

for prep_dir in prompts/*_PREP/prom/; do
  [ -d "$prep_dir" ] || continue
  JD=$(basename "$(dirname "$prep_dir")" _PREP)

  rename_if_needed() {
    local OLD="${prep_dir}$1"
    local NEW="${prep_dir}$2"
    if [ -f "$OLD" ] && [ ! -f "$NEW" ]; then
      mv "$OLD" "$NEW"
      echo "  ✅ $JD: $1 → $2"
      RENAMED=$((RENAMED+1))
    elif [ -f "$OLD" ] && [ -f "$NEW" ]; then
      echo "  ⚠️  $JD: both $1 and $2 exist — keeping numbered, removing old"
      rm "$OLD"
      SKIPPED=$((SKIPPED+1))
    fi
  }

  rename_if_needed "ats_prompt.txt"              "2_ats_prompt.txt"
  rename_if_needed "ats_recommend_prompt.txt"    "3_ats_recommend_prompt.txt"
  rename_if_needed "ats_evidence_gap_prompt.txt" "4_ats_evidence_gap_prompt.txt"
  rename_if_needed "semantic_prompt.txt"         "1.5_semantic_prompt.txt"
  rename_if_needed "variant_rank_prompt.txt"     "1_variant_rank_prompt.txt"
done

echo ""
echo "Done: $RENAMED renamed, $SKIPPED conflicts resolved"
