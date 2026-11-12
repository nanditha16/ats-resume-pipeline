#!/bin/bash
#
# ui.sh — Simple terminal UI for non-developers.
#         Wraps the full batch_prep workflow with menus.
#
# Usage: ./scripts/ui.sh
#
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# ── Colours ───────────────────────────────────────────────────────────────────
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

clear_screen() { printf '\033[2J\033[H'; }
header() {
  clear_screen
  echo -e "${BOLD}${CYAN}"
  echo "  ╔════════════════════════════════════════════════════╗"
  echo "  ║      JOB APPLICATION PREP ASSISTANT               ║"
  echo "  ║      Nanditha's ATS + Evidence Pipeline            ║"
  echo "  ╚════════════════════════════════════════════════════╝${NC}"
  echo ""
}

pause() { echo ""; read -p "  Press Enter to continue..." _; }

# ── Check Docker running ──────────────────────────────────────────────────────
check_docker() {
  if ! docker exec markitdown echo "ok" &>/dev/null; then
    echo -e "${RED}  ❌ Docker container 'markitdown' is not running.${NC}"
    echo "  Start it with: docker-compose up -d"
    pause; return 1
  fi
  return 0
}

# ── Menu: Add JDs ─────────────────────────────────────────────────────────────
menu_add_jds() {
  header
  echo -e "  ${BOLD}📋 STEP 1 — Add Job Descriptions${NC}"
  echo ""
  echo "  Current JDs in input/other/:"
  echo ""
  JD_COUNT=0
  for f in input/other/JD*.txt; do
    [ -f "$f" ] && echo "    ✅ $(basename "$f")" && JD_COUNT=$((JD_COUNT+1))
  done
  [ "$JD_COUNT" -eq 0 ] && echo "    (none yet)"
  echo ""
  echo "  To add a new JD:"
  echo "    1. Create a file: input/other/JD<number>.txt"
  echo "    2. Paste the full job description text into it"
  echo "    3. Save and return here"
  echo ""
  echo "  Example: input/other/JD23.txt"
  echo ""
  echo "  How many JDs do you want to add now?"
  read -p "  Number (or 0 to skip): " N
  if [ "$N" -gt 0 ] 2>/dev/null; then
    for i in $(seq 1 "$N"); do
      # Find next available JD number
      NUM=1
      while [ -f "input/other/JD${NUM}.txt" ]; do NUM=$((NUM+1)); done
      echo ""
      echo "  Opening input/other/JD${NUM}.txt for editing..."
      echo "  Paste the JD text, then save and close."
      sleep 1
      # Try common editors
      if command -v nano &>/dev/null; then
        nano "input/other/JD${NUM}.txt"
      elif command -v vim &>/dev/null; then
        vim "input/other/JD${NUM}.txt"
      else
        echo "  No editor found. Create the file manually: input/other/JD${NUM}.txt"
        pause
      fi
    done
  fi
  pause
}

