#!/bin/bash

set -e

PIPELINE_START=$(date +%s)

echo "🚀 Running full document pipeline..."
echo "   router → extract → clean → chunk"

# ✅ 1. Setup / ensure container is running
STAGE_START=$(date +%s)
./scripts/setup.sh
echo "⏱️  setup: $(($(date +%s) - STAGE_START))s"

# ✅ 2. Route + convert based on file type
#    pdf/  -> pdfplumber, fallback to MarkItDown if extraction is weak
#    docx/ -> MarkItDown
#    html/ -> MarkItDown
#    image/-> tesseract OCR
#    other/-> MarkItDown (JD .txt files etc.)
STAGE_START=$(date +%s)
./scripts/router.sh
echo "⏱️  router: $(($(date +%s) - STAGE_START))s"

# ✅ 3. Clean extracted text (encoding, bullets, broken lines, headings)
#    Runs via docker exec (perl -pi — cross-platform, Linux/Mac safe)
STAGE_START=$(date +%s)
./scripts/clean.sh
echo "⏱️  clean: $(($(date +%s) - STAGE_START))s"

# ✅ 4. Smart chunking by heading (LLM-friendly chunks in chunks/)
#    Note: chunks ALL .md files currently in output/ — use
#    prepare_variant.sh first if you need single-variant scope for
#    Stage 2/3, or rm -f chunks/<jd_basename>_part_*.md after this
#    step to drop the JD's own chunks from the retrieval candidate pool.
#    batch_prep.sh --continue handles both of these automatically.
STAGE_START=$(date +%s)
./scripts/smart_chunk.sh
echo "⏱️  smart_chunk: $(($(date +%s) - STAGE_START))s"

