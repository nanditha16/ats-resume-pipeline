#!/bin/bash
#
# init_dirs.sh -- create the folders the pipeline reads and writes.
#
# Git does not store empty folders, and the data folders (input/, output/, chunks/, prompts/)
# are git-ignored on purpose, so a fresh clone has none of them. docker-compose bind-mounts
# ./input and ./output; if they are missing Docker creates them itself, owned by root on Linux.
# Run this before the first `docker compose up` (setup.sh, serve.sh and setup_first_time.sh do it).
#
#   ./scripts/init_dirs.sh                  create whatever is missing
#   ./scripts/init_dirs.sh JD1 JD2 JD7      also pre-create prompts/JDn_PREP/{prom,resp} and prompts/JD_Analysis/JDn
#   ./scripts/init_dirs.sh --check          report only; exit 1 if anything is missing
#   ./scripts/init_dirs.sh --quiet          print one line only if something had to be created
#
# Safe to re-run: it only creates folders. It never deletes, moves or overwrites anything.

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CHECK=0
QUIET=0
JDS=""
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --quiet) QUIET=1 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    *)
      if ! [[ "$arg" =~ ^[A-Za-z0-9_-]+$ ]]; then
        echo "Not a valid JD name: $arg (letters, digits, _ and - only)" >&2
        exit 2
      fi
      JDS="$JDS $arg" ;;
  esac
done

# Everything the code reads or writes under these folders (found by reading the scripts):
#   input/    router.sh reads pdf, docx, html, image, other; ingest_evidence.sh reads evidence
#   output/   resume (variants), evidence (index), review_resume (finished resumes), _archive
#   chunks/   smart_chunk.sh        prompts/  every stage writes here, JD_Analysis from batch_prep.sh
DIRS="
input/pdf
input/docx
input/html
input/image
input/other
input/evidence
output/resume
output/evidence
output/review_resume
output/_archive
chunks
prompts/JD_Analysis"
for jd in $JDS; do
  DIRS="$DIRS
prompts/${jd}_PREP/prom
prompts/${jd}_PREP/resp
prompts/JD_Analysis/${jd}"
done

# API keys and settings live outside the repo (docker-compose mounts this folder).
CONFIG_DIR="${MARKITDOWN_CODESPACE_DIR:-$HOME/.markitdown-codespace}"
CONFIG_DIR="${CONFIG_DIR/#\~/$HOME}"

created=0
existed=0
missing=0
make_dir() {  # $1 = path, $2 = optional mode
  if [ -d "$1" ]; then
    existed=$((existed + 1))
    return 0
  fi
  if [ "$CHECK" = 1 ]; then
    missing=$((missing + 1))
    echo "  missing  $1"
    return 0
  fi
  mkdir -p "$1"
  if [ -n "$2" ]; then chmod "$2" "$1"; fi
  created=$((created + 1))
  if [ "$QUIET" != 1 ]; then echo "  created  $1"; fi
  return 0
}

for d in $DIRS; do make_dir "$d"; done
make_dir "$CONFIG_DIR" 700

if [ "$CHECK" = 1 ]; then
  if [ "$missing" -eq 0 ]; then
    if [ "$QUIET" != 1 ]; then echo "All $existed folders exist."; fi
    exit 0
  fi
  echo "$missing folder(s) missing. Run ./scripts/init_dirs.sh to create them."
  exit 1
fi

if [ "$QUIET" = 1 ]; then
  if [ "$created" -gt 0 ]; then echo "init_dirs: created $created folder(s)"; fi
  exit 0
fi

echo ""
echo "Folders: $created created, $existed already there."
cat <<'GUIDE'

Where things go:
  input/pdf, input/docx      your resume files (PDF or Word)
  input/other                job descriptions as text, for example JD1.txt
  input/evidence             your evidence files (number ledger, do-not-claim, approved claims)
  input/html, input/image    optional: saved web pages, screenshots of a resume
  output/resume              resume variants as Markdown (the router writes them here)
  output/evidence            written by ingest_evidence.sh (whole files plus the index)
  prompts, chunks            written by the stages themselves
GUIDE
if [ ! -f policy/evidence_policy.local.json ]; then
  echo ""
  echo "Optional: cp policy/evidence_policy.local.example.json policy/evidence_policy.local.json"
  echo "          and put your own employers and claims there (that file is git-ignored)."
fi
