#!/bin/bash

set -e

QUESTION="$1"

if [ -z "$QUESTION" ]; then
  echo "❌ Please provide a question"
  echo "Usage: ./scripts/ask.sh \"your question\""
  exit 1
fi

mkdir -p prompts
OUTPUT_FILE="prompts/question_prompt.txt"

echo "🧠 Building prompt for question: $QUESTION"

echo "You are analyzing a document in chunks." > $OUTPUT_FILE
echo "Answer the question using ALL the provided context." >> $OUTPUT_FILE
echo "" >> $OUTPUT_FILE
echo "QUESTION:" >> $OUTPUT_FILE
echo "$QUESTION" >> $OUTPUT_FILE
echo "" >> $OUTPUT_FILE
echo "DOCUMENT CHUNKS:" >> $OUTPUT_FILE

for f in chunks/*.md; do
  if [ -f "$f" ]; then
    echo "" >> $OUTPUT_FILE
    echo "----- $(basename "$f") -----" >> $OUTPUT_FILE
    cat "$f" >> $OUTPUT_FILE
  fi
done

# ✅ Copy to clipboard (mac)
if command -v pbcopy &> /dev/null; then
  cat $OUTPUT_FILE | pbcopy
  echo "✅ Prompt copied to clipboard!"
else
  echo "✅ Prompt saved to $OUTPUT_FILE"
fi
