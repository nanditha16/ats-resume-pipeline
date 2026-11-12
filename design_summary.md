# Orchestration Layer — Summary

## Tree: what changed and why

```
scripts/llm_execute.sh        [NEW, then revised 3x]
  v1: provider-agnostic (Ollama + Claude/OpenAI/Gemini API stubs)
      → reverted: user wanted Ollama-only for now, untested cloud code
        removed rather than left as dead/unverified paths
  v2: Ollama-only, single hardcoded response path
      → bug found via real test: two different models' responses
        overwrote each other (ats_prompt_response.txt collision)
  v3: routes output to prompts/<sanitized_model_name>/ per model
      → tested: confirmed llama3.1:8b and llama3:8b produce
        non-colliding paths
  v3.1: timeout 900s hardcoded → ${LLM_TIMEOUT_SECONDS:-1800}, configurable
      → bug found via real test: deepseek-r1:14b timed out at 900s;
        generic error gave no indication it was a timeout vs. a crash
      → added: distinct timeout detection (exit code 2), explanatory
        message, 3 concrete next steps (longer timeout / smaller model /
        Claude.ai)

scripts/ats_workflow.sh       [NEW, then revised 1x]
  v1: chains Stage 0→1.5→2→3, auto-executes each via Ollama, hardcoded
      default model "llama3.1:8b"
      → bug found via real test: user deleted llama3.1:8b, pulled
        deepseek-r1:14b, hardcoded default would have silently pointed
        at a model that no longer existed
  v2: no --model given → queries live /api/tags, lists actually-pulled
      models with context length, prompts for explicit selection
      → tested: both explicit-flag and no-flag code paths verified

scripts/variant_rank.sh       [unchanged this phase — already rebuilt
                                 earlier this session from embedding-based
                                 to LLM-prompt-based scoring]
```

## Design considerations

1. **Additive, never a replacement.** Every auto-execution script still
   writes the underlying prompt file to `prompts/` regardless of whether
   automation runs or succeeds. The manual Claude.ai upload path is the
   permanent fallback/primary, not a legacy mode being phased out.

2. **Verify against the real environment, not assumptions.** Every claim
   in this layer (context window per model, response shape, timeout
   behavior) was confirmed against the user's actual running Ollama
   instance via real `curl`/script output — not inferred from
   documentation or a different sandbox. This was an explicit standing
   rule established earlier in the session after two prior incidents
   (`wkhtmltopdf` availability, `reportlab` dependency) where sandbox
   presence was wrongly treated as proof of container presence.

3. **Warn, don't block — but warn specifically, not generically.**
   Stage 0/1 and Stage 3 warnings name the exact failure modes seen in
   real testing (no variant named, hallucinated content, lost output
   format) rather than a generic "this might not work" — the warning
   text itself is evidence-based.

4. **Required arguments over silent defaults, when a wrong default is
   worse than an error.** Model selection moved from a hardcoded default
   to a required, live-queried choice specifically because a silent
   wrong default (pointing at a deleted model) is a worse failure mode
   than asking.

## Model evaluation — 3 models tested, same JD/resume/prompts throughout

| Model | Params | Stage 0/1 (52K tok) | Stage 2 (4K tok) | Stage 3 (6K tok) | Time (0/1 → 2 → 3) |
|---|---|---|---|---|---|
| llama3:8b | 8B | not tested this phase | Fabricated a Critical Gap (missing PM experience) that's the candidate's core qualification | not tested this phase | — |
| llama3.1:8b | 8B | Never named a single variant from 21; confused a resume filename ("eBay") with the target company | Same core misread as llama3:8b (called PM experience "lacking") | Used job titles instead of chunk filenames; audited dates instead of proposing wording edits | ~309s / ~275s / ~262s |
| deepseek-r1:14b | 14.8B | Only addressed 3 of 21 variants; invented two non-existent "eBay Resume 1/2" variants; concluded "GOOD FIT" for an unspecified generic role, not the real JD | **Best result of all three** — accurate, grounded gaps (language/culture fit, mentorship), no fabricated claims | Produced a formatting/style audit instead of paraphrase edits; invented "Deloitte 1/2/3" when only one resume was in scope | ~738s / ~871s / ~918s |

