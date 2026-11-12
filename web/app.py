"""
web/app.py — Flask backend for the ATS Pipeline UI.

Runs INSIDE the Docker container (docker exec markitdown python3 /app/web/app.py).
The container mounts the project at /app so all relative paths resolve correctly.

Design constraints (unchanged from core pipeline):
  - NEVER calls an LLM. All LLM work goes through Claude.ai.
  - NEVER modifies pipeline logic. Calls existing shell scripts only.
  - Policy enforcement: reads policy/execution_policy.json. No hardcoded rules.
  - All state lives in the filesystem. No database.
  - Streaming output via SSE for real-time script progress.

SPLIT-PROMPT ARCHITECTURE (Stage 0/1):
  variant_bank.txt is generated once (or when variants change) by
  build_variant_bank.sh. Each JD run only generates jd_current.txt via
  variant_rank.sh. The UI exposes:
    /api/variant-bank-status  — whether bank exists + staleness info
    /api/build-variant-bank   — SSE stream of build_variant_bank.sh
    /api/generate-stage1      — SSE stream (now calls build check + variant_rank.sh)
    /api/download-bank        — download prompts/variant_bank.txt
    /api/download-jd-current  — download prompts/jd_current.txt
"""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from flask import (Flask, Response, jsonify, render_template, request,
                   send_file, stream_with_context)

# ── Project root is always /app when running inside Docker ────────────────────
ROOT = Path(os.environ.get("ATS_ROOT", "/app"))
if not ROOT.exists():
    ROOT = Path(__file__).parent.parent.resolve()

# Stage 10 + shared parser live in scripts/; make them importable here.
import sys as _sys
_sys.path.insert(0, str(ROOT / "scripts"))
import ats_parse        # noqa: E402  shared Stage 2/9 score parser (plan decision D7)
import callback_score   # noqa: E402  Stage 10 Callback Score
import jd_meta          # noqa: E402  JD company/title/posted-date/URL
import network_brief    # noqa: E402  Stage 10 brief + targets store
import evidence_index   # noqa: E402  evidence ledger / do-not-claim index

PROMPTS        = ROOT / "prompts"
INPUT_OTHER    = ROOT / "input" / "other"
INPUT_EVIDENCE = ROOT / "input" / "evidence"
OUTPUT_RESUME  = ROOT / "output" / "resume"
OUTPUT_REVIEW  = ROOT / "output" / "review_resume"
EVIDENCE_CHUNKS= ROOT / "output" / "evidence"   # whole files now (was output/career_wealth_chunk/ chunks)
POLICY_FILE    = ROOT / "policy" / "execution_policy.json"

# Split-prompt files
VARIANT_BANK_FILE = PROMPTS / "variant_bank.txt"
# JD text lives at output/JDx.md — no separate jd_current.txt needed

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_policy():
    with open(POLICY_FILE, encoding="utf-8") as f:
        return json.load(f)

def _find_response_file(directory: Path):
    for name in ["variant_rank_prompt_response.txt",
                 "1_variant_rank_prompt_response.txt"]:
        p = directory / name
        if p.exists():
            return p
    return None

def _has_prompt(prep_dir: Path, names):
    return any((prep_dir / "prom" / n).exists() for n in names)

_COST_RE = re.compile(r'Cost:\s*~?\$([\d.]+)')

def _extract_cost_header(resp_path: Path):
    """Read only the first few lines (API-generated responses prepend a
    '<!-- Cost: ~$X.XXXXX (...) -->' header) instead of loading the whole
    file, which can be large. Returns None for manually-pasted responses
    that have no such header — that's expected, not an error."""
    try:
        with resp_path.open(encoding="utf-8", errors="replace") as f:
            for _ in range(6):
                line = f.readline()
                if not line:
                    break
                m = _COST_RE.search(line)
                if m:
                    return float(m.group(1))
    except Exception:
        pass
    return None

def variant_bank_status() -> dict:
    """
    Return status of the two static files built in Step 0:
      prompts/variant_bank.txt          — all resume variants
      prompts/variant_rank_prompt.txt   — evaluation instructions
    Also returns whether any variant is newer than the bank (stale signal).
    """
    bank_exists   = VARIANT_BANK_FILE.exists()
    prompt_exists = (PROMPTS / "variant_rank_prompt.txt").exists()

    bank_mtime     = None
    bank_size      = None
    bank_stale     = False
    newest_variant = None

    if bank_exists:
        stat = VARIANT_BANK_FILE.stat()
        bank_mtime = stat.st_mtime
        bank_size  = stat.st_size

        if OUTPUT_RESUME.exists():
            for f in OUTPUT_RESUME.glob("*.md"):
                fmtime = f.stat().st_mtime
                if fmtime > bank_mtime:
                    bank_stale     = True
                    newest_variant = f.name
                    break

    resume_count = len(list(OUTPUT_RESUME.glob("*.md"))) if OUTPUT_RESUME.exists() else 0

    return {
        "bank_exists":    bank_exists,
        "bank_stale":     bank_stale,
        "bank_size":      bank_size,
        "newest_variant": newest_variant,
        "prompt_exists":  prompt_exists,
        "resume_count":   resume_count,
    }

def _extract_ats_subscores(text: str) -> dict:
    """Kept as a thin wrapper so existing callers don't change; the parsing
    itself lives in scripts/ats_parse.py (single source of truth, D7)."""
    return ats_parse.extract_subscores(text)