# ── Menu: Update evidence corpus ──────────────────────────────────────────────
menu_evidence() {
  header
  echo -e "  ${BOLD}📂 STEP 2 — Update Evidence Corpus${NC}"
  echo ""
  echo "  Evidence files in input/evidence/:"
  ls input/evidence/ 2>/dev/null | grep -v '^\.' | while read -r f; do echo "    $f"; done
  echo ""
  echo "  Current evidence files in output/evidence/:"
  COUNT=$(ls output/evidence/*.md 2>/dev/null | wc -l | tr -d ' ')
  echo "    ${COUNT} chunk(s) ready"
  echo ""
  echo "  Run evidence ingest? (Only needed when you add new evidence files)"
  read -p "  [y/N]: " yn
  if [[ "$yn" =~ ^[Yy] ]]; then
    check_docker || return
    echo ""
    ./scripts/ingest_evidence.sh
    pause
  fi
}

# ── Menu: Generate Stage 0/1 prompts ─────────────────────────────────────────
menu_gen_stage1() {
  header
  echo -e "  ${BOLD}🚀 STEP 3 — Generate Variant-Rank Prompts (Stage 0/1)${NC}"
  echo ""
  echo "  This generates the first prompt for each JD."
  echo "  You upload each prompt to Claude.ai to get variant ranking."
  echo ""
  JD_COUNT=$(find input/other -maxdepth 1 -name "JD*.txt" 2>/dev/null | wc -l | tr -d ' ')
  echo "  JDs found: $JD_COUNT"
  echo ""
  read -p "  Generate Stage 0/1 prompts? [y/N]: " yn
  [[ "$yn" =~ ^[Yy] ]] || { pause; return; }
  echo ""
  check_docker || return
  ./scripts/batch_prep.sh
  pause
}

# ── Menu: Continue (Stages 2-4) ───────────────────────────────────────────────
menu_continue() {
  header
  echo -e "  ${BOLD}⚡ STEP 4 — Generate ATS + Evidence Prompts (Stages 2-4)${NC}"
  echo ""
  echo "  This runs AFTER you have:"
  echo "    - Uploaded each 1_variant_rank_prompt.txt to Claude.ai"
  echo "    - Saved the response to prompts/JDx_PREP/resp/variant_rank_prompt_response.txt"
  echo ""
  echo "  Which JDs have responses ready?"
  echo ""
  READY=0
  for prep_dir in prompts/*_PREP; do
    [ -d "$prep_dir" ] || continue
    JD=$(basename "$prep_dir" _PREP)
    RESP="${prep_dir}/resp/variant_rank_prompt_response.txt"
    ALREADY="${prep_dir}/prom/4_ats_evidence_gap_prompt.txt"
    if [ -f "$RESP" ] && [ ! -f "$ALREADY" ]; then
      echo "    ⏳ $JD — response exists, stages 2-4 not yet generated"
      READY=$((READY+1))
    elif [ -f "$RESP" ] && [ -f "$ALREADY" ]; then
      echo "    ✅ $JD — all prompts ready"
    else
      echo "    ❌ $JD — waiting for response"
    fi
  done
  echo ""
  [ "$READY" -eq 0 ] && echo "  Nothing ready to continue. Paste responses first." && pause && return
  echo "  $READY JD(s) ready to generate Stages 2-4."
  read -p "  Continue? [y/N]: " yn
  [[ "$yn" =~ ^[Yy] ]] || { pause; return; }
  echo ""
  check_docker || return
  ./scripts/batch_prep.sh --continue
  pause
}

# ── Menu: Status dashboard ────────────────────────────────────────────────────
menu_status() {
  header
  echo -e "  ${BOLD}📊 STATUS DASHBOARD${NC}"
  echo ""
  ./scripts/batch_prep.sh --status
  pause
}

# ── Menu: Convert updated PDF ─────────────────────────────────────────────────
menu_convert_pdf() {
  header
  echo -e "  ${BOLD}📄 STEP 5 — Convert Updated Resume PDF${NC}"
  echo ""
  echo "  After editing your resume based on ATS recommendations,"
  echo "  convert the PDF to markdown for cover letter generation."
  echo ""
  echo "  PDF files in output/:"
  ls output/*.pdf 2>/dev/null | while read -r f; do echo "    $(basename "$f")"; done
  echo ""
  read -p "  Enter PDF filename (e.g. Nanditha_Murthy_Resume_JD7.pdf): " PDF_NAME
  [ -z "$PDF_NAME" ] && pause && return

  check_docker || return
  MD_NAME="${PDF_NAME%.pdf}.md"
  echo ""
  echo "  Converting $PDF_NAME → $MD_NAME ..."
  docker exec markitdown markitdown "/output/${PDF_NAME}" -o "/output/${MD_NAME}" && \
    echo -e "  ${GREEN}✅ Converted: output/${MD_NAME}${NC}" || \
    echo -e "  ${RED}❌ Conversion failed. Try pdfplumber instead.${NC}"
  pause
}

# ── Menu: Cover letter ────────────────────────────────────────────────────────
menu_cover_letter() {
  header
  echo -e "  ${BOLD}✉️  STEP 6 — Generate Cover Letter Prompt${NC}"
  echo ""
  echo "  Generate a cover letter prompt after updating your resume."
  echo ""
  echo "  Available JDx_PREP folders:"
  for prep_dir in prompts/*_PREP; do
    [ -d "$prep_dir" ] && echo "    $(basename "$prep_dir" _PREP)"
  done
  echo ""
  read -p "  Enter JD name (e.g. JD7): " JD_NAME
  [ -z "$JD_NAME" ] && pause && return
  check_docker || return
  ./scripts/batch_prep.sh --cover "$JD_NAME"
  pause
}

# ── Main menu loop ─────────────────────────────────────────────────────────────
while true; do
  header
  echo -e "  ${BOLD}MAIN MENU${NC}"
  echo ""
  echo "  ┌─────────────────────────────────────────────────────┐"
  echo "  │  1. Add Job Descriptions (input/other/JDx.txt)      │"
  echo "  │  2. Update Evidence Corpus (ingest_evidence.sh)      │"
  echo "  │  3. Generate Stage 0/1 Prompts (Variant Ranking)     │"
  echo "  │  4. Continue → Generate Stages 2-4 Prompts           │"
  echo "  │     (after pasting Stage 0/1 responses)              │"
  echo "  │  5. Status Dashboard                                  │"
  echo "  │  6. Convert Updated Resume PDF → MD                   │"
  echo "  │  7. Generate Cover Letter Prompt                      │"
  echo "  │  0. Exit                                              │"
  echo "  └─────────────────────────────────────────────────────┘"
  echo ""
  read -p "  Choose [0-7]: " CHOICE

  case "$CHOICE" in
    1) menu_add_jds ;;
    2) menu_evidence ;;
    3) menu_gen_stage1 ;;
    4) menu_continue ;;
    5) menu_status ;;
    6) menu_convert_pdf ;;
    7) menu_cover_letter ;;
    0) echo ""; echo "  Goodbye!"; echo ""; exit 0 ;;
    *) echo "  Invalid choice." ; sleep 1 ;;
  esac
done
