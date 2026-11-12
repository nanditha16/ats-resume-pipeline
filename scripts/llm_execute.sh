#!/bin/bash
#
# llm_execute.sh — Sends a prompt file to a local Ollama model and saves
# the response. ADDITIVE — the prompt file this reads already exists
# regardless of whether this runs; if Ollama isn't running or this fails,
# the prompt file is still there to upload manually to Claude.ai.
#
# POLICY-GATED: every call now goes through policy/policy_check.py before
# anything runs. This replaced the previous filename-pattern matching
# (checking if BASENAME started with "variant_rank_prompt" etc.) which
# hardcoded the same rules independently of policy/execution_policy.json —
# meaning editing the policy file wouldn't have changed this script's
# actual behavior. Now there is exactly one place execution rules live.
#
# Usage:
#   ./scripts/llm_execute.sh <prompt_file> <stage_key> <model> [--override] [--force]
#
#   stage_key: must match a key in policy/execution_policy.json "stages"
#              (e.g. stage_2_ats_optimize, stage_0_1_variant_rank)
#   --override: required to run a manual_only/untested stage locally.
#               Without it, the script refuses before ever contacting
#               Ollama. This is enforced by policy_check.py, not by this
#               script re-implementing the logic.
#   --force: separate flag, only affects the context-window size check
#            below (unrelated to the trust-policy override).
#
#   Examples:
#     ./scripts/llm_execute.sh prompts/ats_prompt.txt stage_2_ats_optimize llama3.1:8b
#     ./scripts/llm_execute.sh prompts/variant_rank_prompt.txt stage_0_1_variant_rank llama3.1:8b --override
#
set -e

PROMPT_FILE="$1"
STAGE_KEY="$2"
MODEL="$3"
shift 3 2>/dev/null || true
OVERRIDE_FLAG=""
FORCE_FLAG=""
for arg in "$@"; do
  [ "$arg" == "--override" ] && OVERRIDE_FLAG="--override"
  [ "$arg" == "--force" ] && FORCE_FLAG="--force"
done

OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

if [ -z "$PROMPT_FILE" ] || [ -z "$STAGE_KEY" ] || [ -z "$MODEL" ]; then
  echo "❌ Usage: ./scripts/llm_execute.sh <prompt_file> <stage_key> <model_name> [--override] [--force]"
  echo "   Example: ./scripts/llm_execute.sh prompts/ats_prompt.txt stage_2_ats_optimize llama3.1:8b"
  echo ""
  echo "   stage_key must match policy/execution_policy.json. Known stages:"
  python3 "$(dirname "$0")/../policy/policy_check.py" __list_stages__ 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    for s in data.get('known_stages', []):
        print('     -', s)
except Exception:
    pass
" 2>/dev/null
  exit 1
fi

if [ ! -f "$PROMPT_FILE" ]; then
  echo "❌ Prompt file not found: $PROMPT_FILE"
  exit 1
fi

# ─────────────────────────────────────────────────────────────────────
# POLICY CHECK — must pass before anything else happens
# ─────────────────────────────────────────────────────────────────────
echo "🔒 Checking execution policy for stage '$STAGE_KEY' ..."
POLICY_SCRIPT="$(dirname "$0")/../policy/policy_check.py"
if [ "$OVERRIDE_FLAG" == "--override" ]; then
  POLICY_RESULT=$(python3 "$POLICY_SCRIPT" "$STAGE_KEY" --override) || true
else
  POLICY_RESULT=$(python3 "$POLICY_SCRIPT" "$STAGE_KEY") || true
fi
# Re-run to get the actual exit code — $() with set -e swallows it above.
# This is a deliberate two-call pattern: first captures output safely,
# second gets the real exit code. The output is identical both times.
if [ "$OVERRIDE_FLAG" == "--override" ]; then
  python3 "$POLICY_SCRIPT" "$STAGE_KEY" --override > /dev/null 2>&1
else
  python3 "$POLICY_SCRIPT" "$STAGE_KEY" > /dev/null 2>&1
fi
POLICY_EXIT=$?