def jd_status(jd_name: str) -> dict:
    analysis_dir = PROMPTS / "JD_Analysis" / jd_name
    prep_dir     = PROMPTS / f"{jd_name}_PREP"
    no_dir       = PROMPTS / "JD_Analysis" / f"{jd_name}_NO"

    # prompt_exists = the JD's converted .md exists in output/ (pipeline already ran).
    # prepare_variant.sh moves every non-current JD's .md into output/_archive/
    # to keep smart_chunk.sh from mixing variants — check both locations.
    OUTPUT_JD_MD = ROOT / "output" / f"{jd_name}.md"
    ARCHIVE_JD_MD = ROOT / "output" / "_archive" / f"{jd_name}.md"
    prompt_exists = (
        OUTPUT_JD_MD.exists() or
        ARCHIVE_JD_MD.exists() or
        (analysis_dir / "variant_rank_prompt.txt").exists() or   # legacy monolithic
        (prep_dir / "prom" / "1_variant_rank_prompt.txt").exists()
    )
    resp_path = _find_response_file(analysis_dir)
    has_response = resp_path is not None

    # Extract Stage 0 verdict for UI display. Checked in priority order
    # (GOOD > PARTIAL > POOR) so a response that mentions "POOR FIT" only
    # in passing (e.g. describing one weak variant among many) doesn't
    # override an actual GOOD/PARTIAL overall verdict.
    fit_verdict = None
    if resp_path and resp_path.exists():
        resp_text = resp_path.read_text(errors="replace")
        if "GOOD FIT" in resp_text:
            fit_verdict = "GOOD FIT"
        elif "PARTIAL FIT" in resp_text:
            fit_verdict = "PARTIAL FIT"
        elif "POOR FIT" in resp_text:
            fit_verdict = "POOR FIT"

    poor_fit = no_dir.exists() or fit_verdict == "POOR FIT"

    # Stage 2 verdict (YES/MAYBE/NO), read once here so the UI can gray out
    # Stage 3/4/5/6 actions the same way it already grays out Stage 2-4 for
    # a Stage 0/1 POOR FIT — see claude_execute.py / batch_prep.sh for the
    # execution-side half of this same gate.
    ats_no_shortlist = False
    s2_score = None
    s2_subscores = None
    for base in [prep_dir / "resp"]:
        p = base / "ats_prompt_response.txt"
        if p.exists():
            s2_text = p.read_text(errors="replace")
            # Tolerant of "**Shortlist:**" and "**Shortlist: MAYBE -> YES**"
            # (whole phrase bolded) -- same fix already applied in
            # synthesize_resume.py's parse_stage2, which this had drifted
            # from by still using the old exact-bold-close pattern.
            verdict = ats_parse.extract_shortlist(s2_text)
            ats_no_shortlist = verdict == "NO"
            s2_score = ats_parse.extract_ats_score(s2_text)
            s2_subscores = _extract_ats_subscores(s2_text)
            break

    # Stage 9 -- the SAME Stage 2 evaluation re-run against the finalized,
    # Stage 5/6/7-edited resume, so the "before" score above (from the
    # original, pre-edit resume) has something to actually compare against.
    # Without this, the score shown on the Review Board is a stale snapshot
    # that never reflects anything Stage 5/6/7 changed.
    s2_score_after = None
    rescore_resp = prep_dir / "resp" / "ats_rescore_response.txt"
    if rescore_resp.exists():
        rescore_text = rescore_resp.read_text(errors="replace")
        s2_score_after = ats_parse.extract_ats_score(rescore_text)

    s2 = _has_prompt(prep_dir, ["2_ats_prompt.txt", "ats_prompt.txt"])
    s3 = _has_prompt(prep_dir, ["3_ats_recommend_prompt.txt", "ats_recommend_prompt.txt"])
    s4 = _has_prompt(prep_dir, ["4_ats_evidence_gap_prompt.txt", "ats_evidence_gap_prompt.txt"])
    s5 = _has_prompt(prep_dir, ["5_cover_letter_prompt.txt", "cover_letter_prompt.txt"])

    cv_file = analysis_dir / ".chosen_variant"
    if not cv_file.exists():
        cv_file = prep_dir / ".chosen_variant"
    chosen = cv_file.read_text().strip() if cv_file.exists() else None
    if not chosen:
        rfile = analysis_dir / "variant_rank_prompt_response.txt"
        if rfile.exists():
            txt = rfile.read_text(errors="replace")
            import re as _re
            m = _re.search(r'Nanditha_Murthy_Resume_[\w\-_ ]+', txt)
            if m:
                chosen = m.group(0).strip().rstrip('.')

    if poor_fit:                       stage = "poor_fit"
    elif not prompt_exists:            stage = "needs_stage1_prompt"
    elif not has_response:             stage = "waiting_response"
    elif not s2:                       stage = "needs_stages_2_4"
    elif ats_no_shortlist and not s3:  stage = "ats_no_shortlist"  # Stage 2 said NO — gated, matches poor_fit's role but one stage later
    elif s4:                           stage = "complete"
    else:                              stage = "needs_stages_2_4"

    # Shared static files (built once in Step 0)
    SHARED_PROMPT = PROMPTS / "variant_rank_prompt.txt"
    prompt_file   = str(SHARED_PROMPT.relative_to(ROOT)) if SHARED_PROMPT.exists() else None

    # JD text lives at output/JDx.md — produced by the pipeline, no copy needed
    OUTPUT_JD_MD  = ROOT / "output" / f"{jd_name}.md"
    ARCHIVE_JD_MD = ROOT / "output" / "_archive" / f"{jd_name}.md"
    if OUTPUT_JD_MD.exists():
        jd_file_path = str(OUTPUT_JD_MD.relative_to(ROOT))
    elif ARCHIVE_JD_MD.exists():
        jd_file_path = str(ARCHIVE_JD_MD.relative_to(ROOT))
    else:
        jd_file_path = None

    stage_prompts = {}
    for key, names in [
        ("s2", ["2_ats_prompt.txt", "ats_prompt.txt"]),
        ("s3", ["3_ats_recommend_prompt.txt", "ats_recommend_prompt.txt"]),
        ("s4", ["4_ats_evidence_gap_prompt.txt", "ats_evidence_gap_prompt.txt"]),
        ("s5", ["5_cover_letter_prompt.txt", "cover_letter_prompt.txt"]),
    ]:
        for n in names:
            p = prep_dir / "prom" / n
            if p.exists():
                stage_prompts[key] = str(p.relative_to(ROOT))
                break

    responses = {}
    costs = {}
    for key, names in [
        ("s1", ["variant_rank_prompt_response.txt", "1_variant_rank_prompt_response.txt"]),
        ("s2", ["ats_prompt_response.txt"]),
        ("s3", ["ats_recommend_prompt_response.txt"]),
        ("s4", ["ats_evidence_gap_response.txt"]),
        ("s8", ["cover_letter_response.txt"]),
        ("s9", ["ats_rescore_response.txt"]),
    ]:
        for n in names:
            for base in [analysis_dir, prep_dir / "resp"]:
                p = base / n
                if p.exists():
                    responses[key] = str(p.relative_to(ROOT))
                    costs[key] = _extract_cost_header(p)
                    break
            if key in responses:
                break

    # Stage 10 -- computed live (deterministic, file reads only). Never allowed
    # to break the status endpoint: any error degrades to "not_scored".
    callback = None
    if rescore_resp.exists():
        try:
            cb = callback_score.compute(jd_name)
            callback = {k: cb.get(k) for k in ("status", "callback_score", "threshold", "gated_in",
                                                "failed_knockouts", "unknown_knockouts", "reason")}
        except Exception as e:  # noqa: BLE001
            callback = {"status": "not_scored", "reason": f"callback error: {e}"}

    return {
        "name": jd_name,
        "stage": stage,
        "callback": callback,
        "prompt_exists": prompt_exists,
        "has_response": has_response,
        "poor_fit": poor_fit,
        "fit_verdict": fit_verdict,
        "ats_no_shortlist": ats_no_shortlist,
        "s2_score": s2_score,
        "s2_subscores": s2_subscores,
        "s2_score_after": s2_score_after,
        "s2": s2, "s3": s3, "s4": s4, "s5": s5,
        "chosen_variant": chosen,
        "prompt_file": prompt_file,
        "jd_file": jd_file_path,
        "stage_prompts": stage_prompts,
        "responses": responses,
        "costs": costs,
    }

