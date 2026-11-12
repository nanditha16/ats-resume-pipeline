#!/bin/bash

set -e

QUERY="$1"

if [ -z "$QUERY" ]; then
  echo "❌ Usage: ./scripts/semantic_retrieve.sh \"your question or JD text\""
  exit 1
fi

START=$(date +%s)

# ✅ Run two-stage semantic retrieval inside container (chunk-level → paragraph rerank)
if [ -f "/.dockerenv" ]; then
  python3 /app/scripts/retrieve.py "$QUERY"
else
  docker exec markitdown python /app/scripts/retrieve.py "$QUERY"
fi

END=$(date +%s)
ELAPSED=$((END - START))
echo "⏱️  Retrieval took ${ELAPSED}s"

echo "✅ Semantic prompt saved to prompts/semantic_prompt.txt"
echo "👉 Upload prompts/semantic_prompt.txt directly to Claude.ai (file upload,"
echo "   not clipboard paste — pbcopy was confirmed to corrupt non-ASCII chars)."
