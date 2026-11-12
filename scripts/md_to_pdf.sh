#!/bin/bash
#
# md_to_pdf.sh — Convert any finalized markdown file to a presentable,
# professionally formatted PDF (Liberation Serif body text, 1in margins,
# 1.4 line spacing) — suitable for cover letters, resumes, or reports.
#
# Runs inside the container via docker exec, matching every other pipeline
# stage. Uses scripts/md_to_pdf.py (reportlab — pure Python, no external
# binary dependency). An earlier version of this script used pandoc +
# wkhtmltopdf, but wkhtmltopdf is deprecated upstream and unavailable on
# Debian Trixie (the current python:3.12-slim base) — confirmed by a
# failed build, not assumed. reportlab avoids that whole category of
# apt-package-availability risk.
#
# FONT NOTE: Times New Roman itself is a licensed Microsoft font and can't
# be installed via apt. This uses Liberation Serif instead — metrically
# identical (same character widths/line breaks), the standard open-source
# drop-in replacement.
#
# Usage:
#   ./scripts/md_to_pdf.sh "output/JD1_cover.md"
#   ./scripts/md_to_pdf.sh "output/JD1_cover.md" "output/Custom_Name.pdf"
#
set -e

INPUT_MD="$1"
OUTPUT_PDF="$2"

if [ -z "$INPUT_MD" ]; then
  echo "❌ Usage: ./scripts/md_to_pdf.sh <input.md> [output.pdf]"
  exit 1
fi

if [ ! -f "$INPUT_MD" ]; then
  echo "❌ File not found: $INPUT_MD"
  echo "   (Path is checked from the host but must also resolve inside the"
  echo "   container — place files under input/, output/, or the project root.)"
  exit 1
fi

if [ -z "$OUTPUT_PDF" ]; then
  OUTPUT_PDF="${INPUT_MD%.*}.pdf"
fi

echo "📄 Converting $INPUT_MD → $OUTPUT_PDF (Liberation Serif, report-ready formatting)"

if [ -f "/.dockerenv" ]; then
  python3 /app/scripts/md_to_pdf.py "$INPUT_MD" "$OUTPUT_PDF"
else
  docker exec markitdown python /app/scripts/md_to_pdf.py "$INPUT_MD" "$OUTPUT_PDF"
fi

echo "✅ Saved $OUTPUT_PDF"