TOTAL=$(( $(date +%s) - PIPELINE_START ))
echo "✅ Pipeline complete! (total: ${TOTAL}s)"
echo ""
echo "👉 Next steps:"
echo ""
echo "── BATCH WORKFLOW (recommended — handles 1-25 JDs per run) ────────────"
echo ""
echo "   Non-developer UI (guided menu):"
echo "     ./scripts/ui.sh"
echo ""
echo "   Step 1 — Add JD text files, generate Stage 0/1 prompts:"
echo "     Drop JD1.txt … JD25.txt into input/other/"
echo "     ./scripts/batch_prep.sh"
echo "     → prompts/JD_Analysis/JDx/variant_rank_prompt.txt  [UPLOAD TO CLAUDE.AI]"
echo "     → Paste response into prompts/JD_Analysis/JDx/variant_rank_prompt_response.txt"
echo ""
echo "   Step 2 — Generate Stages 2-4 for all JDs with responses:"
echo "     ./scripts/batch_prep.sh --continue"
echo "     → prompts/JDx_PREP/prom/2_ats_prompt.txt           [upload to Claude.ai]"
echo "     → prompts/JDx_PREP/prom/3_ats_recommend_prompt.txt [upload to Claude.ai]"
echo "     → prompts/JDx_PREP/prom/4_ats_evidence_gap_prompt.txt [upload to Claude.ai]"
echo ""
echo "   Flags:"
echo "     ./scripts/batch_prep.sh                        # Sequence 1: Stage 0/1 for all JDs"
echo "     ./scripts/batch_prep.sh --continue             # Sequences 3-5: Stages 2-4 for all"
echo "     ./scripts/batch_prep.sh --continue --jd JD3   # Single JD only"
echo "     ./scripts/batch_prep.sh --status              # Dashboard only"
echo "     ./scripts/batch_prep.sh --cover JD3           # Stage 6 cover letter for one JD"
echo "     FORCE=1 ./scripts/batch_prep.sh --continue    # Re-run even if prompts exist"
echo ""
echo "   Step 3 — Cover letter (after updating resume PDF):"
echo "     docker exec markitdown markitdown \"/output/Updated.pdf\" -o \"/output/Updated.md\""
echo "     ./scripts/batch_prep.sh --cover JD3"
echo "     → prompts/JD3_PREP/prom/5_cover_letter_prompt.txt  [upload to Claude.ai]"
echo ""
echo "── EVIDENCE CORPUS (one-time / per update) ────────────────────────────"
echo "   Drop files into input/evidence/:"
echo "     Career_Wealth.xlsx, project_notes.pdf, etc."
echo ""
echo "   ./scripts/ingest_evidence.sh"
echo "   → output/evidence/*.md  (whole files + evidence_index.json)"
echo "   → run once per corpus update, not every ATS cycle"
echo ""
echo "── SINGLE RESUME ──────────────────────────────────────────────────────"
echo "   ATS evaluation against a JD:"
echo "     ./scripts/ats_optimize.sh \"output/JD.md\" \"output/Resume.md\""
echo "     → prompts/ats_prompt.txt  [upload to Claude.ai]"
echo ""
echo "   Cover letter:"
echo "     ./scripts/cover_letter.sh \"output/JD.md\" \"output/Resume.md\""
echo "     → prompts/cover_letter_prompt.txt  [upload to Claude.ai]"
echo "     → save Claude's output to output/cover/cover_letter.md"
echo "     ./scripts/md_to_pdf.sh \"output/cover/cover_letter.md\""
echo "     → output/cover/cover_letter.pdf"
echo ""
echo "── MULTI-VARIANT MANUAL (step-by-step) ────────────────────────────────"
echo "   1. ./scripts/variant_rank.sh \"output/JD.md\" \"output/resume\""
echo "      → prompts/variant_rank_prompt.txt  [UPLOAD TO CLAUDE.AI — manual_only]"
echo "      → paste response → prompts/JD_Analysis/JDx/variant_rank_prompt_response.txt"
echo ""
echo "   2. ./scripts/prepare_variant.sh \"output/resume/<chosen>.md\" \"output/JD.md\""
echo "      → isolates one variant + JD in output/ (archives rest)"
echo ""
echo "   3. ./scripts/smart_chunk.sh"
echo "      rm -f chunks/<jd_basename>_part_*.md   ← drop JD chunks"
echo ""
echo "   4. ./scripts/ats_optimize.sh \"output/JD.md\" \"output/<chosen>.md\""
echo "      → prompts/ats_prompt.txt  [upload to Claude.ai, or run locally]"
echo "      Optional local: ./scripts/llm_execute.sh prompts/ats_prompt.txt stage_2_ats_optimize <model>"
echo ""
echo "   5. ./scripts/ats_recommend.sh \"output/JD.md\" \"<variant_name>\""
echo "      → prompts/ats_recommend_prompt.txt  [UPLOAD TO CLAUDE.AI — manual_only]"
echo ""
echo "   6. ./scripts/ats_evidence_gap.sh \"output/JD.md\" \"<variant_name>\""
echo "      → prompts/ats_evidence_gap_prompt.txt  [UPLOAD TO CLAUDE.AI — manual_only]"
echo ""
echo "   7. docker exec markitdown markitdown \"/output/Updated.pdf\" -o \"/output/Updated.md\""
echo ""
echo "   8. ./scripts/cover_letter.sh \"output/JD.md\" \"output/Updated.md\""
echo "      → prompts/cover_letter_prompt.txt  [upload to Claude.ai]"
echo "      ./scripts/md_to_pdf.sh \"output/cover/cover_letter.md\""
echo ""
echo "── AGENT WORKFLOW (Ollama) ─────────────────────────────────────────────"
echo "   ./scripts/ats_workflow.sh \"output/JD.md\" \"output/resume\""
echo "   → interactive model selection from pulled Ollama models"
echo "   → or: --model deepseek-r1:14b to specify directly"
echo "   → Stage 0/1 and Stage 3 warn + require --override (manual_only)"
echo "   → Stage 2 runs automatically (local_allowed)"
echo ""
echo "── POLICY LAYER ────────────────────────────────────────────────────────"
echo "   Execution rules: policy/execution_policy.json"
echo "   Enforcement:     python3 policy/policy_check.py <stage_key>"
echo ""
echo "   Stage classifications:"
echo "     stage_0_1_variant_rank      → manual_only  (3/3 local models failed)"
echo "     stage_1_5_prepare_variant   → local_always (deterministic, no LLM)"
echo "     stage_2_ats_optimize        → local_allowed (advisory trust)"
echo "     stage_3_ats_recommend       → manual_only  (3/3 local models failed)"
echo "     stage_3_5_evidence_gap      → manual_only  (multi-doc attribution fails)"
echo "     cover_letter                → untested     (manual until evaluated)"
echo ""
echo "   Local execution (policy-gated):"
echo "     ./scripts/llm_execute.sh <prompt_file> <stage_key> <model>"
echo "     ./scripts/llm_execute.sh prompts/ats_prompt.txt stage_2_ats_optimize llama3.1:8b"
echo "     LLM_TIMEOUT_SECONDS=3600 ./scripts/llm_execute.sh ...  # override 1800s default"
echo ""
echo "── OUTPUT LOCATIONS ────────────────────────────────────────────────────"
echo "   prompts/JD_Analysis/JDx/     Stage 0/1 prompt + response per JD"
echo "   prompts/JDx_PREP/prom/       Numbered prompts 1-5 for each JD"
echo "   prompts/JDx_PREP/resp/       Paste Claude.ai responses here"
echo "   prompts/<model_name>/         Ollama response files, per model"
echo "   output/                       Converted + cleaned .md files"
echo "   output/resume/                Variant bank (.md, one per target role)"
echo "   output/evidence/   Evidence files + index (from ingest_evidence.sh)"
echo "   output/cover/                 Cover letters and PDFs"
echo "   output/_archive/              Files moved by prepare_variant.sh"
echo "   chunks/                       Heading-split chunks (rebuilt per JD+variant)"
echo ""
echo "   NOTE: always use output/*.md (post-clean), not input/ source files"
