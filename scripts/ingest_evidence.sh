#!/bin/bash
#
# ingest_evidence.sh — Converts the evidence files placed in input/evidence/
# into ONE WHOLE .md per source under output/evidence/, then builds the
# evidence index (output/evidence/evidence_index.json).
#
# No chunking. The evidence is a curated single source of truth (Number Ledger,
# Approved Claim Copy, Do-Not-Claim tables); chunking only produced citations to
# files like X_part_5.md and cut the tables apart. Whole files + a parsed index
# let every downstream check be a lookup. See docs/public/EVIDENCE_PIPELINE.md.
#
# Run it when a file in input/evidence/ is added, changed or removed. It is
# incremental: a file whose content hash is unchanged is skipped; an output
# whose source is gone is deleted.
#
# Handles: .pdf (pdfplumber → MarkItDown → Tesseract OCR fallback chain)
#          .xlsx/.xls, .docx, .txt, .md
#
# Filenames with spaces are fully supported — output names sanitize
# spaces to underscores so downstream globs work safely.
#
# Usage:
#   ./scripts/ingest_evidence.sh
#
set -e

EVIDENCE_DIR="input/evidence"
EVIDENCE_OUT="output/evidence"
HASH_DIR="$EVIDENCE_OUT/.hash"

# Respect INSIDE_DOCKER so this script works both when invoked from the host
# (app.py sets INSIDE_DOCKER=1 because it itself runs inside the container)
# and when invoked directly on the host shell.
[ "$INSIDE_DOCKER" = "1" ] && INSIDE_DOCKER=true
[ "$INSIDE_DOCKER" != "true" ] && INSIDE_DOCKER=false
[ -f "/.dockerenv" ] && INSIDE_DOCKER=true
drun() { if $INSIDE_DOCKER; then "$@"; else docker exec markitdown "$@"; fi; }
drun_i() { if $INSIDE_DOCKER; then "$@"; else docker exec -i markitdown "$@"; fi; }

echo "📂 Ingesting evidence files from $EVIDENCE_DIR/ ..."

if [ ! -d "$EVIDENCE_DIR" ] || [ -z "$(ls -A "$EVIDENCE_DIR" 2>/dev/null | grep -v '^[.]')" ]; then
  echo "⚠️  No files found in $EVIDENCE_DIR/"
  echo "   Drop evidence files there first: .pdf, .xlsx, .txt, .docx"
  exit 1
fi

mkdir -p "$EVIDENCE_OUT" "$HASH_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────
sanitize() { echo "$1" | tr ' ' '_' | tr -d "'" | tr -d '(' | tr -d ')'; }

file_hash() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# 0 (true) when the source is new or changed, or its output is missing/tiny.
needs_convert() {
  local f="$1" safe="$2" out="$EVIDENCE_OUT/$2.md"
  [ -f "$out" ] && [ "$(wc -c < "$out")" -ge 50 ] && [ "$(cat "$HASH_DIR/$safe.sha256" 2>/dev/null)" = "$(file_hash "$f")" ] && return 1
  return 0
}

# Names every source will produce, so outputs whose source vanished can be removed.
EXPECTED=" "
while IFS= read -r -d '' f; do
  b=$(basename -- "$f"); EXPECTED="${EXPECTED}$(sanitize "${b%.*}") "
done < <(find "$EVIDENCE_DIR" -maxdepth 2 -type f \( -name "*.pdf" -o -name "*.xlsx" -o -name "*.xls" -o -name "*.docx" -o -name "*.txt" -o -name "*.md" \) -not -name ".*" -print0)

container_size() {
  drun bash -c "wc -c < '$1' 2>/dev/null || echo 0" | tr -d '[:space:]'
}