**Conclusion, evidence-based:** single-resume, moderate-length, single-task
prompts (Stage 2) are within reach of local 8–14B models — `deepseek-r1:14b`
in particular gave a usable result. Multi-document, multi-entity,
strict-output-format tasks (Stage 0/1, Stage 3) failed on **all three
models tested**, and a larger/slower/reasoning-tuned model did not
improve quality — it took 2.4–3x longer to produce a differently-wrong
answer. This is treated as a capability ceiling at this model size class
for this task type, not a model-selection problem solvable by trying a
fourth model.

A live system check during the `deepseek-r1:14b` run also showed the
Mac under real memory pressure (147MB free of 16GB, active bidirectional
swapping in the tens of millions of pages, ~4.3GB held idle by two
Docker Desktop VM processes) — the slow times may partly reflect memory
contention, not pure compute cost. Worth knowing, but does not change
the *quality* finding above, which was about correctness, not speed.

## Practical implication for the orchestrator / UI

- **Stage 2 (`ats_optimize.sh`): safe to auto-run locally by default.**
  No warning needed beyond what already exists.
- **Stage 0/1 (`variant_rank.sh`) and Stage 3 (`ats_recommend.sh`): should
  require explicit, deliberate opt-in, not just a soft warning + pause.**
  Three real failures across three models is enough evidence to make this
  a harder gate in the UI (e.g., a confirmation checkbox with the failure
  modes listed, not a single "proceed anyway" button) — discussed as a
  candidate change but not yet implemented; flagging it here as a known
  open item for the UI phase rather than deciding it unilaterally.
- **Any UI showing local-model output should visually distinguish
  "Ollama draft (unverified)" from "Claude.ai result" — never present
  them with equal visual weight**, given the confirmed failure rate on
  the harder stages.
- **Timeouts need a progress indicator, not a silent wait.** A user
  watching a spinner for 15+ minutes with no feedback has no way to
  distinguish "working" from "hung" — the CLI's elapsed-time-only
  feedback was barely adequate even there.

## What's needed for the next phase (chatbot-style UI, no CLI)

Concrete, scoped to what this session's testing actually surfaced as
necessary — not a generic feature wishlist:

1. **A persistent job/run state model.** The CLI orchestrator runs
   synchronously in one terminal session with `read -p` blocking on
   user input. A UI needs the workflow to be resumable/async: JD
   uploaded → Stage 0/1 running in background → user can leave and
   come back → variant selection happens whenever the user is ready,
   not blocking a script.
