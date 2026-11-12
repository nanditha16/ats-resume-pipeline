#!/usr/bin/env python3
"""
policy_check.py — Reads policy/execution_policy.json and answers exactly
one question correctly, every time: "is this stage allowed to run on a
local model right now, and if so, what trust label does the output get?"

This exists so the policy file is enforced code, not documentation that
scripts/UI can drift from. Every execution path (llm_execute.sh today,
any future UI backend) should call this before running a stage locally —
never re-implement the policy logic inline.

Usage:
    python3 policy_check.py <stage_key> [--override]

    stage_key: one of the keys under "stages" in execution_policy.json
               (e.g. stage_0_1_variant_rank, stage_2_ats_optimize)

    --override: explicit acknowledgment flag for manual_only stages.
                Without it, manual_only stages always return "blocked".
                This script does NOT decide whether override is allowed —
                that's a UI/CLI-layer decision; this script only reports
                what the policy says and whether override was requested.

Exit codes:
    0 = allowed (prints trust_level and any required UI text to stdout as JSON)
    1 = blocked (manual_only stage, no override given)
    2 = stage_key not found in policy
    3 = policy file missing or invalid

Output (stdout, JSON): always machine-readable, so a UI backend can parse
it directly rather than scraping text.
"""
import json
import sys
import os

POLICY_PATH = os.path.join(os.path.dirname(__file__), "execution_policy.json")


def load_policy():
    if not os.path.isfile(POLICY_PATH):
        print(json.dumps({"error": f"Policy file not found: {POLICY_PATH}"}))
        sys.exit(3)
    try:
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Policy file is invalid JSON: {e}"}))
        sys.exit(3)


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: policy_check.py <stage_key> [--override]"}))
        sys.exit(3)

    stage_key = sys.argv[1]
    override_requested = "--override" in sys.argv[2:]

    policy = load_policy()
    stages = policy.get("stages", {})

    if stage_key not in stages:
        print(json.dumps({
            "error": f"Unknown stage_key '{stage_key}'.",
            "known_stages": list(stages.keys())
        }))
        sys.exit(2)

    stage = stages[stage_key]
    execution_policy = stage.get("execution_policy")
    local_allowed = stage.get("local_execution_allowed", False)

    result = {
        "stage_key": stage_key,
        "description": stage.get("description"),
        "execution_policy": execution_policy,
        "evidence": stage.get("evidence"),
        "ui_must_show": stage.get("ui_must_show", []),
    }

    if execution_policy == "local_always":
        result["allowed"] = True
        result["trust_level"] = None  # no LLM involved, trust labeling doesn't apply
        print(json.dumps(result))
        sys.exit(0)

    if execution_policy == "local_allowed":
        result["allowed"] = True
        result["trust_level"] = "advisory"
        result["trust_label"] = policy["trust_levels"]["advisory"]["label"]
        print(json.dumps(result))
        sys.exit(0)

    if execution_policy in ("manual_only", "untested"):
        if override_requested:
            result["allowed"] = True
            result["trust_level"] = "unsafe"
            result["trust_label"] = policy["trust_levels"]["unsafe"]["label"]
            result["override_used"] = True
            result["warning"] = (
                "Policy says this stage is manual_only/untested. Override was "
                "explicitly requested — proceeding, but output MUST be labeled "
                "'unsafe' / 'Do not use without review' in any UI."
            )
            print(json.dumps(result))
            sys.exit(0)
        else:
            result["allowed"] = False
            result["trust_level"] = None
            result["reason"] = (
                f"Stage '{stage_key}' is {execution_policy} per policy. "
                "Local execution is blocked by default. Re-run with --override "
                "to proceed anyway (output will be labeled unsafe)."
            )
            print(json.dumps(result))
            sys.exit(1)

    # Unknown execution_policy value in the JSON itself — fail closed,
    # not open. A typo in the policy file should never silently allow
    # something.
    result["allowed"] = False
    result["error"] = f"Unrecognized execution_policy value: '{execution_policy}'. Failing closed."
    print(json.dumps(result))
    sys.exit(3)


if __name__ == "__main__":
    main()