def all_jds() -> list:
    names = set()
    for d in [PROMPTS / "JD_Analysis", PROMPTS]:
        if d.exists():
            for item in d.iterdir():
                m = re.match(r"^(JD\d+)(?:_PREP|_NO|_Applied)?$", item.name)
                if m:
                    names.add(m.group(1))
    if INPUT_OTHER.exists():
        for f in INPUT_OTHER.glob("JD*.txt"):
            names.add(f.stem)
    return sorted(names, key=lambda x: int(re.sub(r"\D", "", x) or "0"))

def system_health() -> dict:
    return {
        "docker": True,
        "resume_count": len(list(OUTPUT_RESUME.glob("*.md"))) if OUTPUT_RESUME.exists() else 0,
        "evidence_chunks": len(list(EVIDENCE_CHUNKS.glob("*.md"))) if EVIDENCE_CHUNKS.exists() else 0,
        "evidence": evidence_index.status(),
        "jd_inputs": len(list(INPUT_OTHER.glob("JD*.txt"))) if INPUT_OTHER.exists() else 0,
    }

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")

def stream_cmd(cmd: str):
    """Run shell command from /app, yield SSE lines with live stdout."""
    env = {**os.environ,
           "FORCE_COLOR": "0", "NO_COLOR": "1",
           "TERM": "dumb", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        ["bash", "-c", cmd], cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=env
    )
    for raw in proc.stdout:
        line = ANSI_RE.sub("", raw).rstrip()
        if line:
            yield f"data: {json.dumps(line)}\n\n"
    proc.wait()
    yield f"data: {json.dumps('__DONE__')}\n\n"
    yield f"data: {json.dumps({'exit_code': proc.returncode})}\n\n"

def _sse(cmd: str) -> Response:
    return Response(
        stream_with_context(stream_cmd(cmd)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

def _safe_path(rel: str):
    try:
        full = (ROOT / rel).resolve()
        full.relative_to(ROOT.resolve())
        return full
    except Exception:
        return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    policy = load_policy()
    return jsonify({
        "system": system_health(),
        "jds": [jd_status(j) for j in all_jds()],
        "stages": policy["stages"],
        "trust_levels": policy["trust_levels"],
        # Include variant bank status so the UI can show build prompt
        "variant_bank": variant_bank_status(),
    })

@app.route("/api/health")
def api_health():
    return jsonify(system_health())

# ── Split-prompt: variant bank endpoints ──────────────────────────────────────

@app.route("/api/variant-bank-status")
def api_variant_bank_status():
    """
    Returns whether variant_bank.txt exists, its size, and whether it is
    stale (any resume variant in output/resume/ is newer than the bank).
    Also returns whether jd_current.txt exists from the last variant_rank run.
    The UI uses this to decide whether to show 'Build Bank' vs 'Rebuild Bank'
    and to enable/disable the two download buttons.
    """
    return jsonify(variant_bank_status())

@app.route("/api/build-variant-bank")
def build_variant_bank():
    """
    SSE stream: run build_variant_bank.sh to (re)generate variant_bank.txt.
    Pass ?rebuild=1 to force rebuild even if bank is current.
    The UI calls this from the Stage 1 panel when the bank is missing or stale.
    """
    rebuild = request.args.get("rebuild", "0") == "1"
    flag    = "--rebuild" if rebuild else ""
    cmd     = f"./scripts/build_variant_bank.sh {flag} output/resume".strip()
    return _sse(cmd)

@app.route("/api/download-bank")
def download_bank():
    """Download the static variant bank prompt file."""
    if not VARIANT_BANK_FILE.exists():
        return "variant_bank.txt not found — run Build Variant Bank first", 404
    return send_file(str(VARIANT_BANK_FILE), as_attachment=True,
                     download_name="variant_bank.txt")

# ── Existing routes (unchanged except generate-stage1 note) ──────────────────

@app.route("/api/upload-jd", methods=["POST"])
def upload_jd():
    INPUT_OTHER.mkdir(parents=True, exist_ok=True)
    existing_nums = [
        int(re.sub(r"\D", "", f.stem) or "0")
        for f in INPUT_OTHER.glob("JD*.txt")
    ]
    next_num = max(existing_nums, default=0) + 1

    files = request.files.getlist("file")
    if files and files[0].filename:
        saved = []
        for uploaded in files:
            if not uploaded.filename:
                continue
            jd_name = f"JD{next_num}"
            (INPUT_OTHER / f"{jd_name}.txt").write_bytes(uploaded.read())
            saved.append(jd_name)
            next_num += 1
        return jsonify({"ok": True, "saved": saved})

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text provided"}), 400
    jd_name = f"JD{next_num}"
    (INPUT_OTHER / f"{jd_name}.txt").write_text(text, encoding="utf-8")
    return jsonify({"ok": True, "saved": [jd_name]})

@app.route("/api/paste-response", methods=["POST"])
def paste_response():
    data = request.get_json(silent=True) or {}
    jd_name = data.get("jd", "").strip()
    text    = data.get("text", "").strip()
    if not jd_name or not text:
        return jsonify({"ok": False, "error": "jd and text required"}), 400
    dest = PROMPTS / "JD_Analysis" / jd_name
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "variant_rank_prompt_response.txt").write_text(text, encoding="utf-8")
    return jsonify({"ok": True})

@app.route("/api/paste-stage-response", methods=["POST"])
def paste_stage_response():
    data  = request.get_json(silent=True) or {}
    jd    = data.get("jd", "").strip()
    stage = data.get("stage", "").strip()
    text  = data.get("text", "").strip()
    if not jd or not stage or not text:
        return jsonify({"ok": False, "error": "jd, stage, and text required"}), 400
    names = {
        "s2": "ats_prompt_response.txt",
        "s3": "ats_recommend_prompt_response.txt",
        "s4": "ats_evidence_gap_response.txt",
        "s5": "cover_letter_response.txt",
    }
    fname = names.get(stage)
    if not fname:
        return jsonify({"ok": False, "error": f"Unknown stage: {stage}"}), 400
    dest = PROMPTS / f"{jd}_PREP" / "resp"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / fname).write_text(text, encoding="utf-8")
    return jsonify({"ok": True})

