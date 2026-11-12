#!/usr/bin/env python3
"""
scripts/check_tier.py — Tier access gate for ATS Pipeline.

Current state: stub only. Checks for a local config file to determine
which tier the user has access to. No network calls, no server auth.

Future state (v0.2): validates a license key or BYOK config against
a lightweight auth endpoint. The interface below stays the same —
only the _validate() function changes.

Usage:
    python3 scripts/check_tier.py                    # print current tier
    python3 scripts/check_tier.py --check stage_2    # exit 0 if allowed, 1 if not
    python3 scripts/check_tier.py --setup            # guided BYOK setup

Tiers:
    free    No API key. Manual Claude.ai upload/paste only.
    pro     BYOK API key present. Stage 2 automation enabled.
    team    Managed key from org. All stages automated. (future)
"""

import argparse
import json
import os
import sys
from pathlib import Path

CONFIG_DIR  = Path.home() / ".markitdown-codespace"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Stage → minimum tier required for automation
STAGE_TIERS = {
    "stage_0_1": "free",   # always manual (human gate)
    "stage_1_5": "free",   # no LLM, deterministic
    "stage_2":   "pro",    # BYOK unlocks this
    "stage_3":   "team",   # managed only (human review required)
    "stage_4":   "team",   # managed only (citation accuracy)
    "stage_6":   "team",   # cover letter
}

TIER_ORDER = ["free", "pro", "team"]


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


def detect_tier(config: dict) -> str:
    """
    Determine current tier from local config.
    Future: add network validation here for team tier.
    """
    # Team tier: managed key (future — not yet implemented)
    if config.get("managed_key"):
        return "team"

    # Pro tier: any BYOK key present
    has_byok = any([
        config.get("anthropic_api_key"),
        config.get("gemini_api_key"),
        config.get("gcp_project"),
        os.environ.get("ANTHROPIC_API_KEY"),
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GCP_PROJECT"),
        (Path.home() / ".markitdown-codespace" / "claude_api_key").exists(),
        (Path.home() / ".markitdown-codespace" / "gemini_api_key").exists(),
        (Path.home() / ".config" / "gcloud" / "application_default_credentials.json").exists(),
    ])
    if has_byok:
        return "pro"

    return "free"


def tier_allows(tier: str, stage: str) -> bool:
    required = STAGE_TIERS.get(stage, "team")
    return TIER_ORDER.index(tier) >= TIER_ORDER.index(required)


def print_status(tier: str, config: dict):
    icons = {"free": "🆓", "pro": "⚡", "team": "🏢"}
    print(f"\n  ATS Pipeline — {icons.get(tier, '')} {tier.upper()} tier\n")

    print("  Stage access:")
    for stage, required in STAGE_TIERS.items():
        allowed = tier_allows(tier, stage)
        symbol = "✅" if allowed else "—"
        label = {
            "stage_0_1": "Stage 0/1  Variant ranking",
            "stage_1_5": "Stage 1.5  Isolate variant",
            "stage_2":   "Stage 2    ATS evaluation (automated)",
            "stage_3":   "Stage 3    Paraphrase edits (automated)",
            "stage_4":   "Stage 4    Evidence gaps (automated)",
            "stage_6":   "Stage 6    Cover letter (automated)",
        }.get(stage, stage)
        print(f"    {symbol}  {label}")

    print()
    if tier == "free":
        print("  To unlock Stage 2 automation (Pro tier):")
        print("    python3 scripts/check_tier.py --setup")
        print()
        print("  Interested in Team tier (managed, all stages)?")
        print("    See ROADMAP.md or open a GitHub issue with label 'tier-feedback'")
    elif tier == "pro":
        print("  Pro tier active — Stage 2 automation available.")
        print("  Stages 3/4 remain manual (requires Team tier).")
        print("  See ROADMAP.md for Team tier details.")
    print()


def run_setup():
    """Guided BYOK setup — stores config locally, no data sent anywhere."""
    CONFIG_DIR.mkdir(exist_ok=True)
    config = load_config()

    print()
    print("  ATS Pipeline — Pro tier setup (BYOK)")
    print("  Your API key is stored locally only. We never see it.")
    print()
    print("  Choose your API provider:")
    print("    1. Google Vertex AI (Gemini 2.5 Flash) — ~$0.001/JD, uses gcloud auth")
    print("    2. Anthropic Claude (claude-sonnet-4-5)  — ~$0.03/JD, requires API key")
    print("    3. Both")
    print()

    choice = input("  Enter 1, 2, or 3: ").strip()

    if choice in ("1", "3"):
        print()
        print("  Vertex AI setup:")
        print("    1. Install gcloud: brew install --cask google-cloud-sdk")
        print("    2. Run: gcloud auth application-default login")
        project = input("  Your GCP project ID (e.g. my-project-123): ").strip()
        if project:
            config["gcp_project"] = project
            print(f"  ✅ GCP project set: {project}")

    if choice in ("2", "3"):
        print()
        print("  Anthropic setup:")
        print("    Get key at: console.anthropic.com → API Keys → Create key")
        key = input("  Paste your sk-ant-... key (input hidden): ").strip()
        if key.startswith("sk-ant-"):
            key_file = CONFIG_DIR / "claude_api_key"
            key_file.write_text(key)
            key_file.chmod(0o600)
            config["anthropic_key_file"] = str(key_file)
            print(f"  ✅ Claude API key saved to {key_file}")
        else:
            print("  ⚠️  Key doesn't look right (expected sk-ant-...) — skipping")

    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    print()
    print("  ✅ Config saved to ~/.markitdown-codespace/config.json")
    print()
    print("  Test Stage 2 automation:")
    print("    python3 scripts/vertex_execute.py --jd JD1 --dry-run    (Vertex AI)")
    print("    python3 scripts/claude_execute.py --jd JD1 --dry-run    (Claude)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Check ATS Pipeline tier access.")
    parser.add_argument("--check", metavar="STAGE",
                        help="Exit 0 if stage automation is allowed, 1 if not")
    parser.add_argument("--setup", action="store_true",
                        help="Guided BYOK setup for Pro tier")
    parser.add_argument("--json", action="store_true",
                        help="Output tier info as JSON")
    args = parser.parse_args()

    config = load_config()
    tier   = detect_tier(config)

    if args.setup:
        run_setup()
        return

    if args.check:
        stage = args.check.lower().replace("-", "_")
        if tier_allows(tier, stage):
            if not args.json:
                print(f"✅ {stage} automation allowed ({tier} tier)")
            sys.exit(0)
        else:
            required = STAGE_TIERS.get(stage, "team")
            if not args.json:
                print(f"❌ {stage} requires {required} tier (you have: {tier})")
                print(f"   Run: python3 scripts/check_tier.py --setup")
            sys.exit(1)

    if args.json:
        print(json.dumps({
            "tier": tier,
            "stages": {s: tier_allows(tier, s) for s in STAGE_TIERS}
        }))
        return

    print_status(tier, config)


if __name__ == "__main__":
    main()
