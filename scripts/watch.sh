#!/bin/bash

set -e

echo "👀 Watching input folder..."

while true; do
  inotifywait -e close_write input/

  for f in input/*; do
    if [ -f "$f" ]; then
      filename=$(basename -- "$f")
      name="${filename%.*}"

      echo "⚡ Auto converting $filename"
      docker exec markitdown markitdown "/input/$filename" -o "/output/$name.md"
    fi
  done
done
