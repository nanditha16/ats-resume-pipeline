#!/usr/bin/env python3
"""
scripts/claude_execute.py — Run pipeline prompt files through the Claude API.

Uses the SAME model as Claude.ai (claude-sonnet-4-5) — output quality is
identical to manual upload/paste. This script just automates the loop.

Model: claude-sonnet-4-5
  - Same model you use in Claude.ai
  - 200K token context window
  - Cost: ~$0.30-1.50 per JD (all 4 stages) at current Sonnet pricing
  - Requires Anthropic API key (separate from Claude.ai subscription)

Cost note: Claude.ai Pro subscription does NOT include API access.
The API is billed separately at console.anthropic.com.
Approximate cost per JD (Stages 2+3+4):
  Stage 2: ~$0.05-0.15  (single resume + JD, ~4-16K tokens)
  Stage 3: ~$0.10-0.30  (resume chunks, ~6-24K tokens)
  Stage 4: ~$0.20-1.00  (resume + evidence corpus, ~78K tokens)
  Total per JD: ~$0.35-1.45

Setup (one time):
  1. Go to console.anthropic.com → API Keys → Create Key
  2. Store it:
       echo 'sk-ant-YOUR_KEY_HERE' > ~/.markitdown-codespace/claude_api_key
       chmod 600 ~/.markitdown-codespace/claude_api_key
  3. Run:
       python3 scripts/claude_execute.py --jd JD2
       python3 scripts/claude_execute.py --jd JD2 --stages 2     # single stage
       python3 scripts/claude_execute.py --all                    # all JDs
       python3 scripts/claude_execute.py --jd JD2 --dry-run      # preview

Output files written to prompts/JDx_PREP/resp/:
  Stage 2 → ats_prompt_response.txt
  Stage 3 → ats_recommend_prompt_response.txt
  Stage 4 → ats_evidence_gap_response.txt

Trust level: AUTHORITATIVE — same model as Claude.ai manual upload.
Responses marked with source header but no advisory warning needed.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.resolve()
PROMPTS = ROOT / "prompts"

# ── Gemini API ────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"

# ── Stage definitions ─────────────────────────────────────────────────────────
STAGES = {
    "2": {
        "prompt_names": ["2_ats_prompt.txt", "ats_prompt.txt"],
        "response_name": "ats_prompt_response.txt",
        "label": "Stage 2 — ATS evaluation",
        "trust": "authoritative",
        "policy": "local_allowed",
    },
    "3": {
        "prompt_names": ["3_ats_recommend_prompt.txt", "ats_recommend_prompt.txt"],
        "response_name": "ats_recommend_prompt_response.txt",
        "label": "Stage 3 — Paraphrase recommendations",
        "trust": "authoritative",
        "policy": "manual_only (overridden — Gemini Flash, not 8B local model)",
    },
    "4": {
        "prompt_names": ["4_ats_evidence_gap_prompt.txt", "ats_evidence_gap_prompt.txt"],
        "response_name": "ats_evidence_gap_response.txt",
        "label": "Stage 4 — Evidence gap analysis",
        # Phase A lists a row per JD requirement (JD1 had 21) and Phase B, True Gaps and Flags follow it. At 8192 the
        # response was cut off mid-sentence in "True Gaps" and the Flags list never arrived.
        "max_tokens": 16000,
        "trust": "authoritative",
        "policy": "manual_only (overridden — Gemini Flash, not 8B local model)",
    },
    "cover": {
        "prompt_names": ["5_cover_letter_prompt.txt", "cover_letter_prompt.txt"],
        "response_name": "cover_letter_response.txt",
        "label": "Cover Letter",
        "trust": "untested",
        # Same override pattern as Stage 3/4, NOT a policy downgrade -- see
        # policy/execution_policy.json's own "cover_letter" entry, which
        # still reads execution_policy: "untested" and
        # local_execution_allowed: false. Running this via --stages cover
        # is a per-run user decision (--ignore-gate required, same flag
        # Stage 3/4 already use), not a default the tool picks for you.
        "policy": "untested (overridden — Claude Sonnet via API, explicit user choice, not a default)",
    },
    "rescore": {
        "prompt_names": ["9_ats_rescore_prompt.txt"],
        "response_name": "ats_rescore_response.txt",
        "label": "Stage 9 — Final ATS re-score",
        "trust": "advisory",
        # Same policy as Stage 2 itself -- this is the identical task
        # (ats_optimize.sh, unchanged) pointed at the finalized resume
        # instead of the original variant, not a new task shape needing
        # its own evidence basis.
        "policy": "local_allowed (same evidence basis as Stage 2 — identical prompt, different resume content)",
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
    Load Claude API key from (in priority order):
      1. ANTHROPIC_API_KEY environment variable
      2. ~/.markitdown-codespace/claude_api_key file
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key

    key_file = Path.home() / ".markitdown-codespace" / "claude_api_key"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key

    err("Claude API key not found.")
    print()
    print("  Set it up with one of:")
    print("    export ANTHROPIC_API_KEY='sk-ant-...'")
    print("    OR")
    print("    mkdir -p ~/.markitdown-codespace")
    print("    echo 'sk-ant-...' > ~/.markitdown-codespace/claude_api_key")
    print("    chmod 600 ~/.markitdown-codespace/claude_api_key")
    print()
    print("  Get your key: https://console.anthropic.com → API Keys")
    print("  Note: API billing is separate from Claude.ai Pro subscription")
    sys.exit(1)


# ── Gemini API call ───────────────────────────────────────────────────────────
LAST_CALL: dict = {}   # real usage and stop_reason of the most recent successful call


def call_claude(api_key: str, prompt_text: str, stage_label: str, max_tokens: int = 8192) -> str:
    """
    Send prompt to Claude Sonnet via Anthropic API, return response text.
    Same model as Claude.ai — identical output quality.
    The socket timeout scales with max_tokens: a non-streaming reply sends nothing until it is finished, and
    Stage 4 already took 214 s for about 8,900 tokens, so a fixed 300 s would turn a larger limit into a timeout.
    """
    timeout = max(300, max_tokens // 20 + 120)
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt_text}],
    }

    data = json.dumps(payload).encode("utf-8")

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                CLAUDE_URL,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            # Extract text from response
            content_blocks = result.get("content", [])
            text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")

            if not text.strip():
                raise ValueError("Empty response text")

            # Log token usage for cost awareness
            usage = result.get("usage", {})
            input_tok  = usage.get("input_tokens", 0)
            output_tok = usage.get("output_tokens", 0)
            info(f"    Tokens: {input_tok:,} in / {output_tok:,} out")
            stop = result.get("stop_reason")
            LAST_CALL.clear()
            LAST_CALL.update({"stop_reason": stop, "input_tokens": input_tok, "output_tokens": output_tok,
                              "max_tokens": max_tokens})
            if stop == "max_tokens":
                warn(f"    TRUNCATED: {stage_label} stopped at max_tokens={max_tokens:,}; the response is incomplete. "
                     f"Raise max_output_tokens for this stage in policy/execution_policy.json and re-run with --force.")

            return text

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                wait = 60 * (attempt + 1)
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


# ── Persistent run log ────────────────────────────────────────────────────────
RUN_LOG_PATH = ROOT / "policy" / "run_log.jsonl"


def log_run(jd_name: str, stage_key: str, stage_label: str, cost: float,
            input_tok: int, output_tok: int, elapsed: int) -> None:
    """Append one line to policy/run_log.jsonl -- a persistent, cross-session
    record of every stage run (sequential id, timestamp, JD, stage, cost,
    tokens, duration). Deliberately a flat append-only file, not a database:
    the actual need stated was "for now just a simple file write... something
    that lasts beyond each session" -- a database adds real complexity
    (schema, migrations, concurrent-write handling) this need doesn't
    require. JSON Lines specifically because it's append-safe (each line is
    independently valid, so a crash mid-write never corrupts prior entries
    the way appending to a single JSON array's closing bracket would) and
    trivially readable later (grep it, or one line of
    pandas.read_json(path, lines=True) for anything resembling analysis).
    run_id increments by counting existing lines -- correct for the
    sequential, single-process batch runs this tool actually does; not
    safe against two processes appending at the exact same instant, which
    isn't a real scenario for a single-user CLI tool run from one terminal."""
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    run_id = 1
    if RUN_LOG_PATH.exists():
        with open(RUN_LOG_PATH, "r", encoding="utf-8") as f:
            run_id = sum(1 for _ in f) + 1
    entry = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        "jd": jd_name,
        "stage": stage_key,
        "stage_label": stage_label,
        "cost_usd": round(cost, 5),
        "tokens_in": input_tok,
        "tokens_out": output_tok,
        "duration_s": elapsed,
    }
    with open(RUN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


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

    info(f"    Calling Claude API ({CLAUDE_MODEL})...")
    start = time.time()

    max_tokens = stage.get("max_tokens", 8192)
    try:    # policy/execution_policy.json may override: {"max_output_tokens": {"4": 24000}}
        override = json.loads((ROOT / "policy" / "execution_policy.json").read_text()).get("max_output_tokens", {})
        max_tokens = int(override.get(str(stage_key), max_tokens))
    except (OSError, ValueError):
        pass
    try:
        response_text = call_claude(api_key, prompt_text, stage["label"], max_tokens)
    except Exception as e:
        err(f"  {jd_name} Stage {stage_key} failed: {e}")
        return False

    elapsed = int(time.time() - start)

    # Write response
    resp_file.parent.mkdir(parents=True, exist_ok=True)

    # Prepend advisory header so the file is self-documenting
    input_tok  = LAST_CALL.get("input_tokens") or len(prompt_text) // 4      # real usage when the API reported it
    output_tok = LAST_CALL.get("output_tokens") or len(response_text) // 4
    cost = (input_tok / 1_000_000 * 3.0) + (output_tok / 1_000_000 * 15.0)
    stop = LAST_CALL.get("stop_reason")
    stop_line = ""
    if stop:
        stop_line = f"<!-- Stop: {stop} | max_tokens={max_tokens} -->\n"
        if stop == "max_tokens":
            stop_line += (f"<!-- TRUNCATED: the API stopped this response at its output limit ({max_tokens:,} tokens); "
                          f"later sections are missing -->\n")
    header = (
        f"<!-- AUTHORITATIVE: Generated by {CLAUDE_MODEL} via claude_execute.py -->\n"
        f"<!-- Same model as Claude.ai — same quality as manual upload/paste. -->\n"
        f"<!-- Stage: {stage['label']} | JD: {jd_name} | Time: {elapsed}s -->\n"
        f"<!-- Cost: ~${cost:.5f} (~{input_tok:,} in + ~{output_tok:,} out tokens) -->\n"
        f"{stop_line}\n"
    )
    resp_file.write_text(header + response_text, encoding="utf-8")

    # Cost calculation (Sonnet pricing: $3/M input, $15/M output)
    # Token counts are in the response usage — approximate from prompt length
    input_tok  = len(prompt_text) // 4
    output_tok = len(response_text) // 4
    cost = (input_tok / 1_000_000 * 3.0) + (output_tok / 1_000_000 * 15.0)
    ok(f"  {jd_name} Stage {stage_key} done ({elapsed}s)")
    info(f"    Cost: ~${cost:.5f} (~{input_tok:,} in + ~{output_tok:,} out tokens)")
    info(f"    Response: {str(resp_file.relative_to(ROOT))}")
    log_run(jd_name, stage_key, stage["label"], cost, input_tok, output_tok, elapsed)
    import sys; sys.stdout.flush()
    return True


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Run ATS pipeline Stage 2 through the Claude API (claude-sonnet-4-5).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/claude_execute.py --jd JD2              # all stages (2,3,4)
  python3 scripts/claude_execute.py --jd JD2 --stages 2   # stage 2 only
  python3 scripts/api_execute.py --all                  # all JDs with prompts
  python3 scripts/claude_execute.py --all --stages 2,3     # all JDs, stages 2+3
  python3 scripts/claude_execute.py --jd JD2 --dry-run    # preview without calling API
  python3 scripts/claude_execute.py --jd JD2 --force      # rerun even if response exists

Setup:
  echo 'sk-ant-...' > ~/.markitdown-codespace/claude_api_key
  Get key: https://console.anthropic.com → API Keys
        """,
    )
    parser.add_argument("--jd", help="Single JD name (e.g. JD2)")
    parser.add_argument("--all", action="store_true", help="Run all JDs with prompts ready")
    parser.add_argument(
        "--stages",
        default="2",
        help="Comma-separated stage numbers to run (default: 2)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without calling the API")
    parser.add_argument("--force", action="store_true", help="Re-run even if response already exists")
    parser.add_argument("--list", action="store_true", help="List JDs that have prompts ready and exit")
    parser.add_argument("--ignore-gate", action="store_true",
                         help="Run Stage 3/4 even if Stage 2's verdict is NO (explicit override, mirrors policy_check.py's --override)")
    parser.add_argument("--estimate", action="store_true", help="Show token counts and cost estimate without calling API")

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

    # Estimate mode — show token counts and cost without calling API
    if args.estimate:
        # Claude Sonnet pricing (approximate, check console.anthropic.com for current)
        INPUT_COST_PER_1K  = 0.003   # $3 per 1M input tokens
        OUTPUT_COST_PER_1K = 0.015   # $15 per 1M output tokens
        AVG_OUTPUT_TOKENS  = 2000    # typical Stage 2 response

        hdr(f"\nToken + cost estimate for: {', '.join(jd_list)}")
        hdr(f"Model: {CLAUDE_MODEL}")
        print()
        total_input = 0
        total_cost  = 0.0
        for jd_name in jd_list:
            prep_dir = resolve_prep_dir(jd_name)
            for stage_key in stages:
                prompt_file = find_prompt_file(prep_dir, stage_key)
                if not prompt_file:
                    warn(f"  {jd_name} Stage {stage_key}: prompt file not found")
                    continue
                text = prompt_file.read_text(errors="replace")
                # Rough token estimate: ~4 chars per token
                input_tok = len(text) // 4
                est_cost  = (input_tok / 1000 * INPUT_COST_PER_1K) +                             (AVG_OUTPUT_TOKENS / 1000 * OUTPUT_COST_PER_1K)
                total_input += input_tok
                total_cost  += est_cost
                print(f"  {jd_name} Stage {stage_key}: ~{input_tok:,} input tokens + ~{AVG_OUTPUT_TOKENS:,} output  ≈ ${est_cost:.4f}")
        print()
        print(f"  Total input tokens : ~{total_input:,}")
        print(f"  Estimated total    : ~${total_cost:.4f}")
        print()
        info("Pricing reference: console.anthropic.com/settings/billing")
        info("Output token count is estimated at 2,000 — actual varies by response length")
        print()
        return

    # Show warning for Stage 3/4
    has_manual_only = any(s in ("3", "4") for s in stages)
    if has_manual_only and not args.dry_run:
        print()
        warn("Stages 3 and 4 are marked manual_only in execution_policy.json.")
        warn("Claude Sonnet is the SAME model as Claude.ai — output is authoritative quality.")
        warn("Citation accuracy (Stage 4) should still be spot-checked.")
        print()
        if sys.stdin.isatty():
            resp = input("  Continue? [y/N]: ").strip().lower()
            if resp != "y":
                print("  Aborted.")
                sys.exit(0)
        else:
            info("  Non-interactive invocation (no TTY) — proceeding without prompt.")
            info("  The caller's own confirmation step (e.g. a UI button click) stands in for this.")
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
        gated = False
        for stage_key in stages:
            # ── COST GATE ──
            # Stage 2's own verdict already says whether this JD is worth
            # pursuing. Stage 3 (~3x Stage 2's cost) and Stage 4 (~10x,
            # since it includes the full evidence corpus — see ROADMAP.md's
            # session cost example) are pure spend if Stage 2 said NO:
            # paraphrasing or adding evidence-backed bullets cannot fix a
            # structural mismatch a Shortlist:NO verdict already flagged.
            # This check only fires when Stage 2 ran *in this same
            # invocation* (or its response already exists on disk) and
            # only blocks stages 3/4 — Stage 2 itself always runs, since
            # its verdict is what the gate depends on.
            if stage_key in ("3", "4", "cover") and not args.ignore_gate:
                prep_dir = resolve_prep_dir(jd_name)
                s2_resp = prep_dir / "resp" / STAGES["2"]["response_name"]
                if s2_resp.exists():
                    verdict_m = re.search(r"\*\*Shortlist:\*\*\s*(YES|NO|MAYBE)", s2_resp.read_text(errors="replace"), re.IGNORECASE)
                    if verdict_m and verdict_m.group(1).upper() == "NO":
                        warn(f"  {jd_name} Stage {stage_key}: SKIPPED — Stage 2 verdict is NO "
                             f"(cost gate; re-run with --ignore-gate to force this stage anyway)")
                        gated = True
                        continue
            total += 1
            # Rate limit: 15 req/min on free tier → wait 5s between calls
            if not args.dry_run and total > 1:
                time.sleep(5)
            success = run_stage(jd_name, stage_key, api_key, args.dry_run, args.force)
            if success:
                succeeded += 1
        if gated:
            info(f"  {jd_name}: Stage 3/4/cover spend avoided by the cost gate. Run "
                 f"'python3 scripts/synthesize_resume.py {jd_name}' to see why Stage 2 said NO.")
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
