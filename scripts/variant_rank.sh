#!/bin/bash
#
# variant_rank.sh — verifies the three static+dynamic files exist for a JD.
#
# THREE-FILE SYSTEM (no file generation here — files already exist):
#   prompts/variant_bank.txt            — variants, built once (Step 0)
#   prompts/variant_rank_prompt.txt     — instructions, built once (Step 0)
#   output/JDx.md                       — JD text, produced by the pipeline (Step 0)
#
# This script's only job: confirm all three files exist and print their paths.
# If output/JDx.md is missing, the user needs to run the pipeline first.
# If bank/instructions are missing, auto-builds them.
#
# Usage:
#   ./scripts/variant_rank.sh JD5
#   ./scripts/variant_rank.sh JD5 output/resume   # custom variant dir
#
set -e

JD_NAME="$1"
VARIANT_DIR="${2:-output/resume}"

if [ -z "$JD_NAME" ]; then
  echo "❌ Usage: ./scripts/variant_rank.sh <JD_NAME>"
  echo "   Example: ./scripts/variant_rank.sh JD5"
  exit 1
fi

# Strip path/extension if user passed a full path
JD_NAME=$(basename "$JD_NAME" .txt)
JD_NAME=$(basename "$JD_NAME" .md)

BANK_FILE="prompts/variant_bank.txt"
PROMPT_FILE="prompts/variant_rank_prompt.txt"
JD_MD="output/${JD_NAME}.md"

# ── Check output/JDx.md exists (produced by pipeline run) ────────────────────
if [ ! -f "$JD_MD" ]; then
  echo "❌ $JD_MD not found."
  echo "   Run the pipeline first (Step 0 → 'Run pipeline') to convert"
  echo "   input/other/${JD_NAME}.txt → output/${JD_NAME}.md"
  exit 1
fi

# ── Check/build static files ──────────────────────────────────────────────────
if [ ! -f "$BANK_FILE" ] || [ ! -f "$PROMPT_FILE" ]; then
  echo "⚠️  Bank or instructions file missing — building now..."
  ./scripts/build_variant_bank.sh "$VARIANT_DIR"
  echo ""
else
  # POSIX-compatible glob check (no shopt needed)
  VARIANT_FILES=$(ls "$VARIANT_DIR"/*.md 2>/dev/null || true)
  if [ -n "$VARIANT_FILES" ]; then
    BANK_MTIME=$(stat -c %Y "$BANK_FILE" 2>/dev/null || stat -f %m "$BANK_FILE" 2>/dev/null)
    for f in "$VARIANT_DIR"/*.md; do
      [ -f "$f" ] || continue
      FMTIME=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)
      if [ "$FMTIME" -gt "$BANK_MTIME" ]; then
        echo "⚠️  WARNING: '$(basename "$f")' is newer than the variant bank."
        echo "   Run ./scripts/build_variant_bank.sh --rebuild to include it."
        echo ""
        break
      fi
    done
  fi
fi

VARIANT_COUNT=$(grep -c "^### VARIANT:" "$BANK_FILE" 2>/dev/null || echo "?")
BANK_SIZE=$(wc -c < "$BANK_FILE" | tr -d ' ')
PROMPT_SIZE=$(wc -c < "$PROMPT_FILE" | tr -d ' ')
JD_SIZE=$(wc -c < "$JD_MD" | tr -d ' ')

echo "✅ All three files ready for ${JD_NAME}:"
echo ""
echo "   1. $BANK_FILE          (${BANK_SIZE} bytes, ${VARIANT_COUNT} variants)"
echo "   2. $PROMPT_FILE   (${PROMPT_SIZE} bytes)"
echo "   3. $JD_MD                      (${JD_SIZE} bytes)"
echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  Upload all THREE to Claude.ai or ChatGPT (same conversation)"
echo "══════════════════════════════════════════════════════════════"
echo "  Use file UPLOAD not copy/paste — clipboard corrupts em-dashes."
echo "  Paste Claude's response →"
echo "  prompts/JD_Analysis/${JD_NAME}/variant_rank_prompt_response.txt"
echo ""