# Stage-key / prompt-file consistency check — catches the case where
# the user passes a wrong stage_key (e.g. stage_1_5_prepare_variant
# for an ats_prompt.txt), which would succeed with the wrong trust label.
# stage_1_5_prepare_variant is local_always (no LLM), so it should never
# be used to run a prompt file at all.
PROMPT_BASENAME=$(basename "$PROMPT_FILE")
if [ "$STAGE_KEY" == "stage_1_5_prepare_variant" ]; then
  echo "❌ stage_1_5_prepare_variant involves no LLM — it's a file-move"
  echo "   operation. It cannot be used as a stage_key for running a prompt."
  echo "   Did you mean stage_2_ats_optimize?"
  exit 1
fi

if [ $POLICY_EXIT -eq 2 ] || [ $POLICY_EXIT -eq 3 ]; then
  echo "❌ Policy check failed:"
  echo "$POLICY_RESULT" | python3 -m json.tool 2>/dev/null || echo "$POLICY_RESULT"
  exit 1
fi

if [ $POLICY_EXIT -eq 1 ]; then
  echo ""
  echo "🛑 BLOCKED by execution policy."
  echo "$POLICY_RESULT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('   Reason:', d.get('reason', 'unknown'))
print('   Evidence:', d.get('evidence', 'n/a'))
"
  echo ""
  echo "   To proceed anyway, re-run with --override. Output will be"
  echo "   labeled 'unsafe' / 'Do not use without review' per policy."
  exit 1
fi

TRUST_LEVEL=$(echo "$POLICY_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('trust_level') or 'none')")
TRUST_LABEL=$(echo "$POLICY_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('trust_label') or 'N/A')")

echo "✅ Policy allows execution. Trust level: $TRUST_LEVEL ($TRUST_LABEL)"

if [ "$TRUST_LEVEL" == "unsafe" ]; then
  echo ""
  echo "⚠️  This output will be UNSAFE per policy — proceeding only because"
  echo "   --override was given. Do not treat the result as reliable."
fi

echo "🔍 Checking Ollama is reachable at $OLLAMA_URL ..."
TAGS_RESPONSE=$(curl -s --max-time 5 "$OLLAMA_URL/api/tags" 2>&1) || {
  echo "❌ Could not reach Ollama at $OLLAMA_URL"
  echo "   Is it running? Try: ollama serve"
  exit 1
}

if ! echo "$TAGS_RESPONSE" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
  echo "❌ Ollama responded but not with valid JSON. Got:"
  echo "$TAGS_RESPONSE" | head -c 500
  exit 1
fi