@app.route("/api/run-pipeline")
def run_pipeline():
    cmd = (
        "echo '🚀 Running full document pipeline...' && "
        "echo '   router → extract → clean → chunk' && "
        "chmod +x scripts/*.sh && "
        "echo '✅ Environment ready (running inside Docker)' && "
        "./scripts/router.sh && "
        "echo '⏱️  router done' && "
        "echo '🧹 Cleaning output files...' && "
        """for f in output/*.md; do
  [ -f "$f" ] || continue
  echo "  ➡️ Cleaning $(basename $f)"
  perl -pi -e 's/&amp;/\\&/g; s/&lt;/</g; s/&gt;/>/g' "$f" 2>/dev/null || true
  perl -pi -e 's/[ \\t]+/ /g' "$f" 2>/dev/null || true
  awk 'NF{p=1} p' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done && """ +
        "echo '⏱️  clean done' && "
        "echo '📥 Routing PDF resumes to output/resume/...' && "
        """mkdir -p output/resume && if [ -s /tmp/pdf_manifest.txt ]; then
  while IFS= read -r name; do
    [ -f "output/$name.md" ] && mv "output/$name.md" "output/resume/$name.md" && echo "  📄 → output/resume/$name.md"
  done < /tmp/pdf_manifest.txt
fi && """ +
        "echo '🧠 Smart chunking...' && "
        "mkdir -p chunks && rm -f chunks/* && "
        """python3 -c "
import re, os
from pathlib import Path
heading = re.compile(r'^([A-Z][A-Z ]+)$|^#{1,6} ')
for src in sorted(Path('output').glob('*.md')):
    name = src.stem
    print(f'  Processing {src.name}')
    lines = src.read_text(errors='replace').splitlines(keepends=True)
    i, chunk = 1, []
    def flush(c, n, idx):
        if c:
            Path(f'chunks/{n}_part_{idx}.md').write_text(''.join(c))
    for line in lines:
        if heading.match(line.rstrip()) and chunk:
            flush(chunk, name, i); i += 1; chunk = []
        chunk.append(line)
    flush(chunk, name, i)
for p in Path('chunks').glob('*.md'):
    if p.stat().st_size == 0: p.unlink()
print('chunking complete')
" && """ +
        "echo '⏱️  chunk done' && "
        "echo '✅ Pipeline complete!'"
    )
    return _sse(cmd)

@app.route("/api/ingest-evidence")
def ingest_evidence():
    return _sse("INSIDE_DOCKER=1 ./scripts/ingest_evidence.sh 2>&1 || true")

@app.route("/api/generate-stage1")
def generate_stage1():
    """
    Verify Stage 0/1 readiness for one JD or all JDs.

    THREE-FILE SYSTEM — no file generation needed here:
      prompts/variant_bank.txt          — built once in Step 0
      prompts/variant_rank_prompt.txt   — built once in Step 0
      output/JDx.md                     — produced by pipeline (Step 0 run.sh)

    This route runs build_variant_bank.sh if the bank is missing/stale, then
    calls variant_rank.sh <JD_NAME> which only does a readiness check and
    prints the three file paths for the user.
    """
    jd      = request.args.get("jd", "").strip()
    rebuild = request.args.get("rebuild", "0") == "1"
    rebuild_flag = "--rebuild" if rebuild else ""

    if jd:
        cmd = (
            f"./scripts/build_variant_bank.sh {rebuild_flag} output/resume && "
            f"echo '' && "
            f"./scripts/variant_rank.sh '{jd}' output/resume"
        )
    else:
        # All JDs — POSIX-compatible: no shopt, no bash arrays
        cmd = (
            f"./scripts/build_variant_bank.sh {rebuild_flag} output/resume && "
            f"echo '' && "
            f"JD_COUNT=$(ls input/other/JD*.txt input/other/JD*.md 2>/dev/null | wc -l | tr -d ' ') && "
            f"if [ \"$JD_COUNT\" -eq 0 ]; then "
            f"  echo '❌ No JD source files in input/other/ — add JDs in Step 1 first'; exit 1; "
            f"fi && "
            f"echo \"📋 Checking $JD_COUNT JD(s)\" && "
            f"MISSING=0 && "
            f"for jd_input in input/other/JD*.txt input/other/JD*.md; do "
            f"  [ -f \"$jd_input\" ] || continue && "
            f"  JD_NAME=$(basename \"$jd_input\" .txt) && JD_NAME=$(basename \"$JD_NAME\" .md) && "
            f"  echo '' && echo \"── $JD_NAME ──\" && "
            f"  ./scripts/variant_rank.sh \"$JD_NAME\" output/resume || MISSING=$((MISSING+1)); "
            f"done && "
            f"echo '' && "
            f"if [ \"$MISSING\" -eq 0 ]; then "
            f"  echo '✅ All JDs ready — download files from each card below.'; "
            f"else "
            f"  echo \"⚠️  $MISSING JD(s) missing output .md — run the pipeline first.\"; "
            f"fi"
        )
    return _sse(cmd.strip())

