#!/bin/bash

set -e

echo "🧹 Cleaning unified output..."

_run_clean() {
  for f in output/*.md; do
    [ -f "$f" ] || continue
    echo "➡️ Cleaning $(basename "$f")"
    perl -pi -e 's/&amp;/\&/g; s/&lt;/</g; s/&gt;/>/g' "$f"
    perl -pi -e 's/[ \t]+/ /g' "$f"
    perl -0777 -pi -e 's/\n([a-z])/ $1/g' "$f"
    perl -pi -e 's/^[•·–]/- /' "$f"
    perl -pi -e 's/^([A-Z][A-Z ]+)$/\n$1\n/' "$f"
    awk 'NF{p=1} p' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  done
}

if [ -f "/.dockerenv" ]; then
  _run_clean
else
  # On host — exec into container
  docker exec markitdown bash -c "$(declare -f _run_clean); _run_clean"
fi

echo "✅ Cleaning complete"
