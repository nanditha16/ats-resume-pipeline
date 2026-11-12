#!/usr/bin/env python3
"""
scripts/api_execute.py — Run pipeline prompt files through the Gemini API.

Replaces the manual Claude.ai upload/paste loop for Stages 2, 3, and 4.
Stage 0/1 (variant_rank) stays manual — local models failed it and it is
the go/no-go gate that should remain human-reviewed.

Model: gemini-1.5-flash (free tier)
  - 15 requests/min, 1,500 requests/day
  - 1M token context window (handles Stage 4's ~78K token prompt)
  - Free at aistudio.google.com

Setup (one time):
  1. Get API key from aistudio.google.com → "Get API key"
  2. Store it:
       echo 'YOUR_KEY_HERE' > ~/.markitdown-codespace/gemini_api_key
       chmod 600 ~/.markitdown-codespace/gemini_api_key
  3. Run:
       python3 scripts/api_execute.py --jd JD2
       python3 scripts/api_execute.py --all
       python3 scripts/api_execute.py --jd JD2 --dry-run

Scope: Stage 2 only (ATS evaluation).
Stages 3 and 4 remain manual — upload to Claude.ai.

Output: prompts/JDx_PREP/resp/ats_prompt_response.txt

Trust level: ADVISORY (same as local model output).
Gemini Flash has not been tested against this pipeline's specific failure
modes. Spot-check Stage 2 Critical Gaps section — this is where local
models fabricated content. Stage 3/4 citation accuracy requires human review.

Policy note: execution_policy.json marks Stage 3 and Stage 4 as manual_only
because local 8B models failed them. Gemini 1.5 Flash is a significantly
stronger model (comparable to Claude Sonnet tier). This script treats it as
advisory — review before acting on any response.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.resolve()
PROMPTS = ROOT / "prompts"

# ── Gemini API ────────────────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# ── Stage definitions ─────────────────────────────────────────────────────────
# Stage 2 only — ATS evaluation.
# Stages 3 and 4 remain manual (upload to Claude.ai) until further testing.
STAGES = {
    "2": {
        "prompt_names": ["2_ats_prompt.txt", "ats_prompt.txt"],
        "response_name": "ats_prompt_response.txt",
        "label": "Stage 2 — ATS evaluation",
        "trust": "advisory",
        "policy": "local_allowed",
    },
}


# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[0;32m"
YELLOW = "\033[1;33m"
RED    = "\033[0;31m"
CYAN   = "\033[0;36m"
BOLD   = "\033[1m"
NC     = "\033[0m"

def ok(msg):   print(f"{GREEN}✅ {msg}{NC}")
def warn(msg): print(f"{YELLOW}⚠️  {msg}{NC}")
def err(msg):  print(f"{RED}❌ {msg}{NC}")
def info(msg): print(f"{CYAN}   {msg}{NC}")
def hdr(msg):  print(f"{BOLD}{CYAN}{msg}{NC}")


# ── API key ───────────────────────────────────────────────────────────────────
def load_api_key() -> str:
    """
    Load Gemini API key from (in priority order):
      1. GEMINI_API_KEY environment variable
      2. ~/.markitdown-codespace/gemini_api_key file
    """
    # Env var
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    # Key file
    key_file = Path.home() / ".markitdown-codespace" / "gemini_api_key"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key

    err("Gemini API key not found.")
    print()
    print("  Set it up with one of:")
    print("    export GEMINI_API_KEY='AIzaSy...'")
    print("    OR")
    print("    mkdir -p ~/.markitdown-codespace")
    print("    echo 'AIzaSy...' > ~/.markitdown-codespace/gemini_api_key")
    print("    chmod 600 ~/.markitdown-codespace/gemini_api_key")
    print()
    print("  Get your key: https://aistudio.google.com → Get API key")
    sys.exit(1)


# ── Gemini API call ───────────────────────────────────────────────────────────
def call_gemini(api_key: str, prompt_text: str, stage_label: str) -> str:
    """
    Send prompt to Gemini 1.5 Flash, return response text.
    Handles rate limiting with automatic retry.
    """
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": 0.2,      # low temp for analytical tasks
            "maxOutputTokens": 8192,
            "topP": 0.8,
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
    }

    url = f"{GEMINI_URL}?key={api_key}"
    data = json.dumps(payload).encode("utf-8")

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            # Extract text from response
            candidates = result.get("candidates", [])
            if not candidates:
                raise ValueError("No candidates in response")

            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            if not text.strip():
                raise ValueError("Empty response text")

            return text

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                if attempt == 0:
                    err("Rate limited (HTTP 429).")
                    err("Your AI Studio key hit its free quota limit.")
                    err("Use your Google Cloud key instead:")
                    err("  1. Go to console.cloud.google.com → select your project")
                    err("  2. APIs & Services → Credentials → Create Credentials → API Key")
                    err("  3. Also enable: APIs & Services → Enable APIs → search Gemini API → Enable")
                    err("  4. echo 'YOUR_CLOUD_KEY' > ~/.markitdown-codespace/gemini_api_key")
                    raise RuntimeError("Rate limited — switch to Google Cloud API key")
                wait = 30 * (attempt + 1)
                warn(f"Rate limited. Waiting {wait}s before retry {attempt + 1}/3...")
                time.sleep(wait)
                continue
            elif e.code == 400:
                err(f"Bad request for {stage_label}: {body[:300]}")
                raise
            else:
                err(f"HTTP {e.code} for {stage_label}: {body[:300]}")
                if attempt < 2:
                    time.sleep(10)
                    continue
                raise

        except Exception as e:
            err(f"API error for {stage_label} (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(10)
                continue
            raise

    raise RuntimeError(f"All retries failed for {stage_label}")


# ── Resolve folders ───────────────────────────────────────────────────────────
def resolve_prep_dir(jd_name: str) -> Path:
    """Return _READY folder if it exists, else _PREP."""
    ready = PROMPTS / f"{jd_name}_READY"
    if ready.exists():
        return ready
    return PROMPTS / f"{jd_name}_PREP"


def find_prompt_file(prep_dir: Path, stage_key: str) -> Path | None:
    """Find the prompt file for a stage, accepting old and new naming."""
    for name in STAGES[stage_key]["prompt_names"]:
        p = prep_dir / "prom" / name
        if p.exists():
            return p
    return None


def response_exists(prep_dir: Path, stage_key: str) -> bool:
    resp = prep_dir / "resp" / STAGES[stage_key]["response_name"]
    return resp.exists()


# ── JD collection ─────────────────────────────────────────────────────────────
def all_jds_with_prompts(stages: list[str]) -> list[str]:
    """Return JD names that have all requested stage prompts ready."""
    jds = []
    for d in sorted(PROMPTS.iterdir()):
        m = re.match(r"^(JD\d+)(?:_PREP|_READY)$", d.name)
        if not m or not d.is_dir():
            continue
        jd_name = m.group(1)
        prep_dir = resolve_prep_dir(jd_name)
        if all(find_prompt_file(prep_dir, s) for s in stages):
            jds.append(jd_name)
    return sorted(set(jds), key=lambda x: int(re.sub(r"\D", "", x) or "0"))


# ── Execute one JD + stage ────────────────────────────────────────────────────
def run_stage(
    jd_name: str,
    stage_key: str,
    api_key: str,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """
    Run one stage for one JD. Returns True on success.
    """
    prep_dir = resolve_prep_dir(jd_name)
    stage = STAGES[stage_key]

    prompt_file = find_prompt_file(prep_dir, stage_key)
    if not prompt_file:
        warn(f"  {jd_name} Stage {stage_key}: prompt file not found — skipping")
        return False

    resp_file = prep_dir / "resp" / stage["response_name"]

    if resp_file.exists() and not force:
        ok(f"  {jd_name} Stage {stage_key}: response already exists (use --force to rerun)")
        return True

    # Estimate token count (rough: 1 token ≈ 4 chars)
    prompt_text = prompt_file.read_text(encoding="utf-8", errors="replace")
    est_tokens = len(prompt_text) // 4
    info(f"  {jd_name} {stage['label']}")
    info(f"    Prompt: {prompt_file.name} (~{est_tokens:,} tokens)")
    info(f"    Trust:  {stage['trust'].upper()} — review before acting on output")

    if dry_run:
        info(f"    [DRY RUN] Would write → {resp_file.relative_to(ROOT)}")
        return True

    info(f"    Calling Gemini {GEMINI_MODEL}...")
    start = time.time()

    try:
        response_text = call_gemini(api_key, prompt_text, stage["label"])
    except Exception as e:
        err(f"  {jd_name} Stage {stage_key} failed: {e}")
        return False

    elapsed = int(time.time() - start)

    # Write response
    resp_file.parent.mkdir(parents=True, exist_ok=True)

    # Prepend advisory header so the file is self-documenting
    header = (
        f"<!-- ADVISORY: Generated by Gemini {GEMINI_MODEL} via api_execute.py -->\n"
        f"<!-- Review before acting on this output, especially Critical Gaps and citations. -->\n"
        f"<!-- Stage: {stage['label']} | JD: {jd_name} | Time: {elapsed}s -->\n\n"
    )
    resp_file.write_text(header + response_text, encoding="utf-8")

    ok(f"  {jd_name} Stage {stage_key} done ({elapsed}s) → {resp_file.relative_to(ROOT)}")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Run ATS pipeline stages through Gemini 1.5 Flash API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/api_execute.py --jd JD2              # all stages (2,3,4)
  python3 scripts/api_execute.py --jd JD2 --stages 2   # stage 2 only
  python3 scripts/api_execute.py --all                  # all JDs with prompts
  python3 scripts/api_execute.py --all --stages 2,3     # all JDs, stages 2+3
  python3 scripts/api_execute.py --jd JD2 --dry-run    # preview without calling API
  python3 scripts/api_execute.py --jd JD2 --force      # rerun even if response exists

Setup:
  echo 'AIzaSy...' > ~/.markitdown-codespace/gemini_api_key
  Get key: https://aistudio.google.com → Get API key
        """,
    )
    parser.add_argument("--jd", help="Single JD name (e.g. JD2)")
    parser.add_argument("--all", action="store_true", help="Run all JDs with prompts ready")
    parser.add_argument(
        "--stages",
        default="2",
        help="Comma-separated stage numbers to run (default: 2,3,4)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without calling the API")
    parser.add_argument("--force", action="store_true", help="Re-run even if response already exists")
    parser.add_argument("--list", action="store_true", help="List JDs that have prompts ready and exit")

    args = parser.parse_args()

    # Parse stages
    try:
        stages = [s.strip() for s in args.stages.split(",")]
        for s in stages:
            if s not in STAGES:
                err(f"Unknown stage: {s}. Valid: {', '.join(STAGES.keys())}")
                sys.exit(1)
    except Exception:
        err(f"Invalid --stages value: {args.stages}")
        sys.exit(1)

    # Collect JDs
    if args.jd:
        jd_list = [args.jd]
    elif args.all:
        jd_list = all_jds_with_prompts(stages)
        if not jd_list:
            warn("No JDs found with all requested stage prompts ready.")
            warn("Run ./scripts/batch_prep.sh --continue first.")
            sys.exit(0)
    else:
        parser.print_help()
        print()
        err("Specify --jd JDx or --all")
        sys.exit(1)

    # List mode
    if args.list:
        hdr(f"\nJDs with stages {', '.join(stages)} prompts ready:")
        for jd in all_jds_with_prompts(stages):
            prep = resolve_prep_dir(jd)
            missing_resp = [
                s for s in stages if not response_exists(prep, s)
            ]
            status = "needs response" if missing_resp else "responses exist"
            print(f"  {jd:<12} {status}")
        print()
        return

    # Show warning for Stage 3/4
    has_manual_only = any(s in ("3", "4") for s in stages)
    if has_manual_only and not args.dry_run:
        print()
        warn("Stages 3 and 4 are marked manual_only in execution_policy.json.")
        warn("Gemini 1.5 Flash is stronger than tested local models — but still ADVISORY.")
        warn("Citation accuracy (Stage 4 especially) must be human-reviewed.")
        print()
        resp = input("  Continue? [y/N]: ").strip().lower()
        if resp != "y":
            print("  Aborted.")
            sys.exit(0)
        print()

    # Load API key (skip for dry run)
    api_key = ""
    if not args.dry_run:
        api_key = load_api_key()
        info(f"API key loaded ({api_key[:8]}...)")

    # Run
    hdr(f"\n{'[DRY RUN] ' if args.dry_run else ''}Running stages {', '.join(stages)} for: {', '.join(jd_list)}")
    print()

    total = 0
    succeeded = 0

    for jd_name in jd_list:
        hdr(f"── {jd_name} ──")
        for stage_key in stages:
            total += 1
            # Rate limit: 15 req/min on free tier → wait 5s between calls
            if not args.dry_run and total > 1:
                time.sleep(5)
            success = run_stage(jd_name, stage_key, api_key, args.dry_run, args.force)
            if success:
                succeeded += 1
        print()

    # Summary
    hdr("── Summary ──")
    ok(f"{succeeded}/{total} stages completed")

    if succeeded < total:
        warn(f"{total - succeeded} stage(s) failed — check errors above")

    if not args.dry_run and succeeded > 0:
        print()
        info("Next steps:")
        info("  1. Review responses in prompts/JDx_PREP/resp/ before acting on them")
        info("  2. Stage 2: check Critical Gaps section for fabricated claims")
        info("  3. Stage 3: verify every recommended edit traces to the actual resume text")
        info("  4. Stage 4: verify every evidence citation names a real file in output/evidence/")
        info("  5. Run ./scripts/batch_prep.sh --status to see updated dashboard")
    print()


if __name__ == "__main__":
    main()