@app.route("/api/generate-stages-2-4")
def generate_stages_2_4():
    jd    = request.args.get("jd", "").strip()
    force = request.args.get("force", "0") == "1"
    flags = "--continue" + (f" --jd {jd}" if jd else "")
    prefix = "FORCE=1 " if force else ""
    return _sse(f"{prefix}./scripts/batch_prep.sh {flags}")

@app.route("/api/generate-cover")
def generate_cover():
    jd = request.args.get("jd", "").strip()
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400
    return _sse(f"./scripts/batch_prep.sh --cover {jd}")

@app.route("/api/prompt-content")
def prompt_content():
    rel  = request.args.get("path", "")
    full = _safe_path(rel)
    if not full:
        return jsonify({"ok": False, "error": "invalid path"}), 403
    if not full.exists():
        return jsonify({"ok": False, "error": "file not found"}), 404
    return jsonify({"ok": True, "path": rel,
                    "content": full.read_text(errors="replace"),
                    "name": full.name})

@app.route("/api/download")
def download():
    rel  = request.args.get("path", "")
    full = _safe_path(rel)
    if not full:
        return "forbidden", 403
    if not full.exists():
        return "not found", 404
    return send_file(str(full), as_attachment=True, download_name=full.name)

@app.route("/api/evidence-status")
def evidence_status():
    src = [f.name for f in INPUT_EVIDENCE.iterdir()
           if INPUT_EVIDENCE.exists() and not f.name.startswith(".")] \
          if INPUT_EVIDENCE.exists() else []
    chunks = sorted([
        {"name": f.name, "size": f.stat().st_size}
        for f in EVIDENCE_CHUNKS.glob("*.md")
    ] if EVIDENCE_CHUNKS.exists() else [], key=lambda x: x["name"])
    return jsonify({
        "source_files": len(src),
        "source_list": src,
        "chunks": len(chunks),
        "chunk_list": chunks,
        "index": evidence_index.status(),
    })

@app.route("/api/resume-list")
def resume_list():
    resumes = sorted([f.stem for f in OUTPUT_RESUME.glob("*.md")]) \
              if OUTPUT_RESUME.exists() else []
    return jsonify({"resumes": resumes})

@app.route("/api/tier")
def api_tier():
    """Return current tier, stage access, active provider, and GCP project."""
    result = subprocess.run(
        ["python3", "/app/scripts/check_tier.py", "--json"],
        capture_output=True, text=True, cwd=str(ROOT)
    )
    try:
        data = json.loads(result.stdout)
    except Exception:
        data = {"tier": "free", "stages": {}}

    gcp_creds   = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    claude_key  = Path.home() / ".markitdown-codespace" / "claude_api_key"
    config_file = Path.home() / ".markitdown-codespace" / "config.json"
    cfg = {}
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text())
        except Exception:
            pass

    gcp_project = cfg.get("gcp_project") or os.environ.get("GCP_PROJECT", "")

    if gcp_creds.exists() and gcp_project:
        data["provider"]    = "vertex"
        data["gcp_project"] = gcp_project
        data["provider_label"] = f"Google Vertex AI ({gcp_project})"
    elif claude_key.exists() or os.environ.get("ANTHROPIC_API_KEY"):
        data["provider"]       = "claude"
        data["provider_label"] = "Anthropic Claude (claude-sonnet-4-5)"
    else:
        data["provider"]       = None
        data["provider_label"] = None

    return jsonify(data)

@app.route("/api/save-config", methods=["POST"])
def save_config():
    """Save BYOK config to ~/.markitdown-codespace/config.json"""
    data = request.get_json(silent=True) or {}
    config_dir  = Path.home() / ".markitdown-codespace"
    config_file = config_dir / "config.json"
    config_dir.mkdir(exist_ok=True)

    existing = {}
    if config_file.exists():
        try:
            existing = json.loads(config_file.read_text())
        except Exception:
            pass

    claude_key = data.get("anthropic_api_key", "").strip()
    if claude_key and claude_key.startswith("sk-ant-"):
        key_file = config_dir / "claude_api_key"
        key_file.write_text(claude_key)
        key_file.chmod(0o600)
        existing["anthropic_key_file"] = str(key_file)

    gcp_project = data.get("gcp_project", "").strip()
    if gcp_project:
        existing["gcp_project"] = gcp_project

    config_file.write_text(json.dumps(existing, indent=2))
    return jsonify({"ok": True, "tier": "pro" if (claude_key or gcp_project) else "free"})