# ── PDFs: pdfplumber → MarkItDown → Tesseract OCR ─────────────────────────────
while IFS= read -r -d '' f; do
  filename=$(basename -- "$f")
  name="${filename%.*}"
  safe_name=$(sanitize "$name")
  needs_convert "$f" "$safe_name" || { echo "✓ unchanged: $filename"; continue; }
  out_md="/output/evidence/${safe_name}.md"

  echo "📄 PDF → $filename"

  # Pass 1: pdfplumber (best text fidelity for digital PDFs)
  PLUMBER_RESULT=$(drun_i python3 << PYEOF
import pdfplumber, sys, re
src = "/input/evidence/${filename}"
out = "${out_md}"
JUNK_RE = re.compile(r"^\s*scanned\s+(with|by)\s+camscanner\s*$", re.IGNORECASE)
try:
    with pdfplumber.open(src) as pdf:
        text = "\n\n".join(p.extract_text() or "" for p in pdf.pages).strip()
    # Measure signal, not noise: strip known scanner-app watermark lines
    # before deciding OK vs WEAK, or a watermark-only scan (real content is
    # an unextractable image, watermark is real embedded text) falsely
    # clears the threshold and skips the OCR fallback that's actually needed.
    signal_text = "\n".join(l for l in text.split("\n") if not JUNK_RE.match(l)).strip()
    if len(signal_text) >= 200:
        with open(out, "w") as fh:
            fh.write("# ${safe_name}\n\n" + signal_text)
        print("OK:" + str(len(signal_text)))
    else:
        print("WEAK:" + str(len(signal_text)))
except Exception as e:
    print("ERROR:" + str(e))
PYEOF
)

  if echo "$PLUMBER_RESULT" | grep -qE "^OK:"; then
    CHARS=$(echo "$PLUMBER_RESULT" | sed 's/OK://')
    echo "   ✅ pdfplumber: ${CHARS} chars"
    continue
  fi

  echo "   ↳ pdfplumber ${PLUMBER_RESULT} — trying MarkItDown"

  # Pass 2: MarkItDown
  drun markitdown "/input/evidence/${filename}" -o "${out_md}" 2>/dev/null || true

  # Measure signal, not noise: on a scanned PDF, MarkItDown can "succeed" by
  # extracting nothing but the CamScanner watermark line repeated per page.
  # Strip it before judging OK vs WEAK, same as the pdfplumber pass above —
  # otherwise a watermark-only extraction (real content is an unextractable
  # image, watermark is real embedded text) falsely clears the byte threshold
  # and skips the OCR fallback that's actually needed.
  MARKITDOWN_RESULT=$(drun_i python3 << PYEOF
import re
out = "${out_md}"
JUNK_RE = re.compile(r"^\s*scanned\s+(with|by)\s+camscanner\s*$", re.IGNORECASE)
try:
    with open(out, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    signal_text = "".join(l for l in lines if not JUNK_RE.match(l)).strip()
    if len(signal_text) >= 200:
        print("OK:" + str(len(signal_text)))
    else:
        print("WEAK:" + str(len(signal_text)))
except FileNotFoundError:
    print("WEAK:0")
PYEOF
)

  if echo "$MARKITDOWN_RESULT" | grep -qE "^OK:"; then
    CHARS=$(echo "$MARKITDOWN_RESULT" | sed 's/OK://')
    echo "   ✅ MarkItDown: ${CHARS} signal chars"
    continue
  fi

  WEAK_CHARS=$(echo "$MARKITDOWN_RESULT" | sed 's/WEAK://')
  echo "   ↳ MarkItDown weak (${WEAK_CHARS} signal chars) — trying Tesseract OCR"

  # Pass 3: Tesseract OCR (for scanned/image-only PDFs)
  # Convert PDF pages to images via pdftoppm, then OCR each page
  OCR_RESULT=$(drun_i python3 << PYEOF
import subprocess, os, glob, sys

src = "/input/evidence/${filename}"
safe = "${safe_name}"
out = "${out_md}"
work_dir = f"/tmp/ocr_{safe}"
os.makedirs(work_dir, exist_ok=True)

# Convert PDF to PNG images (one per page) using pdftoppm
try:
    r = subprocess.run(
        ["pdftoppm", "-r", "200", "-png", src, f"{work_dir}/page"],
        capture_output=True, text=True, timeout=120
    )
    pages = sorted(glob.glob(f"{work_dir}/page*.png"))
except FileNotFoundError:
    pages = []

if not pages:
    # pdftoppm not available or no pages — try ghostscript
    try:
        r = subprocess.run(
            ["gs", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m", "-r200",
             f"-sOutputFile={work_dir}/page%03d.png", src],
            capture_output=True, text=True, timeout=120
        )
        pages = sorted(glob.glob(f"{work_dir}/page*.png"))
    except FileNotFoundError:
        pages = []

if not pages:
    print("NO_IMAGES")
    sys.exit(0)

# OCR each page with Tesseract
all_text = []
for page_img in pages:
    page_name = os.path.splitext(page_img)[0]
    try:
        subprocess.run(
            ["tesseract", page_img, page_name, "-l", "eng"],
            capture_output=True, timeout=60
        )
        txt_file = page_name + ".txt"
        if os.path.exists(txt_file):
            with open(txt_file) as fh:
                all_text.append(fh.read().strip())
    except Exception as e:
        all_text.append(f"[OCR error on {os.path.basename(page_img)}: {e}]")

full_text = "\n\n".join(t for t in all_text if t)
if len(full_text) >= 100:
    with open(out, "w") as fh:
        fh.write(f"# {safe}\n\n{full_text}")
    print("OK:" + str(len(full_text)))
else:
    print("WEAK:" + str(len(full_text)))
PYEOF
)

  if echo "$OCR_RESULT" | grep -qE "^OK:"; then
    CHARS=$(echo "$OCR_RESULT" | sed 's/OK://')
    echo "   ✅ Tesseract OCR: ${CHARS} chars"
  elif echo "$OCR_RESULT" | grep -q "NO_IMAGES"; then
    echo "   ⚠️  $filename — no image conversion tool available (pdftoppm/gs missing)"
    echo "      This is likely a scanned PDF. Manual option: export pages as PNG,"
    echo "      save to input/evidence/ as .txt after OCR-ing externally."
  else
    echo "   ⚠️  $filename — all 3 extraction methods failed ($OCR_RESULT)"
    echo "      File may be encrypted, corrupted, or purely image-based without"
    echo "      an available OCR tool. Add a .txt companion file manually."
  fi

done < <(find "$EVIDENCE_DIR" -maxdepth 2 -name "*.pdf" -print0)

# ── XLSX / XLS ────────────────────────────────────────────────────────────────
while IFS= read -r -d '' f; do
  filename=$(basename -- "$f")
  name="${filename%.*}"
  safe_name=$(sanitize "$name")
  needs_convert "$f" "$safe_name" || { echo "✓ unchanged: $filename"; continue; }
  echo "📊 XLSX → $filename"
  drun markitdown "/input/evidence/${filename}" \
    -o "/output/evidence/${safe_name}.md" || echo "   ⚠️  conversion failed: $filename"
done < <(find "$EVIDENCE_DIR" -maxdepth 2 \( -name "*.xlsx" -o -name "*.xls" \) -print0)

# ── DOCX ──────────────────────────────────────────────────────────────────────
while IFS= read -r -d '' f; do
  filename=$(basename -- "$f")
  name="${filename%.*}"
  safe_name=$(sanitize "$name")
  needs_convert "$f" "$safe_name" || { echo "✓ unchanged: $filename"; continue; }
  echo "📝 DOCX → $filename"
  drun markitdown "/input/evidence/${filename}" \
    -o "/output/evidence/${safe_name}.md" || echo "   ⚠️  conversion failed: $filename (is markitdown[docx] installed?)"
done < <(find "$EVIDENCE_DIR" -maxdepth 2 -name "*.docx" -print0)

# ── TXT / MD ──────────────────────────────────────────────────────────────────
while IFS= read -r -d '' f; do
  filename=$(basename -- "$f")
  name="${filename%.*}"
  safe_name=$(sanitize "$name")
  needs_convert "$f" "$safe_name" || { echo "✓ unchanged: $filename"; continue; }
  echo "📋 TXT/MD → $filename"
  {
    echo "# ${safe_name}"
    echo ""
    cat "$f"
  } | drun_i bash -c "cat > /output/evidence/${safe_name}.md"
done < <(find "$EVIDENCE_DIR" -maxdepth 2 \( -name "*.txt" -o -name "*.md" \) -print0)

# ── Strip known scanner-app watermark noise ────────────────────────────────────
# Free/unregistered CamScanner stamps "Scanned by CamScanner" on every page of
# scanned PDFs. It survives OCR/text extraction verbatim — pure noise, adds
# tokens with zero signal to every downstream prompt. Strip before indexing.
echo "🧹 Stripping scanner watermark noise ..."
drun_i python3 << 'PYEOF_CLEAN'
import os, re

CHUNK_DIR = "output/evidence"
import subprocess
cwd = subprocess.run(["pwd"], capture_output=True, text=True).stdout.strip()
chunk_dir = os.path.join(cwd, CHUNK_DIR)
if not os.path.isdir(chunk_dir):
    chunk_dir = "/" + CHUNK_DIR

JUNK_RE = re.compile(r"^\s*scanned\s+(with|by)\s+camscanner\s*$", re.IGNORECASE)
stripped = 0
for fname in os.listdir(chunk_dir):
    if not fname.endswith(".md"):
        continue
    fpath = os.path.join(chunk_dir, fname)
    with open(fpath, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    kept = [l for l in lines if not JUNK_RE.match(l)]
    if len(kept) != len(lines):
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
        stripped += (len(lines) - len(kept))
if stripped:
    print(f"   Removed {stripped} watermark line(s).")
PYEOF_CLEAN

# ── Remove outputs whose source file is gone ─────────────────────────────────
for md in "$EVIDENCE_OUT"/*.md; do
  [ -f "$md" ] || continue
  base=$(basename "$md" .md)
  case "$EXPECTED" in
    *" $base "*) ;;
    *) rm -f "$md" "$HASH_DIR/$base.sha256"
       echo "🗑️  removed $base.md (its source is no longer in $EVIDENCE_DIR/)";;
  esac
done

# ── Record hashes of what converted successfully ─────────────────────────────
while IFS= read -r -d '' f; do
  b=$(basename -- "$f"); safe=$(sanitize "${b%.*}")
  if [ -f "$EVIDENCE_OUT/$safe.md" ] && [ "$(wc -c < "$EVIDENCE_OUT/$safe.md")" -ge 50 ]; then
    file_hash "$f" > "$HASH_DIR/$safe.sha256"
  fi
done < <(find "$EVIDENCE_DIR" -maxdepth 2 -type f \( -name "*.pdf" -o -name "*.xlsx" -o -name "*.xls" -o -name "*.docx" -o -name "*.txt" -o -name "*.md" \) -not -name ".*" -print0)

# ── Legacy chunk directory (replaced by output/evidence/) ────────────────────
if ls output/career_wealth_chunk/*.md >/dev/null 2>&1; then
  drun bash -c "rm -f /output/career_wealth_chunk/*.md"
  echo "🗑️  cleared the legacy chunk directory output/career_wealth_chunk/ (superseded)"
fi

# ── Evidence index (ledger, approved claims, do-not-claim rules) ─────────────
echo "🧾 Building the evidence index ..."
drun env ATS_ROOT=/app python3 /app/scripts/evidence_index.py build || echo "   ⚠️  index build failed; metric checks will be skipped until it succeeds"

true  # ensure set -e doesn't fire on the last command

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "✅ Evidence ingestion complete."
echo ""
echo "   Files produced in $EVIDENCE_OUT/:"
TOTAL=0
SKIPPED=0
if ls "$EVIDENCE_OUT"/*.md 2>/dev/null | grep -q .; then
  while IFS= read -r chunk; do
    SIZE=$(wc -c < "$chunk")
    if [ "$SIZE" -lt 50 ]; then
      echo "     ⚠️  $(basename "$chunk")  (${SIZE} bytes — likely empty/failed)"
      SKIPPED=$((SKIPPED + 1))
    else
      echo "     ✅ $(basename "$chunk")  (${SIZE} bytes)"
      TOTAL=$((TOTAL + 1))
    fi
  done < <(ls "$EVIDENCE_OUT"/*.md)
else
  echo "     (none — all conversions may have failed)"
fi

echo ""
[ "$SKIPPED" -gt 0 ] && echo "   ⚠️  $SKIPPED file(s) failed — see warnings above for manual action."
echo "   ✅ $TOTAL evidence file(s) ready (whole files, no chunks)."
echo ""
echo "👉 Next:"
echo "   ./scripts/ats_evidence_gap.sh \"output/JD.md\" \"<variant_name>\""
