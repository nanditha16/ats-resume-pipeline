#!/usr/bin/env python3
"""
scripts/vertex_execute.py — Run ATS pipeline Stage 2 through Vertex AI Gemini.

Design: org-billed, audit-tracked, production path.
Uses gcloud Application Default Credentials (ADC) — no API keys.
Billed to the Google Cloud project, not a personal account.

Architecture:
  User → ATS Pipeline → Vertex AI (gemini-1.5-flash-002) → billed to GCP project
                                                          → usage in Cloud Billing
                                                          → logs in Cloud Logging

Prerequisites (one-time per machine):
  1. Install gcloud:
       brew install --cask google-cloud-sdk   (Mac)
       # or: https://cloud.google.com/sdk/docs/install
  
  2. Authenticate:
       gcloud auth application-default login
  
  3. Set project:
       gcloud config set project YOUR_GCP_PROJECT_ID
  
  4. Enable API (if not done):
       gcloud services enable aiplatform.googleapis.com

  5. Install Vertex AI SDK (inside Docker or on host):
       pip install google-cloud-aiplatform

Inside Docker (required for pipeline integration):
  Add to docker-compose.yml volumes:
    - ~/.config/gcloud:/root/.config/gcloud:ro
  Then rebuild: docker compose up --build

Usage:
  python3 scripts/vertex_execute.py --jd JD2
  python3 scripts/vertex_execute.py --all
  python3 scripts/vertex_execute.py --jd JD2 --dry-run
  python3 scripts/vertex_execute.py --jd JD2 --estimate
  python3 scripts/vertex_execute.py --list

Cost reference (Vertex AI Gemini 1.5 Flash pricing):
  Input:  $0.00001875 per 1K characters (~$0.075 per 1M tokens)
  Output: $0.000075 per 1K characters (~$0.30 per 1M tokens)
  Stage 2 typical: ~13K chars in + ~8K chars out ≈ $0.0009 per JD
  All 15 JDs Stage 2: ≈ $0.014 total
  Well within $300 free credit.

Scope: Stage 2 only (2_ats_prompt.txt → ats_prompt_response.txt).
Stages 3 and 4 remain manual — upload to Claude.ai.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent.parent.resolve()
PROMPTS = ROOT / "prompts"

# ── Vertex AI config ──────────────────────────────────────────────────────────
# Your GCP project
GCP_PROJECT  = os.environ.get("GCP_PROJECT", "YOUR_GCP_PROJECT_ID")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
# Model name for new google-genai SDK on Vertex AI backend
# gemini-2.0-flash is available on Vertex AI and uses ADC auth
VERTEX_MODEL = "gemini-2.5-flash"

# ── Stage definitions ─────────────────────────────────────────────────────────
STAGES = {
    "2": {
        "prompt_names": ["2_ats_prompt.txt", "ats_prompt.txt"],
        "response_name": "ats_prompt_response.txt",
        "label": "Stage 2 — ATS evaluation",
        "trust": "advisory",
    },
    "3": {
        "prompt_names": ["3_ats_recommend_prompt.txt", "ats_recommend_prompt.txt"],
        "response_name": "ats_recommend_prompt_response.txt",
        "label": "Stage 3 — Paraphrase recommendations",
        "trust": "advisory (UNTESTED for Gemini — local models failed this task shape)",
    },
    "4": {
        "prompt_names": ["4_ats_evidence_gap_prompt.txt", "ats_evidence_gap_prompt.txt"],
        "response_name": "ats_evidence_gap_response.txt",
        "label": "Stage 4 — Evidence gap analysis",
        "trust": "advisory (UNTESTED for Gemini — citation accuracy failed on every local model tried)",
    },
}

# Vertex AI pricing (Gemini 1.5 Flash, as of 2025)
# Check: cloud.google.com/vertex-ai/generative-ai/pricing
PRICE_INPUT_PER_1K_CHARS  = 0.00001875   # $0.01875 per 1M chars
PRICE_OUTPUT_PER_1K_CHARS = 0.000075     # $0.075 per 1M chars
AVG_OUTPUT_CHARS = 8000  # typical Stage 2 response


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


# ── Vertex AI import ──────────────────────────────────────────────────────────
def import_genai():
    """Import google-genai SDK (new, replaces vertexai SDK deprecated June 2025)."""
    try:
        from google import genai
        from google.genai import types
        return genai, types
    except ImportError:
        err("google-genai SDK not installed.")
        print()
        print("  Install it:")
        print("    pip install google-genai")
        sys.exit(1)


# ── Check gcloud credentials ──────────────────────────────────────────────────
def check_credentials():
    """Verify gcloud ADC credentials are available."""
    adc_paths = [
        Path.home() / ".config" / "gcloud" / "application_default_credentials.json",
        Path("/root/.config/gcloud/application_default_credentials.json"),  # inside Docker
        Path(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent")),
    ]
    for p in adc_paths:
        if p.exists():
            info(f"Credentials found: {p}")
            return True

    err("gcloud credentials not found.")
    print()
    print("  Run on your Mac (not inside Docker):")
    print("    gcloud auth application-default login")
    print("    gcloud config set project YOUR_GCP_PROJECT_ID")
    print()
    print("  Then ensure docker-compose.yml mounts credentials:")
    print("    volumes:")
    print("      - ~/.config/gcloud:/root/.config/gcloud:ro")
    print()
    print("  Rebuild: docker compose down && docker compose up --build")
    return False


# ── Folder helpers ─────────────────────────────────────────────────────────────
def resolve_prep_dir(jd_name: str) -> Path:
    ready = PROMPTS / f"{jd_name}_READY"
    return ready if ready.exists() else PROMPTS / f"{jd_name}_PREP"

def find_prompt_file(prep_dir: Path, stage_key: str):
    for name in STAGES[stage_key]["prompt_names"]:
        p = prep_dir / "prom" / name
        if p.exists():
            return p
    return None

def response_exists(prep_dir: Path, stage_key: str) -> bool:
    return (prep_dir / "resp" / STAGES[stage_key]["response_name"]).exists()

def all_jds_with_prompts(stages: list) -> list:
    names = []
    for d in sorted(PROMPTS.iterdir()):
        m = re.match(r"^(JD\d+)(?:_PREP|_READY)$", d.name)
        if not m or not d.is_dir():
            continue
        jd = m.group(1)
        prep = resolve_prep_dir(jd)
        if all(find_prompt_file(prep, s) for s in stages):
            names.append(jd)
    return sorted(set(names), key=lambda x: int(re.sub(r"\D", "", x) or "0"))


# ── Cost estimate ──────────────────────────────────────────────────────────────
def show_estimate(jd_list: list, stages: list):
    hdr(f"\nVertex AI cost estimate — {VERTEX_MODEL}")
    hdr(f"Project: {GCP_PROJECT}")
    print()
    total_cost = 0.0
    for jd_name in jd_list:
        prep = resolve_prep_dir(jd_name)
        for s in stages:
            f = find_prompt_file(prep, s)
            if not f:
                warn(f"  {jd_name} Stage {s}: prompt not found")
                continue
            input_chars = len(f.read_text(errors="replace"))
            cost = (input_chars / 1000 * PRICE_INPUT_PER_1K_CHARS) + \
                   (AVG_OUTPUT_CHARS / 1000 * PRICE_OUTPUT_PER_1K_CHARS)
            total_cost += cost
            print(f"  {jd_name} Stage {s}: {input_chars:,} chars in + ~{AVG_OUTPUT_CHARS:,} out  ≈ ${cost:.5f}")
    print()
    ok(f"Estimated total: ${total_cost:.5f}")
    info("Pricing: cloud.google.com/vertex-ai/generative-ai/pricing")
    info("Output char count estimated — actual varies by response length")
    info(f"Your $300 credit covers ~{int(300 / max(total_cost, 0.0001)):,} runs of this batch")
    print()


# ── Run one stage ─────────────────────────────────────────────────────────────
def run_stage(
    jd_name: str,
    stage_key: str,
    _unused1,
    _unused2,
    _unused3,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    prep_dir    = resolve_prep_dir(jd_name)
    stage       = STAGES[stage_key]
    prompt_file = find_prompt_file(prep_dir, stage_key)

    if not prompt_file:
        warn(f"  {jd_name} Stage {stage_key}: prompt file not found")
        return False

    resp_file = prep_dir / "resp" / stage["response_name"]

    if resp_file.exists() and not force:
        ok(f"  {jd_name} Stage {stage_key}: response exists (--force to rerun)")
        return True

    prompt_text  = prompt_file.read_text(encoding="utf-8", errors="replace")
    input_chars  = len(prompt_text)
    est_cost     = (input_chars / 1000 * PRICE_INPUT_PER_1K_CHARS) + \
                   (AVG_OUTPUT_CHARS / 1000 * PRICE_OUTPUT_PER_1K_CHARS)

    info(f"  {jd_name} {stage['label']}")
    info(f"    Prompt: {prompt_file.name} ({input_chars:,} chars, ~${est_cost:.5f})")
    info(f"    Model:  {VERTEX_MODEL}")
    info(f"    Trust:  ADVISORY — review before acting on output")

    if dry_run:
        info(f"    [DRY RUN] → {resp_file.relative_to(ROOT)}")
        return True

    info("    Calling Vertex AI (google-genai SDK)...")
    import sys; sys.stdout.flush()
    start = time.time()

    try:
        genai, types = import_genai()
        # Use Vertex AI backend with ADC credentials
        client = genai.Client(
            vertexai=True,
            project=GCP_PROJECT,
            location=GCP_LOCATION,
        )
        response = client.models.generate_content(
            model=VERTEX_MODEL,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=8192,
                top_p=0.8,
            ),
        )
        text = response.text
        if not text.strip():
            raise ValueError("Empty response")
    except Exception as e:
        err(f"  {jd_name} Stage {stage_key} failed: {e}")
        if "403" in str(e) or "PERMISSION_DENIED" in str(e):
            err("  Permission denied — check: gcloud auth application-default login")
        elif "404" in str(e) or "NOT_FOUND" in str(e):
            err(f"  Model not found. Try: GCP_LOCATION=us-east4 python3 scripts/vertex_execute.py --jd {jd_name}")
        return False

    elapsed = int(time.time() - start)
    output_chars = len(text)
    actual_cost  = (input_chars / 1000 * PRICE_INPUT_PER_1K_CHARS) + \
                   (output_chars / 1000 * PRICE_OUTPUT_PER_1K_CHARS)

    resp_file.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"<!-- ADVISORY: Generated by Vertex AI {VERTEX_MODEL} via vertex_execute.py -->\n"
        f"<!-- Project: {GCP_PROJECT} | JD: {jd_name} | Time: {elapsed}s -->\n"
        f"<!-- Cost: ~${actual_cost:.5f} ({input_chars:,} in + {output_chars:,} out chars) -->\n\n"
    )
    resp_file.write_text(header + text, encoding="utf-8")

    ok(f"  {jd_name} Stage {stage_key} done ({elapsed}s)")
    info(f"    Cost: ~${actual_cost:.5f} ({input_chars:,} in + {output_chars:,} out chars)")
    info(f"    Response: {str(resp_file.relative_to(ROOT))}")
    import sys; sys.stdout.flush()
    return True


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Run ATS pipeline Stage 2 through Vertex AI Gemini (org-billed).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/vertex_execute.py --jd JD2
  python3 scripts/vertex_execute.py --all
  python3 scripts/vertex_execute.py --estimate          # cost projection, no API call
  python3 scripts/vertex_execute.py --jd JD2 --dry-run
  python3 scripts/vertex_execute.py --list

Override project/location:
  GCP_PROJECT=my-project GCP_LOCATION=us-east1 python3 scripts/vertex_execute.py --jd JD2
        """,
    )
    parser.add_argument("--jd",       help="Single JD name (e.g. JD2)")
    parser.add_argument("--all",      action="store_true", help="All JDs with requested stage prompts")
    parser.add_argument("--stages",   default="2", help="Comma-separated stage numbers to run (default: 2)")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--force",    action="store_true", help="Re-run even if response exists")
    parser.add_argument("--list",     action="store_true", help="List ready JDs and exit")
    parser.add_argument("--estimate", action="store_true", help="Show cost estimate and exit")
    args = parser.parse_args()

    stages = [s.strip() for s in args.stages.split(",")]
    for s in stages:
        if s not in STAGES:
            err(f"Unknown stage: {s}. Valid: {', '.join(STAGES.keys())}")
            sys.exit(1)

    if args.jd:
        jd_list = [args.jd]
    elif args.all or args.estimate or args.list:
        jd_list = all_jds_with_prompts(stages)
    else:
        parser.print_help()
        print()
        err("Specify --jd JDx or --all")
        sys.exit(1)

    if args.list:
        hdr(f"\nJDs with stage {', '.join(stages)} prompt(s) ready:")
        for jd in jd_list:
            prep = resolve_prep_dir(jd)
            missing = [s for s in stages if not response_exists(prep, s)]
            status = "needs response" if missing else "responses exist"
            print(f"  {jd:<12} {status}")
        print()
        return

    if args.estimate:
        show_estimate(jd_list, stages)
        return

    has_manual_only = any(s in ("3", "4") for s in stages)
    if has_manual_only and not args.dry_run:
        print()
        warn("Stages 3 and 4 are marked manual_only in execution_policy.json.")
        warn("Gemini has not been evidence-tested against this task shape — every")
        warn("local model tried fabricated filenames/citations on Stage 3 and 4.")
        warn("Spot-check every recommendation and citation before acting on it.")
        print()
        if sys.stdin.isatty():
            resp = input("  Continue? [y/N]: ").strip().lower()
            if resp != "y":
                print("  Aborted.")
                sys.exit(0)
        else:
            info("  Non-interactive invocation (no TTY) — proceeding without prompt.")
        print()

    if not args.dry_run:
        if not check_credentials():
            sys.exit(1)

    hdr(f"\n{'[DRY RUN] ' if args.dry_run else ''}Running stages {', '.join(stages)} for: {', '.join(jd_list)}")
    hdr(f"Project: {GCP_PROJECT} | Model: {VERTEX_MODEL}")
    print()

    total = succeeded = 0
    for jd_name in jd_list:
        hdr(f"── {jd_name} ──")
        for stage_key in stages:
            total += 1
            if run_stage(jd_name, stage_key, None, None, None,
                         args.dry_run, args.force):
                succeeded += 1
        print()

    hdr("── Summary ──")
    ok(f"{succeeded}/{total} stages completed")
    if succeeded < total:
        warn(f"{total - succeeded} failed — check errors above")
    if not args.dry_run and succeeded > 0:
        print()
        info("Review responses in prompts/JDx_PREP/resp/ before acting on them")
        info("Cost tracked in: console.cloud.google.com → Billing → Reports")
    print()


if __name__ == "__main__":
    main()