@app.route("/api/run-stage2")
def run_stage2():
    """Stream Stage 2 automation via configured API backend (Vertex AI or Claude)."""
    jd       = request.args.get("jd", "").strip()
    force    = request.args.get("force", "0") == "1"
    provider = request.args.get("provider", "auto").strip()
    stages   = request.args.get("stages", "2").strip()

    config_file = Path.home() / ".markitdown-codespace" / "config.json"
    cfg = {}
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text())
        except Exception:
            pass

    claude_key_file = Path.home() / ".markitdown-codespace" / "claude_api_key"
    gcp_creds       = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    gcp_project = cfg.get("gcp_project") or os.environ.get("GCP_PROJECT", "")
    if not gcp_project and gcp_creds.exists():
        try:
            cred_data = json.loads(gcp_creds.read_text())
            gcp_project = cred_data.get("quota_project_id", "")
        except Exception:
            pass
    claude_env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_key     = claude_key_file.read_text().strip() if claude_key_file.exists() else claude_env_key

    force_flag  = "--force" if force else ""
    jd_flag     = ("--jd " + jd) if jd else "--all"
    stages_flag = "--stages " + stages

    use_vertex = (provider == "vertex") or (provider == "auto" and gcp_creds.exists() and gcp_project)
    use_claude = (provider == "claude") or (provider == "auto" and not use_vertex and claude_key)

    if use_vertex and gcp_project:
        cmd = "GCP_PROJECT=" + gcp_project + " python3 /app/scripts/vertex_execute.py " + jd_flag + " " + stages_flag + " " + force_flag
    elif use_claude and claude_key:
        cmd = "ANTHROPIC_API_KEY=" + claude_key + " python3 /app/scripts/claude_execute.py " + jd_flag + " " + stages_flag + " " + force_flag
    else:
        def _no_key():
            msg = "ERROR: No API key configured."
            if provider == "vertex":
                msg += " Vertex AI requires gcloud auth + GCP project in Settings."
            elif provider == "claude":
                msg += " Claude requires an API key in Settings."
            else:
                msg += " Go to Settings to add a Vertex AI project or Claude API key."
            yield "data: " + json.dumps(msg) + "\n\n"
            yield "data: " + json.dumps("__DONE__") + "\n\n"
            yield "data: " + json.dumps({"exit_code": 1}) + "\n\n"
        return Response(
            stream_with_context(_no_key()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return _sse(cmd.strip())


# ── Stage 5/6 — synthesize + human review/merge ─────────────────────────────
# Same design constraint as the rest of this file: no pipeline logic lives
# here. Every route below shells out to scripts/synthesize_resume.py (which
# makes zero LLM calls — see that script's own module docstring) and either
# streams its stdout (generate) or parses its JSON stdout (manifest/apply).
# The manifest and apply-by-id contract is documented in that script's
# argparse help; this file must not reimplement the accept/reject or
# insertion-placement logic independently of it.

@app.route("/review/<jd>")
def review_page(jd):
    """Stage 6 diff-review page: approve/reject each Stage 3/4 candidate
    change, then merge only the approved ones into the final resume."""
    return render_template("review.html", jd=jd)


@app.route("/api/generate-stage5")
def generate_stage5():
    """Stage 5: run the default (hands-off) synthesis policy. Kept available
    for anyone who wants the old zero-click behavior; the review page below
    calls --manifest-only / --apply-ids directly instead of this route."""
    jd = request.args.get("jd", "").strip()
    force = request.args.get("force", "0") == "1"
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400
    force_flag = " --force" if force else ""
    return _sse(f"python3 /app/scripts/synthesize_resume.py {jd}{force_flag}")


@app.route("/api/stage6-manifest")
def stage6_manifest():
    """Return the base resume text + every candidate change (paraphrase or
    evidence-backed insertion) for the diff-review UI, with a server-computed
    word-level diff per change so the frontend never has to reimplement
    diffing. If Stage 2 said NO, `gated` is true and `changes` is empty --
    the UI shows the reason instead of a review list."""
    jd = request.args.get("jd", "").strip()
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400

    result = subprocess.run(
        ["python3", "/app/scripts/synthesize_resume.py", jd, "--manifest-only"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    try:
        data = json.loads(result.stdout)
    except Exception:
        return jsonify({"ok": False, "error": f"could not parse synthesize_resume.py output: {result.stdout or result.stderr}"}), 500

    if not data.get("ok"):
        return jsonify(data), 404

    for change in data.get("changes", []):
        change["diff_html"] = _word_diff_html(change.get("before", ""), change.get("after", ""))

    variant_file = OUTPUT_RESUME / f"{data['variant']}.md" if data.get("variant") else None
    data["base_resume_available"] = bool(variant_file and variant_file.exists())
    return jsonify(data)


@app.route("/api/stage6-apply", methods=["POST"])
def stage6_apply():
    """Write output/review_resume/{JD}_new_resume.md using EXACTLY the
    change ids the user approved in the review page -- nothing more,
    nothing less."""
    data = request.get_json(silent=True) or {}
    jd = (data.get("jd") or "").strip()
    accepted_ids = data.get("accepted_ids") or []
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400
    if not isinstance(accepted_ids, list):
        return jsonify({"ok": False, "error": "accepted_ids must be a list"}), 400

    ids_arg = ",".join(str(i) for i in accepted_ids)
    result = subprocess.run(
        ["python3", "/app/scripts/synthesize_resume.py", jd, "--force", "--apply-ids", ids_arg],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    ok = result.returncode == 0
    return jsonify({
        "ok": ok,
        "log": result.stdout if ok else (result.stderr or result.stdout),
        "resume_path": f"output/review_resume/{jd}_new_resume.md" if ok else None,
    }), (200 if ok else 400)


@app.route("/api/generate-resume-pdf")
def generate_resume_pdf():
    """Convert an already-merged output/review_resume/{JD}_new_resume.md
    into a PDF via scripts/md_to_pdf.py (deterministic, reportlab, zero
    LLM calls -- see that script's own module docstring). This is a
    separate, later action from Stage 6 apply on purpose: the Review
    Board only enables it once the person has explicitly checked "I've
    manually reviewed this" for that JD -- the checkbox state lives in
    the frontend, but the file-existence check here is the real gate
    against converting a resume nobody has actually looked at yet."""
    jd = request.args.get("jd", "").strip()
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400

    md_path = OUTPUT_REVIEW / f"{jd}_new_resume.md"
    if not md_path.exists():
        return jsonify({
            "ok": False,
            "error": f"No merged resume found at output/review_resume/{jd}_new_resume.md yet — "
                     f"use Review & merge first, then come back to convert it.",
        }), 404

    pdf_path = OUTPUT_REVIEW / f"{jd}_new_resume.pdf"
    result = subprocess.run(
        ["python3", "/app/scripts/md_to_pdf.py", str(md_path), str(pdf_path)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    ok = result.returncode == 0
    return jsonify({
        "ok": ok,
        "log": (result.stdout + result.stderr).strip() if ok else (result.stderr or result.stdout),
        "pdf_path": f"output/review_resume/{jd}_new_resume.pdf" if ok else None,
    }), (200 if ok else 400)


# ── Stage 7 — trim over-long roles against the real JD text ─────────────────
# Same shape as Stage 6: a script computes candidates as data, a page lets a
# human pick which ones to actually apply, nothing is deleted without an
# explicit approved id list. See scripts/trim_review.py's own docstring for
# why this needs no LLM call despite sounding like a judgment task.

@app.route("/trim/<jd>")
def trim_page(jd):
    return render_template("trim.html", jd=jd)


@app.route("/api/trim-manifest")
def trim_manifest():
    jd = request.args.get("jd", "").strip()
    cap = request.args.get("cap", "5").strip()
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400

    result = subprocess.run(
        ["python3", "/app/scripts/trim_review.py", jd, "--cap", cap, "--manifest-only"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    try:
        data = json.loads(result.stdout)
    except Exception:
        return jsonify({"ok": False, "error": f"could not parse trim_review.py output: {result.stdout or result.stderr}"}), 500
    return jsonify(data), (200 if data.get("ok") else 404)


@app.route("/api/trim-apply", methods=["POST"])
def trim_apply():
    data = request.get_json(silent=True) or {}
    jd = (data.get("jd") or "").strip()
    accepted_ids = data.get("accepted_ids") or []
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400
    if not isinstance(accepted_ids, list):
        return jsonify({"ok": False, "error": "accepted_ids must be a list"}), 400

    if not accepted_ids:
        return jsonify({"ok": True, "log": "No bullets selected — nothing deleted.", "deleted": 0})

    ids_arg = ",".join(str(i) for i in accepted_ids)
    result = subprocess.run(
        ["python3", "/app/scripts/trim_review.py", jd, "--apply-ids", ids_arg],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    ok = result.returncode == 0
    return jsonify({
        "ok": ok,
        "log": result.stdout if ok else (result.stderr or result.stdout),
        "deleted": ids_arg.count(",") + 1 if ok else 0,
    }), (200 if ok else 400)


# ── Cover letter (Stage 8) ───────────────────────────────────────────────────
# Generation is deterministic prompt-building (no LLM, instant) -- exactly
# like every prompt-building step elsewhere in this app. Execution calls the
# Claude API and is explicitly override-gated: policy/execution_policy.json's
# own "cover_letter" entry still reads execution_policy: "untested" and
# local_execution_allowed: false. --ignore-gate here is not this app
# bypassing its own policy -- it's the same per-run user decision Stage 3/4
# already require, with the button click itself serving as the explicit
# confirmation step (mirrors the non-interactive-invocation convention
# already used for Stage 2-4's web-triggered API calls).

@app.route("/api/generate-cover-letter-prompt")
def generate_cover_letter_prompt():
    jd = request.args.get("jd", "").strip()
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400
    result = subprocess.run(
        ["bash", "/app/scripts/cover_letter.sh", jd],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    ok = result.returncode == 0
    return jsonify({
        "ok": ok,
        "log": result.stdout if ok else (result.stderr or result.stdout),
    }), (200 if ok else 400)


@app.route("/api/generate-cover-letter")
def generate_cover_letter():
    """Build the prompt (free, instant), call the Claude API for the actual
    letter (--ignore-gate — the button click is the user's explicit
    override decision), then convert to PDF via md_to_pdf.py's generic
    path. Plain JSON, not SSE, to match this page's other action buttons
    (Convert to PDF, Delete trims) rather than introduce a live-log pattern
    this page doesn't otherwise have — the tradeoff is no incremental
    progress during the ~20-90s API call, only a final result."""
    jd = request.args.get("jd", "").strip()
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400

    strip_header = (
        f"python3 -c \""
        f"import re; "
        f"t = open('prompts/{jd}_PREP/resp/cover_letter_response.txt').read(); "
        f"t = re.sub(r'^(<!--.*-->\\n)+\\n*', '', t); "
        f"open('output/review_resume/{jd}_cover_letter.md', 'w').write(t)\""
    )
    cmd = (
        f"bash /app/scripts/cover_letter.sh {jd} && "
        f"python3 /app/scripts/claude_execute.py --jd {jd} --stages cover --ignore-gate --force && "
        f"{strip_header} && "
        f"python3 /app/scripts/md_to_pdf.py "
        f"output/review_resume/{jd}_cover_letter.md "
        f"output/review_resume/{jd}_cover_letter.pdf"
    )
    try:
        result = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, cwd=str(ROOT), timeout=180,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Timed out after 180s waiting on the Claude API call."}), 504

    ok = result.returncode == 0
    return jsonify({
        "ok": ok,
        "log": (result.stdout + result.stderr).strip() if ok else (result.stderr or result.stdout),
        "md_path": f"output/review_resume/{jd}_cover_letter.md" if ok else None,
        "pdf_path": f"output/review_resume/{jd}_cover_letter.pdf" if ok else None,
    }), (200 if ok else 400)


# ── Final ATS re-score (Stage 9) ─────────────────────────────────────────────
# Re-runs the exact Stage 2 evaluation against the Stage 5/6/7-finalized
# resume, so "Stage 2: 72/100" on the Review Board isn't a permanently
# stale snapshot from before any editing happened. Same policy/trust as
# Stage 2 itself (advisory, local_allowed) -- see rescore_resume.sh's own
# docstring for why this isn't a new task shape needing new evidence.

@app.route("/api/rescore-resume")
def rescore_resume():
    """One call, two steps: build the prompt (retrieval + templating, no
    LLM, same as any other prompt-building call in this app), then call
    the Claude API for the actual score. Plain JSON, matching this page's
    other action buttons."""
    jd = request.args.get("jd", "").strip()
    if not jd:
        return jsonify({"ok": False, "error": "jd required"}), 400

    cmd = (
        f"bash /app/scripts/rescore_resume.sh {jd} && "
        f"python3 /app/scripts/claude_execute.py --jd {jd} --stages rescore --force"
    )
    try:
        result = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, cwd=str(ROOT), timeout=180,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Timed out after 180s waiting on the Claude API call."}), 504

    ok = result.returncode == 0
    score_after = None
    if ok:
        resp_path = PROMPTS / f"{jd}_PREP" / "resp" / "ats_rescore_response.txt"
        if resp_path.exists():
            score_after = ats_parse.extract_ats_score(resp_path.read_text(errors="replace"))

    return jsonify({
        "ok": ok,
        "log": (result.stdout + result.stderr).strip() if ok else (result.stderr or result.stdout),
        "score_after": score_after,
    }), (200 if ok else 400)


# ── Stage 10: Callback Score + outreach targets ──────────────────────────────
# Deterministic and $0: no model calls, no LinkedIn access (plan D1, D6).
# The brief holds search links the user opens in their own browser session.
_JD_RE = re.compile(r"^JD\d{1,4}$")


def _bad_jd(jd):
    return None if jd and _JD_RE.match(jd) else (jsonify({"ok": False, "error": f"invalid JD name: {jd!r}"}), 400)


@app.route("/network/<jd>")
def network_page(jd):
    if not _JD_RE.match(jd):
        return "invalid JD name", 400
    return render_template("network.html", jd=jd)


@app.route("/api/network/<jd>")
def network_state(jd):
    bad = _bad_jd(jd)
    if bad:
        return bad
    score = callback_score.compute(jd)
    brief_file = network_brief.brief_path(jd)
    return jsonify({
        "ok": True,
        "score": score,
        "targets": network_brief.load_targets(jd),
        "brief": brief_file.read_text(errors="replace") if brief_file.exists() else None,
        "statuses": network_brief.TARGET_STATUSES,
        "outcomes": network_brief.OUTCOMES,
    })


@app.route("/api/network-brief", methods=["POST"])
def network_generate():
    body = request.get_json(silent=True) or {}
    jd = body.get("jd")
    bad = _bad_jd(jd)
    if bad:
        return bad
    r = network_brief.generate(jd, force=bool(body.get("force")))
    return jsonify(r), (200 if r.get("ok") else 400)


@app.route("/api/targets/<jd>", methods=["POST"])
def network_save_targets(jd):
    bad = _bad_jd(jd)
    if bad:
        return bad
    r = network_brief.save_targets(jd, request.get_json(silent=True) or {})
    return jsonify(r), (200 if r.get("ok") else 400)


@app.route("/api/scout-context/<jd>")
def scout_context(jd):
    """Read by the linkedin-target-scout skill (Claude in Chrome) at the start
    of a run: company, title, job ID, locations, gate status, target rules and
    the output contract it must return. Read-only."""
    bad = _bad_jd(jd)
    if bad:
        return bad
    return jsonify(network_brief.scout_context(jd))


@app.route("/api/targets/<jd>/import", methods=["POST"])
def network_import_targets(jd):
    """Import the skill's results: JSON contract, or its text format, as
    {"text": "..."} or the JSON object itself."""
    bad = _bad_jd(jd)
    if bad:
        return bad
    body = request.get_json(silent=True)
    if body is None:
        body = {"text": request.get_data(as_text=True)}
    r = network_brief.import_scout(jd, body)
    return jsonify(r), (200 if r.get("ok") else 400)


@app.route("/api/jd-meta/<jd>", methods=["POST"])
def network_save_meta(jd):
    """Fill what could not be derived from input/other (company for Oracle/ADP/
    SAP/Dayforce/LinkedIn links, posting date for most JDs) or override a
    knockout the regexes got wrong. Stored in prompts/JD_Analysis/JDx/.jd_meta.json."""
    bad = _bad_jd(jd)
    if bad:
        return bad
    body = request.get_json(silent=True) or {}
    ko = body.get("knockout_overrides")
    if ko is not None and (not isinstance(ko, dict) or
                           any(v not in ("PASS", "FAIL", "UNKNOWN", "NA", "") for v in ko.values())):
        return jsonify({"ok": False, "error": "knockout_overrides values must be PASS/FAIL/UNKNOWN/NA"}), 400
    if ko is not None:
        body["knockout_overrides"] = {k: v for k, v in ko.items() if v}
    pd = body.get("posted_date")
    if pd:
        try:
            __import__("datetime").date.fromisoformat(pd)
        except ValueError:
            return jsonify({"ok": False, "error": "posted_date must be YYYY-MM-DD"}), 400
    jd_meta.save_override(jd, body)
    return jsonify({"ok": True, "score": callback_score.compute(jd)})


def _word_diff_html(before: str, after: str) -> dict:
    """Server-side word-level diff (Python stdlib only -- no client-side diff
    library dependency) rendered as two HTML strings with <del>/<ins> spans,
    for a diffchecker-style before/after view."""
    import difflib
    import html as _html
    before_words = before.split(" ")
    after_words = after.split(" ")
    sm = difflib.SequenceMatcher(None, before_words, after_words)
    before_html, after_html = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        b_chunk = _html.escape(" ".join(before_words[i1:i2]))
        a_chunk = _html.escape(" ".join(after_words[j1:j2]))
        if tag == "equal":
            before_html.append(b_chunk)
            after_html.append(a_chunk)
        elif tag == "delete":
            before_html.append(f"<del>{b_chunk}</del>")
        elif tag == "insert":
            after_html.append(f"<ins>{a_chunk}</ins>")
        elif tag == "replace":
            before_html.append(f"<del>{b_chunk}</del>")
            after_html.append(f"<ins>{a_chunk}</ins>")
    return {"before": " ".join(before_html), "after": " ".join(after_html)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    print(f"\n  ATS Pipeline UI — http://localhost:{args.port}")
    print(f"  Root: {ROOT}\n")
    app.run(host=args.host, port=args.port, debug=False)
