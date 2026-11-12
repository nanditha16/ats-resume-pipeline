#!/bin/bash
#
# prepare_variant.sh — Clean-slate step between Stage 0/1 (variant_rank.sh)
# and chunking/Stage 2/3. Ensures output/ contains ONLY the chosen resume
# variant before smart_chunk.sh runs, so chunks/ never ends up mixing
# multiple variants together.
#
# WHY THIS EXISTS:
# smart_chunk.sh runs `for f in output/*.md` — it chunks EVERY .md file
# sitting in output/, not just one resume. A real run showed this produces
# 20+ mixed "variants" in chunks/ (JD files, leftover resumes, fragments
# of badly-split filenames) once more than one .md file accumulates in
# output/ over a session. smart_chunk.sh's own `rm -f chunks/*` only
# guards against STALE chunks from a previous run — it does nothing about
# too many INPUT files in the current run. This script closes that gap by
# moving everything except the JD and the chosen variant out of output/
# before chunking happens.
#
# This does NOT delete anything — non-matching .md files in output/ are
# moved to output/_archive/, not removed, so nothing is lost.
#
# Usage:
#   ./scripts/prepare_variant.sh "output/resume/Nanditha_Murthy_Resume_eBay.md" "output/JD1.md"
#
# After running this, output/ will contain only:
#   - the chosen resume variant (copied in)
#   - the JD file (left as-is, if already in output/)
# and chunks/ is cleared, ready for a clean ./scripts/smart_chunk.sh run.
#
set -e

VARIANT_PATH="$1"
JD_PATH="$2"

if [ -z "$VARIANT_PATH" ]; then
  echo "❌ Usage: ./scripts/prepare_variant.sh <path/to/chosen_variant.md> [path/to/jd.md]"
  echo "   Example: ./scripts/prepare_variant.sh \"output/resume/Nanditha_Murthy_Resume_eBay.md\" \"output/JD1.md\""
  exit 1
fi

if [ ! -f "$VARIANT_PATH" ]; then
  echo "❌ Variant file not found: $VARIANT_PATH"
  exit 1
fi

VARIANT_FILENAME=$(basename "$VARIANT_PATH")
JD_FILENAME=""
if [ -n "$JD_PATH" ]; then
  if [ ! -f "$JD_PATH" ]; then
    echo "❌ JD file not found: $JD_PATH"
    exit 1
  fi
  JD_FILENAME=$(basename "$JD_PATH")
fi

echo "🧹 Preparing clean single-variant scope in output/ ..."

mkdir -p output/_archive

MOVED_COUNT=0
for f in output/*.md; do
  [ -f "$f" ] || continue
  filename=$(basename "$f")

  if [ "$filename" == "$VARIANT_FILENAME" ]; then
    continue
  fi
  if [ -n "$JD_FILENAME" ] && [ "$filename" == "$JD_FILENAME" ]; then
    continue
  fi

  mv "$f" "output/_archive/$filename"
  MOVED_COUNT=$((MOVED_COUNT + 1))
done

# Copy the chosen variant into output/ if it's not already there
# (e.g. it lives in output/resume/ per this project's convention).
if [ ! -f "output/$VARIANT_FILENAME" ]; then
  cp "$VARIANT_PATH" "output/$VARIANT_FILENAME"
  echo "📄 Copied $VARIANT_FILENAME into output/"
fi

echo "✅ Moved $MOVED_COUNT other .md file(s) to output/_archive/ (not deleted)"
echo "✅ output/ now contains only: $VARIANT_FILENAME$( [ -n "$JD_FILENAME" ] && echo " and $JD_FILENAME" )"
echo ""
echo "👉 Next: ./scripts/smart_chunk.sh  (will now only chunk the chosen variant)"
