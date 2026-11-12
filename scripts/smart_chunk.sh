#!/bin/bash

set -e

echo "🧠 Smart chunking (fixed empty chunk issue)..."

# Detect if we're already inside the Docker container.
# When running via `docker exec` from the host, the outer shell calls this
# script which then calls `docker exec markitdown` again — which works.
# When running FROM INSIDE the container (e.g. via Flask web UI), the
# nested `docker exec` fails because Docker CLI is not in the container.
# Solution: if we're inside Docker, run the chunking logic directly.

_run_chunking() {
  mkdir -p chunks
  rm -f chunks/*

  for f in output/*.md; do
    [ -f "$f" ] || continue
    filename=$(basename -- "$f")
    name="${filename%.*}"
    echo "➡️ Processing $filename"

    python3 - "$f" "$name" << 'PYEOF'
import re, sys
from pathlib import Path

src  = Path(sys.argv[1])
name = sys.argv[2]
heading = re.compile(r'^([A-Z][A-Z ]+)\s*$|^#{1,6} ')

lines = src.read_text(errors='replace').splitlines(keepends=True)
i, chunk = 1, []

def flush(c, n, idx):
    if c:
        Path(f'chunks/{n}_part_{idx}.md').write_text(''.join(c))

for line in lines:
    if heading.match(line.rstrip()) and chunk:
        flush(chunk, name, i)
        i += 1
        chunk = []
    chunk.append(line)
flush(chunk, name, i)
PYEOF

  done

  find chunks/ -type f -size 0 -delete
}

# Are we inside Docker? Check for /.dockerenv (present in every Docker container)
if [ -f "/.dockerenv" ]; then
  # Already inside the container — run directly
  _run_chunking
else
  # Running on host — exec into the container as before
  docker exec markitdown bash -c "$(declare -f _run_chunking); _run_chunking"
fi

echo "✅ Smart chunks created (no empty files)"