2. **Streaming or progress feedback for long-running local-model calls.**
   Real generation times observed: 262s–918s. A chatbot UI showing
   nothing for 15 minutes will read as broken. At minimum, surface
   Ollama's `done: false` streaming chunks (the API supports
   `stream: true`, not used yet in this project) or a live elapsed-timer
   with the known model/prompt-size context ("this typically takes
   ~5-15 min for this prompt size on your machine").
3. **A model-quality indicator baked into the UI, not just docs.** Given
   the confirmed 3-model failure pattern on Stage 0/1 and Stage 3, the UI
   should surface this as structured metadata (e.g., a badge: "Local
   models: reliable for this stage" vs. "Local models: unreliable for
   this stage — Claude.ai recommended") rather than relying on the user
   having read this summary.
4. **File-system abstraction.** Everything currently keyed on literal
   paths (`output/resume/*.md`, `chunks/*.md`, `prompts/<model>/*.txt`)
   works for a CLI user who knows the project layout. A chatbot UI needs
   this abstracted into named entities (variants, JDs, runs) the user
   interacts with by name/upload, not by knowing the directory structure.
5. **Decide the UI's relationship to Docker.** Every stage currently
   requires the `markitdown` container running and reachable via
   `docker exec`. A genuinely "no CLI" experience needs the UI to manage
   container lifecycle itself (start/health-check/stop) rather than
   assuming the user has already run `./run.sh` in a terminal.
6. **Decide whether the UI calls scripts as subprocesses or
   reimplements logic natively.** Reimplementing risks drifting from the
   now-tested CLI behavior; shelling out to the existing scripts
   preserves everything verified in this session but means the UI's
   backend needs the same shell/Docker access the CLI does. This is a
   real architectural fork to decide explicitly before writing UI code,
   not something to default into.

---

## Policy as Code — Design, Definition, and Execution

### Why a policy layer exists

Before this layer was built, execution rules were scattered across three
different scripts, each re-implementing its own version of "should I run
this locally?":

- `ats_workflow.sh` had hardcoded filename patterns (`variant_rank_prompt*`,
  `ats_recommend_prompt*`) to decide when to print a warning
- `llm_execute.sh` had a separate, identical hardcoded filename check
- Both scripts had their own concept of what "blocked" meant

This meant editing the policy in one place didn't change behavior in
another. The policy layer replaced all of this with a single source of
truth: `policy/execution_policy.json` defines the rules; `policy/policy_check.py`
enforces them; every script reads the enforcer, never re-implements the rules.

### What is defined — `policy/execution_policy.json`

The file has five top-level sections:

**`trust_levels`** — defines the three possible output classifications:
- `authoritative`: human-verified Claude.ai output. Nothing in this system
  marks output authoritative automatically — this label only applies after
  a human confirms it.
- `advisory`: local model output on a stage with confirmed-adequate
  reliability. Directionally useful; spot-check before acting on it.
- `unsafe`: local model output on a stage with confirmed unreliable
  behavior. 3/3 tested models failed these stages. Never present as a
  final answer.

**`stages`** — one entry per pipeline stage, each containing:
- `execution_policy`: one of `manual_only`, `local_allowed`,
  `local_always`, `untested`
- `local_execution_allowed`: boolean, derived from the policy value
- `default_action_on_run_click`: what happens when a user clicks "Run"
  — `generate_prompt_file_only`, `execute_immediately`, or
  `prompt_user_local_or_cloud`
- `evidence`: exact text of what was observed in real testing. Not
  prose — a specific description of what the model did or didn't do.
- `ui_must_show`: list of things the UI is required to surface for this
  stage, regardless of what the user has already seen

Current stage classifications:

| Stage | `execution_policy` | `local_execution_allowed` | Evidence basis |
|---|---|---|---|
| `stage_0_1_variant_rank` | `manual_only` | false | 3/3 models: named wrong variants, hallucinated content, lost output format |
| `stage_1_5_prepare_variant` | `local_always` | true | No LLM — deterministic file ops, tested against mock and real data |
| `stage_2_ats_optimize` | `local_allowed` | true | 3/3 models: directionally OK, 2/3 fabricated one Critical Gap |
| `stage_3_ats_recommend` | `manual_only` | false | 3/3 models: ignored the actual task, produced formatting audits instead |
| `cover_letter` | `untested` | false | Never run against a local model — defaults to manual until evidence exists |

**`model_recommendations`** — tested model results per stage. Currently
only populated for `stage_2_ats_optimize` (the only stage with local
execution allowed). `deepseek-r1:14b` is listed as `best_observed` —
accurate, no fabricated claims — though 3x slower than `llama3.1:8b`.

**`cost_and_limits`** — provider-level metadata. Ollama is fully
populated (no cost, context limit queried live). Cloud provider fields
(`claude_api`, `openai_api`) exist as placeholder schema only, explicitly
marked `"integrated": false` with a warning not to display cost estimates
until actually wired in. This was a deliberate decision: no fake numbers
for APIs that don't exist yet in this system.

**`default_behavior`** — the principle: clicking "Run" must never
silently execute on an unverified path. Documents what each
`default_action_on_run_click` value means in concrete terms.

### How it works — `policy/policy_check.py`

The enforcer is a standalone Python script that reads the JSON and answers
exactly one question: "is this stage allowed to run locally, and if so,
what trust label does its output get?"

```
python3 policy/policy_check.py <stage_key> [--override]
```

Exit codes are machine-readable:
- `0` = allowed (also JSON to stdout with `trust_level`, `trust_label`,
  `evidence`, `ui_must_show`)
- `1` = blocked (`manual_only`/`untested`, no override given)
- `2` = `stage_key` not found in policy
- `3` = policy file missing or invalid JSON

Output is always valid JSON to stdout — never prose — so a UI backend can
parse it directly without scraping text.

The script fails **closed**, not open: an unrecognized `execution_policy`
value in the JSON blocks execution rather than allowing it. A typo in the
policy file should never silently permit a stage.

`--override` is the explicit opt-in for `manual_only`/`untested` stages.
The script doesn't decide whether override is appropriate — that's the
caller's responsibility. It only reports what the policy says and whether
override was requested, then sets `trust_level` to `unsafe` and includes
a warning in the JSON output.

### What happens when executed — `llm_execute.sh` integration

Every call to `llm_execute.sh` now runs `policy_check.py` before
contacting Ollama:

```
llm_execute.sh <prompt_file> <stage_key> <model> [--override] [--force]
```

The execution sequence:

1. Validate `stage_key` is not `stage_1_5_prepare_variant` (that stage
   has no LLM — using it is a user error, now caught immediately with a
   hint).

2. Call `policy_check.py <stage_key>` (with `--override` if given):
   - Uses `|| true` pattern to safely capture stdout regardless of exit
     code (a confirmed `set -e` bug was found where the `$()` subshell's
     non-zero exit killed the script before the BLOCKED message printed —
     fixed in this session).
   - Then calls the script again silently to get the real exit code.

3. On exit code 1 (blocked): print the full BLOCKED message including the
   `reason` and `evidence` fields from the JSON — not a generic "this is
   blocked." Exit without contacting Ollama.

4. On exit code 0 (allowed): extract `trust_level` and `trust_label` from
   the JSON. If `trust_level == "unsafe"` (override was used), print an
   explicit warning before proceeding.

5. Proceed with the Ollama call (context-window check, request, timeout
   handling).

6. Write response to `prompts/<sanitized_model_name>/<prompt_name>_response.txt`
   with a trust-level header baked into the file itself:
   ```
   ════════════════════════════════════════════════════════════
   TRUST LEVEL: advisory — Draft (local model — may be incorrect)
   Stage: stage_2_ats_optimize | Model: llama3.1:8b | Generated: 2026-06-22T17:48:46Z
   ════════════════════════════════════════════════════════════
   ```
   This means any future UI (or human) opening the raw file immediately
   sees its classification without needing to re-run the policy check.

### What the policy guarantees

These are concrete, code-enforced guarantees — not aspirational claims:

1. A stage cannot run locally and produce output unless `policy_check.py`
   returns exit code 0.
2. `manual_only` stages (Stage 0/1, Stage 3) require `--override` at the
   call site. The `ats_workflow.sh` orchestrator uses `read -p` as the
   human confirmation before passing `--override` — so user consent is
   both required and explicit.
3. Every locally-generated response file is permanently labeled with its
   trust level at the top. It cannot be mistaken for a Claude.ai result
   later, even if someone opens the file weeks after it was created.
4. Policy changes propagate everywhere automatically — editing
   `execution_policy.json` changes behavior in `llm_execute.sh`,
   `ats_workflow.sh`, and any future UI backend that calls
   `policy_check.py`, without touching any script.

### What the policy does NOT guarantee

Also important to state explicitly:

1. It does not guarantee output quality — `advisory` trust still means
   the model may be wrong. The trust level classifies reliability based
   on observed testing, not mathematical proof.
2. It does not prevent a user from always choosing `--override` on every
   blocked stage. The system makes this deliberate and visible; it cannot
   make it impossible.
3. It does not cover the cover letter stage — `untested` classification
   means "we haven't run this against a local model; we don't know if it
   works." The default is conservative (blocked), but no evidence either
   way exists yet.

### Final test validation (eBay/JD2 run, 2026-06-22)

Both manual and agent runs were validated against this policy:

- Stage 2 `ats_prompt_response.txt` carried correct `advisory` trust label,
  model name, stage key, and timestamp in the header.
- Stage 0/1 and Stage 3 response files carried `unsafe` trust labels
  (override was used by the user knowingly). Stage 3 content confirmed
  the known failure pattern — formatting audit instead of paraphrase edits.
- Policy BLOCKED message now prints correctly (silent exit bug was
  confirmed fixed: `🔒 Checking...` and `✅ Policy allows...` appeared
  correctly in the workflow output for all three stage calls).
- JD self-retrieval confirmed clean on both runs: no JD boilerplate
  appeared in the RETRIEVED SECTIONS of `ats_prompt.txt`.

---

## Phase 3 — Evidence Layer (Stage 3.5)

### What was added

```
input/evidence/                         [NEW directory]
  Career_Wealth.xlsx                    ← drop here (and any other evidence)
  project_notes.pdf
  <any employer/project notes>

scripts/ingest_evidence.sh              [NEW]
  Converts input/evidence/ → output/career_wealth_chunk/*.md
  Uses same MarkItDown container. pdfplumber first for PDFs, fallback to
  MarkItDown. Chunks large files by heading (>8KB threshold, same logic
  as smart_chunk.sh). Idempotent — clears stale chunks on re-run.

scripts/ats_evidence_gap.sh             [NEW]
  Stage 3.5 — builds prompts/ats_evidence_gap_prompt.txt
  Loads: JD + resume variant chunks + evidence corpus chunks + Stage 2/3
  gap output (if saved). Writes a structured prompt that asks Claude to:
    Phase A: for each gap, check evidence corpus for a matching fact
    Phase B: for each match, propose a minimal new resume bullet
             grounded only in the evidence — not inferred
  Outputs: prompts/ats_evidence_gap_prompt.txt → upload to Claude.ai

policy/execution_policy.json            [UPDATED]
  stage_3_5_evidence_gap added:
    execution_policy: manual_only
    rationale: same attribution failure mode as Stage 3 + more source
    files → more surface area for local model hallucination. Not yet
    tested; conservative default.
```

### Design rationale

**Why this is a separate stage, not folded into Stage 3:**
Stage 3 (ats_recommend) is constrained to the resume chunks only — by
design, it cannot reference material outside the chosen variant. That
constraint is correct for its purpose (paraphrase of existing facts) and
must not be relaxed. Stage 3.5 has a fundamentally different task: it
explicitly crosses a boundary Stage 3 must not cross. Folding them
together would dilute both constraints and make the output ambiguous.

**Why evidence ingest is separate from the ATS cycle:**
The evidence corpus (Career_Wealth.xlsx, project notes) changes rarely
and independently of which JD you're working on. Running ingest on every
ATS cycle would be wasteful. Separating it means: run `ingest_evidence.sh`
once when the corpus updates, then use `ats_evidence_gap.sh` for any JD.

**What Stage 3.5 does NOT do:**
- It does not write the new resume bullets for you. It proposes drafts
  grounded in evidence that the candidate must verify and reword.
- It does not reduce Stage 3's gap list. Stage 3 and Stage 3.5 are
  additive: Stage 3 fixes what's already there, Stage 3.5 finds what's
  not there at all.
- It does not replace the manual path. Policy is manual_only — same
  rationale as Stage 3.

**Gap taxonomy (end-to-end):**
```
Stage 2/3 output                Stage 3.5 outcome
─────────────────────────────   ──────────────────────────────────────
Presentation gap (text exists,  → Stage 3 handles (paraphrase only)
  phrased differently)
Evidence gap (real experience,  → Stage 3.5 surfaces (new bullet from
  not on any variant)              evidence corpus)
True gap (not on resume,        → Both stages name it honestly.
  not in evidence either)          Do not fabricate.
```

---

## Phase 4 — Batch Orchestration + Non-Developer UI

### What was added

```
scripts/batch_prep.sh               [NEW]
  Automates Sequences 1-5 of the manual multi-variant workflow.
  Two-phase: first run generates Stage 0/1 prompts; --continue generates
  Stages 2-4 after variant responses are pasted.

scripts/ui.sh                       [NEW]
  Terminal menu for non-developers. Numbered options covering all steps
  from adding JDs to generating cover letters.

scripts/normalize_prompt_names.sh   [NEW, one-time utility]
  Renames old unnumbered prompt files (ats_prompt.txt) to numbered
  convention (2_ats_prompt.txt). Safe to run multiple times.
```

### batch_prep.sh — flag reference

```bash
./scripts/batch_prep.sh                        # Sequence 1: gen Stage 0/1 prompts for all JDx.txt
./scripts/batch_prep.sh --continue             # Sequences 3-5: Stages 2-4 for all JDs with responses
./scripts/batch_prep.sh --continue --jd JD3   # Single JD only
./scripts/batch_prep.sh --status              # Dashboard only, no action
./scripts/batch_prep.sh --cover JD3           # Stage 6 cover letter for one JD
FORCE=1 ./scripts/batch_prep.sh --continue    # Re-run even if prompts already exist
FORCE=1 ./scripts/batch_prep.sh --continue --jd JD1  # Force single JD
```

### Design rationale

**Why batch_prep.sh mirrors the manual sequence exactly:**
The manual Sequence 1-5 was already optimized for correctness:
- Stage 0/1 generates ALL JDs' prompts in one pass (batch)
- Stage 1.5+2+3+4 runs ONE JD at a time (sequential, focused context)
- `smart_chunk.sh` wipes `chunks/` per JD+variant pair intentionally —
  this is not a bug. The script explicitly replicates `prepare_variant.sh`
  → `smart_chunk.sh` → `rm chunks/JDx_part_*.md` per JD.

Changing `smart_chunk.sh` behavior would have broken the focused-context
guarantee. Instead, `batch_prep.sh` groups JDs by chosen variant and
processes each group sequentially, which minimizes `prepare_variant.sh`
calls while preserving the one-JD-at-a-time chunk isolation.

**Variant extraction fallback chain:**
Claude's response doesn't always name the variant with an exact filename.
`extract_variant()` tries four strategies in order:
1. Bold `.md` filename on Top Pick line — `**Name.md**`
2. Rank-1 row in the markdown table
3. Bold keyword on Top Pick line (no `.md` extension)
4. Any bold `.md` filename anywhere in the file

`resolve_variant_file()` then matches the extracted keyword to actual files
using four strategies:
1. Exact glob match in `output/resume/`
2. Case-insensitive grep
3. Normalize spaces/underscores/hyphens and compare (catches the
   `Walmart_staff_ R-2506210` vs `Walmart_staff_R-2506210` space mismatch)
4. Last-segment match (keyword after final `_` or `-`)

**Bash 3 / zsh compatibility:**
`declare -A` (associative arrays) requires bash 4+. macOS ships bash 3.2.
The script uses temp files (`mktemp`) for the variant→JD map instead of
associative arrays. All subshell-return bugs (piped `while` loops where
`return` exits the subshell, not the function) are avoided by writing
results to temp files and reading with `while IFS= read -r`.

**POOR FIT handling:**
Responses containing "POOR FIT" are automatically archived to
`prompts/JD_Analysis/JDx_NO/` during `--continue`. No manual rename needed.

**Naming convention — numbered prompts:**
All prompts generated by `batch_prep.sh` use numbered filenames for
unambiguous ordering:
```
1_variant_rank_prompt.txt
1.5_semantic_prompt.txt
2_ats_prompt.txt
3_ats_recommend_prompt.txt
4_ats_evidence_gap_prompt.txt
5_cover_letter_prompt.txt
```
Older JDs generated before this convention used unnumbered names.
`normalize_prompt_names.sh` migrates them. The dashboard accepts both.

### Bugs fixed during development (evidence log)

| Bug | Root cause | Fix |
|-----|-----------|-----|
| `declare -A` failure on macOS | bash 3.2 / zsh don't support associative arrays | Replaced with temp files |
| `extract_variant` returning empty | `return` inside piped `while` subshell exits subshell, not function | Rewrote using temp file + sequential `while read` |
| `Walmart_staff_ R-2506210` not resolving | Space in filename; Claude's response omits it | Added normalize-and-compare strategy to `resolve_variant_file` |
| Stage 2 `Resume file not found` | `ats_optimize.sh` takes full path; batch passed basename only | Pass `output/${VARIANT_BASENAME}.md` (where `prepare_variant.sh` copies it) |
| Dashboard S2 showing ❌ for completed JDs | Checked `2_ats_prompt.txt` only; old JDs have `ats_prompt.txt` | Accept both naming conventions in dashboard check |
| `--continue` skipping JDs with responses | Response file named `1_variant_rank_prompt_response.txt`; script checked `variant_rank_prompt_response.txt` | `find_response()` tries all known naming variants |
| `smart_chunk.sh` chunking JDs not resumes | Script called without prepare_variant; `output/` contained all JDs + resumes | Corrected: `batch_prep.sh` always calls `prepare_variant.sh` first, mirroring manual sequence |

---

## Phase 5 — Local Web UI + First-Time Setup

### What was added

```
web/
  app.py                    Flask backend (runs INSIDE Docker container)
  templates/index.html      Single-page UI — 5 steps, chat-bubble guide
  static/css/style.css      Dark theme, purpose-built (no framework)
  static/js/app.js          All UI interactions, SSE streaming, state

scripts/serve.sh            Start the web UI (one command)
setup_first_time.sh         One-command first-time setup for new users
SETUP.md                    Plain-language setup guide (no jargon)
```

### Architecture

```
Browser (localhost:5001)
        ↕ HTTP / SSE
Flask app (inside Docker container — python3 /app/web/app.py)
        ↕ subprocess
Existing shell scripts (UNCHANGED — no pipeline logic modified)
        ↕ direct execution (no docker exec needed — already inside)
Python tools (retrieve.py, markitdown, pdfplumber, etc.)
```

Flask runs **inside** the same Docker container that runs all other pipeline
tools. This means:
- No host-side Python dependency (Flask is in the Dockerfile)
- Scripts run directly without `docker exec` wrapper
- Port 5001 mapped from container to host via docker-compose

### Design decisions

**Why Flask inside Docker, not on the host:**
The original design ran Flask on the host and called `docker exec markitdown`
for each pipeline command. This broke because every pipeline script also calls
`docker exec markitdown` internally — creating nested docker exec calls that
fail when the Docker CLI isn't available inside the container.

Moving Flask inside the container resolves the entire class of problem:
scripts run directly as subprocesses, no docker exec nesting possible.

**Why port 5001 not 5000:**
macOS reserves port 5000 for AirPlay Receiver (System Settings → AirDrop &
Handoff → AirPlay Receiver). Binding 5000 fails silently on macOS 12+.
Port 5001 has no system reservation.

**`/.dockerenv` detection:**
All pipeline scripts now check for `/.dockerenv` (injected by Docker into
every container). When present, scripts skip the `docker exec markitdown`
wrapper and run the inner logic directly. When absent (host terminal),
scripts call `docker exec` as before. Both workflows work correctly.

**JD text via temp file:**
`ats_optimize.sh` passes the JD text to `retrieve.py` as `sys.argv[1]`.
A multi-line 2KB JD text passed through `docker exec ... python retrieve.py "$JD_TEXT"`
breaks silently — argument quoting collapses newlines. Fixed by writing
JD text to a temp file, passing the path, reading inside Python. This
bypasses all shell argument quoting and docker exec argument limits.

**Chatbot is scripted, not LLM-backed:**
The UI guide is a state machine (5 fixed steps). It is not an LLM-backed
chatbot. This was a deliberate design choice:
- No API key required for non-technical users
- No additional cost or failure mode
- Scripted guide delivers identical UX value for the demo use case
- LLM-backed assistant remains a future option (Persona B) without
  changing any pipeline code

### Bugs fixed during Web UI development

| Bug | Root cause | Fix |
|-----|-----------|-----|
| Blank page at localhost:5000 | Flask debug mode's reloader re-ran `__main__`, `ROOT` path resolved ambiguously on restart | Set `debug=False` inside Docker; pin `ROOT = Path("/app")` via env var |
| Port 5000 unavailable | macOS AirPlay Receiver owns port 5000 on macOS 12+ | Changed to port 5001 throughout |
| `docker exec` fails inside Docker | Pipeline scripts call `docker exec markitdown` internally; Docker CLI not in container | Added `/.dockerenv` detection to all scripts; run directly when inside |
| `smart_chunk.sh` producing 0 chunks | Called `docker exec markitdown bash -c ...`; Docker CLI absent | Rewrote with `/.dockerenv` check; runs Python chunking directly |
| `ats_optimize.sh` Stage 2 silent fail | JD text passed as `$JD_TEXT` shell argument through `docker exec`; multi-line text breaks argument quoting | Write JD to temp file; pass path; read in Python |
| `grep` repetition-operator errors | `grep -E "(✅|❌|Saved)"` — macOS BSD grep fails on multi-byte Unicode in ERE alternation | Removed emoji from grep patterns; use plain-text equivalents |
| `awk` chunking breaks in Python string | `awk '...'` single-quotes inside Python raw string inside shell heredoc — three-layer quoting conflict | Replaced awk with inline `python3 -c` using temp file approach |
| Banner prints twice on serve.sh | Flask debug mode spawns reloader child; `__main__` block runs twice | `debug=False`; banner only in `__main__` block |
| `run.sh` fails from UI | `run.sh` calls `setup.sh` which calls `docker compose up` — Docker CLI absent in container | Web UI's `/api/run-pipeline` bypasses `setup.sh`, runs `router.sh → clean.sh → smart_chunk.sh` directly |

### `setup_first_time.sh` design

Single script for users who have never used this tool. Checks:
1. Git (`git --version`) — on Mac, offers `xcode-select --install` if missing,
   but notes most users with Docker Desktop already have Git
2. Docker Desktop (`docker info`) — polls every 3s for up to 60s if not running,
   opens Docker Desktop automatically on Mac
3. Port 5001 (`lsof -i :5001`) — warns if blocked, continues anyway
4. Docker Compose (`docker compose version`)
5. Scripts executable (`chmod +x scripts/*.sh`)
6. Input folders exist (`mkdir -p input/pdf input/evidence ...`)
7. Builds container (`docker compose up -d --build`)
8. Runs initial PDF conversion if `input/pdf/` is non-empty
9. Offers to start the web app immediately

**Xcode note:** `xcode-select --install` is offered only as a fallback when
`git` is missing. It is not a required step — users with Docker Desktop
installed almost certainly have Git already (Docker Desktop installs it on
Windows; most Mac developers have it pre-installed).
