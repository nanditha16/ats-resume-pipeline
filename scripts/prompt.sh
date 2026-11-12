#!/bin/bash

set -e

mkdir -p prompts
OUTPUT_FILE="prompts/prompt_output.txt"

echo "🧠 Preparing prompt-ready chunks..." > $OUTPUT_FILE

for f in chunks/*.md; do
  if [ -f "$f" ]; then
    echo "" >> $OUTPUT_FILE
    echo "==============================" >> $OUTPUT_FILE
    echo "📄 $(basename "$f")" >> $OUTPUT_FILE
    echo "==============================" >> $OUTPUT_FILE

    echo "Summarize and extract key insights from the following:" >> $OUTPUT_FILE
    echo "" >> $OUTPUT_FILE

    cat "$f" >> $OUTPUT_FILE
    echo "" >> $OUTPUT_FILE
  fi
done

# Copy to clipboard
if command -v pbcopy &> /dev/null; then
  cat $OUTPUT_FILE | pbcopy
  echo "✅ Copied prompt to clipboard!"
else
  echo "✅ Prompt saved to $OUTPUT_FILE"
fi