CONTEXT_LENGTH=$(echo "$TAGS_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
model = '$MODEL'
for m in data.get('models', []):
    if m.get('name') == model or m.get('model') == model:
        print(m.get('details', {}).get('context_length', ''))
        sys.exit(0)
print('')
")

if [ -z "$CONTEXT_LENGTH" ]; then
  echo "❌ Model '$MODEL' not found in 'ollama list' / /api/tags."
  echo "   Available models:"
  echo "$TAGS_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('models', []):
    print('  -', m.get('name'), f\"(context: {m.get('details', {}).get('context_length', '?')})\")
"
  echo "   Pull it first with: ollama pull $MODEL"
  exit 1
fi

echo "✅ Model '$MODEL' found, context window: $CONTEXT_LENGTH tokens"

PROMPT_CHARS=$(wc -c < "$PROMPT_FILE" | tr -d ' ')
ESTIMATED_TOKENS=$((PROMPT_CHARS / 4))
echo "📏 Prompt file: $PROMPT_FILE ($PROMPT_CHARS chars, ~${ESTIMATED_TOKENS} estimated tokens)"

if [ "$ESTIMATED_TOKENS" -gt "$CONTEXT_LENGTH" ]; then
  echo ""
  echo "❌ Prompt (~${ESTIMATED_TOKENS} tokens) exceeds '$MODEL''s context"
  echo "   window (${CONTEXT_LENGTH} tokens)."
  if [ "$FORCE_FLAG" != "--force" ]; then
    echo "   Options:"
    echo "   1. Use a larger-context model (check 'ollama list' / pull one)"
    echo "   2. Use a cloud model instead (upload $PROMPT_FILE to Claude.ai manually)"
    echo "   3. Re-run with --force to send anyway (not recommended)"
    exit 1
  else
    echo "   ⚠️  --force given, sending anyway. Output quality not guaranteed."
  fi
fi

echo "🧠 Sending to $MODEL via Ollama (this may take a while on CPU)..."

# Timeout is configurable via LLM_TIMEOUT_SECONDS env var (default 1800s
# = 30 min). The original 900s (15 min) default was tuned against
# llama3.1:8b's ~300s real runs with headroom — too short for larger or
# reasoning-style models (e.g. deepseek-r1, which generates extended
# internal "thinking" output before its final answer, often 3-10x more
# tokens than a direct-answer model of similar size) on large prompts.
# Confirmed via real timeout: deepseek-r1:14b exceeded 900s on the
# ~52K-token Stage 0/1 prompt and never got the chance to write output.
TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-1800}"
echo "   (timeout: ${TIMEOUT_SECONDS}s — set LLM_TIMEOUT_SECONDS to change)"

PROMPT_CONTENT=$(cat "$PROMPT_FILE")
REQUEST_START=$(date +%s)

RESPONSE=$(python3 -c "
import json, sys
import urllib.request

payload = json.dumps({
    'model': sys.argv[1],
    'prompt': sys.argv[2],
    'stream': False
}).encode('utf-8')

req = urllib.request.Request(
    '$OLLAMA_URL/api/generate',
    data=payload,
    headers={'Content-Type': 'application/json'},
    method='POST'
)

try:
    with urllib.request.urlopen(req, timeout=$TIMEOUT_SECONDS) as resp:
        body = json.loads(resp.read().decode('utf-8'))
        if 'error' in body:
            print('OLLAMA_ERROR:' + body['error'], file=sys.stderr)
            sys.exit(1)
        print(body.get('response', ''))
except TimeoutError:
    print('TIMEOUT_ERROR: no response after ${TIMEOUT_SECONDS}s', file=sys.stderr)
    sys.exit(2)
except Exception as e:
    err_str = str(e)
    if 'timed out' in err_str.lower():
        print('TIMEOUT_ERROR: no response after ${TIMEOUT_SECONDS}s', file=sys.stderr)
        sys.exit(2)
    print('REQUEST_ERROR:' + err_str, file=sys.stderr)
    sys.exit(1)
" "$MODEL" "$PROMPT_CONTENT")

REQUEST_EXIT=$?
REQUEST_END=$(date +%s)

if [ $REQUEST_EXIT -eq 2 ]; then
  echo "❌ TIMEOUT after $((REQUEST_END - REQUEST_START))s — this is NOT a"
  echo "   crash. The model is likely still slower than ${TIMEOUT_SECONDS}s"
  echo "   for this prompt size (larger/reasoning-style models like"
  echo "   deepseek-r1 generate extended internal output before answering)."
  echo "   Options:"
  echo "   1. Re-run with a longer timeout: LLM_TIMEOUT_SECONDS=3600 $0 $*"
  echo "   2. Use a smaller/faster model"
  echo "   3. Upload the prompt to Claude.ai instead (always available,"
  echo "      no timeout)"
  exit 1
elif [ $REQUEST_EXIT -ne 0 ]; then
  echo "❌ Ollama request failed."
  exit 1
fi

echo "⏱️  Generation took $((REQUEST_END - REQUEST_START))s"

# Sanitize model name for use as a directory name (':' and '/' aren't
# safe as literal path components on all filesystems/shells) — the real
# $MODEL value (unsanitized) is still what's sent to the Ollama API above.
MODEL_DIR=$(echo "$MODEL" | tr ':/' '__')
RESPONSE_DIR="prompts/${MODEL_DIR}"
mkdir -p "$RESPONSE_DIR"

OUTPUT_FILE="${RESPONSE_DIR}/$(basename "${PROMPT_FILE%.txt}")_response.txt"

{
  echo "════════════════════════════════════════════════════════════"
  echo "TRUST LEVEL: $TRUST_LEVEL — $TRUST_LABEL"
  echo "Stage: $STAGE_KEY | Model: $MODEL | Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "════════════════════════════════════════════════════════════"
  echo ""
  echo "$RESPONSE"
} > "$OUTPUT_FILE"

echo "✅ Response saved to $OUTPUT_FILE"
echo "👉 The original prompt file ($PROMPT_FILE) is still there if you want"
echo "   to also run it through Claude.ai for comparison."
