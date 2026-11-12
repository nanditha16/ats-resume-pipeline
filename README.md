# ats-resume-pipeline

> Formerly `markitdown-codespace`. Your API keys and settings still live in `~/.markitdown-codespace/`; that directory name is unchanged on purpose.

A local-first, privacy-preserving job application prep pipeline built on
[MarkItDown](https://github.com/microsoft/markitdown), Docker, and a
policy-gated prompt-generation layer for use with Claude.ai (or any LLM).

**Design principle:** every script generates structured prompt files saved
to `prompts/`. Nothing calls an LLM API by default. You upload the prompts
to Claude.ai for evaluation. Local Ollama execution and API automation are
additive options — they never replace the prompt files.

---

## Prerequisites

| Requirement | Why | Install |
|---|---|---|
| **Docker Desktop** | Runs the entire pipeline — all tools live inside | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop) |
| **Git** | Clone the repo | Pre-installed on most Macs; [git-scm.com](https://git-scm.com) on Windows |
| **Terminal** | Run the startup command | Terminal.app (Mac) / Git Bash (Windows) |

**Port note:** macOS reserves port 5000 for AirPlay. This app uses port **5001**.

---

## First-time setup

```bash
git clone https://github.com/YOUR-ORG/ats-resume-pipeline.git
cd ats-resume-pipeline
./setup_first_time.sh
```

Checks Git and Docker, builds the container (5–15 min first time), creates
all folders, and opens the web app automatically. See `SETUP.md` for the
full plain-language walkthrough.

A fresh clone has no `input/`, `output/`, `prompts/` or `chunks/` folders (git does not store empty folders, and
these hold your private data, so they are git-ignored). `./scripts/init_dirs.sh` creates them; the setup scripts run it for you.
Run `./scripts/init_dirs.sh --check` to see what is missing.

**Every day after that:**
```bash
./scripts/serve.sh    # → http://localhost:5001
```

---

## Folder structure

```
ats-resume-pipeline/
├── input/
│   ├── pdf/                  Resume PDFs → converted to output/resume/*.md
│   ├── evidence/             Career records, project notes, xlsx, PDFs
│   │                         → used by ingest_evidence.sh (one-time / per update)
│   └── other/                JD text files (JD1.txt, JD2.txt …)
├── output/
│   ├── resume/               Variant bank — one .md per target role/company
│   ├── review_resume/        Stage 5/6 output — finalized/merged resumes and
│   │                         their exported PDFs (kept separate from the
│   │                         untouched variant bank so "what you're about to
│   │                         submit" is never confused with "the raw variant
│   │                         Stage 0/1 picked")
│   ├── JDx.md                Converted JD files (used directly by variant_rank.sh)
│   ├── cover/                Cover letters and exported PDFs
│   ├── career_wealth_chunk/  Evidence chunks (from ingest_evidence.sh)
│   └── _archive/             Files moved by prepare_variant.sh
├── chunks/                   Heading-split .md chunks (rebuilt per JD+variant pair)
├── prompts/
│   ├── variant_bank.txt      ← ALL resume variants (built once — Step 0)
│   ├── variant_rank_prompt.txt ← Evaluation instructions (built once — Step 0)
│   ├── jd_current.txt        ← Latest JD file reference (root-level copy)
│   ├── JD_Analysis/
│   │   ├── JDx/              variant_rank_prompt_response.txt (paste here)
│   │   └── JDx_NO/           POOR FIT decisions (archived automatically)
│   └── JDx_PREP/
│       ├── prom/             2_ats_prompt.txt, 3_ats_recommend_prompt.txt,
│       │                     4_ats_evidence_gap_prompt.txt, 5_cover_letter_prompt.txt
│       └── resp/             Paste Stage 2-4 Claude.ai responses here.
│                             Stage 5 also writes 5_synthesis_report.md and
│                             5_changes.json here (the decision log and the
│                             manifest the Stage 6 review UI renders) —
│                             both regenerable any time by re-running Stage 5,
│                             so neither is precious to lose.
├── policy/
│   ├── execution_policy.json Stage trust classifications (single source of truth)
│   └── policy_check.py       Enforcement layer
├── web/                      Local web UI (Flask, runs inside Docker)
│   ├── app.py
│   ├── templates/index.html
│   └── static/
├── scripts/
├── setup_first_time.sh
├── SETUP.md
├── run.sh
├── Dockerfile
└── docker-compose.yml
```

---

## How variant ranking works — three-file system

Stage 0/1 (variant ranking) uses three separate files uploaded together
to Claude.ai in one conversation. This eliminates regenerating 30-50KB of
identical variant content on every JD evaluation:

| File | Built by | When | Contains |
|---|---|---|---|
| `prompts/variant_bank.txt` | `build_variant_bank.sh` | Once (Step 0) | All resume variants |
| `prompts/variant_rank_prompt.txt` | `build_variant_bank.sh` | Once (Step 0) | Evaluation instructions |
| `output/JDx.md` | Pipeline (`run.sh`) | Once per JD | JD text |

Upload all three to Claude.ai in one conversation. No separate generation
step needed in Step 2 — the files already exist after Step 0 runs.

---

## Quick start

```bash
# 1. Add resume PDFs to input/pdf/, JD text files to input/other/JD1.txt etc.
# 2. Start the web UI
./scripts/serve.sh       # → http://localhost:5001

# Or command line:
./run.sh                 # route → clean → convert PDFs → chunk

# (One-time) Ingest career evidence corpus:
./scripts/ingest_evidence.sh

# Build the variant bank (once, after pipeline runs):
./scripts/build_variant_bank.sh
# → prompts/variant_bank.txt + prompts/variant_rank_prompt.txt

# Check all JDs are ready for Stage 0/1 upload:
./scripts/variant_rank.sh JD5

# Generate Stages 2-4 after pasting variant ranking responses:
./scripts/batch_prep.sh --continue
```

---

## Workflows

### Web UI (recommended)

```bash
./scripts/serve.sh
# → http://localhost:5001
```

Five guided steps:

- **Step 0 — Setup:** system check, run pipeline, build variant bank
  (one-time after adding resumes). Creates `prompts/variant_bank.txt` and
  `prompts/variant_rank_prompt.txt`.
- **Step 1 — Add JDs:** paste or upload JD text files
- **Step 2 — Rank Variants:** download all three files, upload to Claude.ai,
  paste response back
- **Step 3 — ATS Analysis:** generate Stages 2-4, upload each to Claude.ai,
  paste responses. Stage 3/4 generation is skipped automatically for a JD
  whose Stage 2 response already says `Shortlist: NO` (the cost gate — see
  ROADMAP.md) rather than generating prompts nobody should upload.
- **Step 4 — Review Board:** every JD with all three Stage 2-4 responses,
  grouped by what needs doing next rather than a flat status table:
  - 🟢 **Ready to review & merge** — click through to `/review/{JD}` (Stage
    5/6): approve or reject each candidate paraphrase edit and evidence-backed
    addition in a diff view, with a live word-count/page estimate, before
    anything is written to `output/review_resume/{JD}_new_resume.md`.
  - 🚫 **Gated — Stage 2 said NO** — Stage 5/6 refuses to synthesize a resume
    here at all (see "Why some JDs stop at Stage 2" below) unless overridden.
  - ⏳ **Still generating** — shows exactly which Stage 2-4 response is missing.
  - ⚪ **Poor fit (archived)** — Stage 0/1 rejects.

  Once a JD's resume is merged, its card gains two more controls, both gated
  behind a single checkbox ("Manual review completed — ready to create
  resume") so nothing downstream runs against a `.md` nobody has actually
  read:
  - **Review bullet trims (Stage 7)** — only relevant once a role has more
    than 5 bullets; scores every bullet in an over-cap role against the real
    JD text and proposes which ones to cut. Optional.
  - **Convert md to pdf resume** — deterministic reportlab render (see
    "Stage 5-7" below); prints the actual page count, not an estimate.

---

### Batch workflow (command line)

#### Step 1 — Add JDs and build variant bank

```bash
# Drop JD text files into input/other/:
#   JD1.txt, JD2.txt, ... (paste full JD text)

./run.sh
# → converts input/other/JDx.txt → output/JDx.md

./scripts/build_variant_bank.sh
# → prompts/variant_bank.txt       (all 21 variants)
# → prompts/variant_rank_prompt.txt (instructions)
# Run only once; rebuild when variants change: --rebuild flag
```

#### Step 2 — Human gate (Claude.ai) — Stage 0/1

```bash
# Verify files are ready for a JD:
./scripts/variant_rank.sh JD5
# → prints the three file paths + upload instructions
# → no file generation — files already exist from Step 1
```

Upload all three to Claude.ai in one conversation:
1. `prompts/variant_bank.txt`
2. `prompts/variant_rank_prompt.txt`
3. `output/JD5.md`

Paste Claude's response into:
`prompts/JD_Analysis/JD5/variant_rank_prompt_response.txt`

Claude's response will include **GOOD FIT / PARTIAL FIT / POOR FIT**,
a ranked table, and a **Top Pick** variant filename.

#### Step 3 — Generate Stages 2-4

```bash
./scripts/batch_prep.sh --continue
# → reads each JDx response, extracts chosen variant
# → POOR FIT JDs archived to JDx_NO/ automatically
# → for each qualifying JD:
#     Stage 1.5: prepare_variant.sh + smart_chunk.sh
#     Stage 2:   ats_optimize.sh   → 2_ats_prompt.txt
#     Stage 3:   ats_recommend.sh  → 3_ats_recommend_prompt.txt
#     Stage 4:   ats_evidence_gap.sh → 4_ats_evidence_gap_prompt.txt

# Single JD:
./scripts/batch_prep.sh --continue --jd JD5

# Force regenerate:
FORCE=1 ./scripts/batch_prep.sh --continue --jd JD5

# Status dashboard:
./scripts/batch_prep.sh --status
```

#### Step 4 — Upload Stages 2-4 to Claude.ai

Upload each file separately to Claude.ai:

| File | Stage | Save response as |
|---|---|---|
| `2_ats_prompt.txt` | ATS gap analysis | `resp/ats_prompt_response.txt` |
| `3_ats_recommend_prompt.txt` | Paraphrase edits | `resp/ats_recommend_prompt_response.txt` |
| `4_ats_evidence_gap_prompt.txt` | Evidence gaps | `resp/ats_evidence_gap_response.txt` |

Stages 2-4 can be uploaded in parallel across JDs — each is self-contained.

#### batch_prep.sh — all flags

```bash
./scripts/batch_prep.sh                        # Status dashboard (no action if no responses yet)
./scripts/batch_prep.sh --continue             # Stages 2-4 for all JDs with responses
./scripts/batch_prep.sh --continue --jd JD3   # Single JD
./scripts/batch_prep.sh --status              # Dashboard only
./scripts/batch_prep.sh --cover JD3           # Cover letter prompt for one JD
FORCE=1 ./scripts/batch_prep.sh --continue    # Force regenerate even if prompts exist
```

`--continue` skips Stage 3/4 *generation* for a JD when a Stage 2 response
already on disk says `Shortlist: NO` — nothing to upload, so nothing is
generated. Set `FORCE=1` to generate anyway (e.g. to re-check after editing
the resume). See ROADMAP.md for why this gate exists and where its
automated-execution counterpart lives.

---

### Stage 5-7 — Synthesize, review, and trim (deterministic, $0 every run)

Everything from here on makes **zero LLM calls**. Stages 2-4 already did
every judgment call that requires a model — what the gaps are, whether
evidence closes them, how confident that closure is. Stages 5-7 only parse,
score, and rewrite text using what Stages 2-4 already produced (Stage 5/6)
or the JD's own wording (Stage 7); no stage here asks a model anything.

```bash
# Stage 5 — apply the default policy (Stage 3 edits always, Stage 4
# HIGH-confidence insertions that land cleanly) directly from the CLI:
./scripts/synthesize_resume.py JD2

# Inspect every candidate change as JSON without writing anything —
# this is what the Stage 6 web review page calls under the hood:
./scripts/synthesize_resume.py JD2 --manifest-only

# Apply an exact, explicit set of approved changes instead of the default:
./scripts/synthesize_resume.py JD2 --apply-ids "p3-0,p3-4,p4-2" --force

# Stage 6 web review: http://localhost:5001/review/JD2
# → approve/reject each change in a diff view, live word-count/page
#   estimate, "Apply approved changes" writes output/review_resume/JD2_new_resume.md

# Stage 7 — score existing bullets in over-cap roles against the real JD text:
./scripts/trim_review.py JD2 --manifest-only
./scripts/trim_review.py JD2 --apply-ids "trim-1-0,trim-1-3"
# Stage 7 web review: http://localhost:5001/trim/JD2

# Deterministic resume → PDF (reportlab; auto-detects resume vs. generic
# markdown and renders each differently — see the script's own docstring):
./scripts/md_to_pdf.py output/review_resume/JD2_new_resume.md output/review_resume/JD2_new_resume.pdf
```

**Why a resume never gets auto-trimmed by ATS score.** It's tempting to
cut low-scoring bullets automatically, but "scores 0 against the JD's
literal keyword list" is not the same as "not worth keeping" — a strong,
evidence-backed bullet about a real platform migration can legitimately
score 0 just because it doesn't happen to contain one of the JD's specific
missing terms. Stage 7 surfaces a recommended cut list with reasons; a
human still clicks Apply.

**Why Stage 2's own "Recommended Improvements" never get auto-applied.**
Stage 2's Current/Improved quotes are often truncated fragments ("...
enterprise-scale..."), not the full sentence Stage 3 quotes verbatim —
there's no safe way to compute a find-and-replace boundary from a
fragment. Stage 6 shows Stage 2's alternative alongside the matching
Stage 3 edit for comparison (with its own ATS-relevance score) but never
turns it into a checkbox.

---

### Single resume, manual

```bash
./scripts/ats_optimize.sh "output/JD.md" "output/Resume.md"
# → prompts/ats_prompt.txt [upload to Claude.ai]

./scripts/cover_letter.sh "output/JD.md" "output/Resume.md"
# → prompts/cover_letter_prompt.txt [upload to Claude.ai]
./scripts/md_to_pdf.sh "output/cover/cover_letter.md"
# → output/cover/cover_letter.pdf
```

---

### Evidence corpus (one-time)

```bash
# Drop files into input/evidence/:
#   Career_Wealth.xlsx, project notes, PDFs

./scripts/ingest_evidence.sh
# → output/career_wealth_chunk/*.md
# Re-run when evidence files change (idempotent)
# Strips known scanner-app watermark noise (e.g. "Scanned by CamScanner")
# before chunking, both from the extraction-threshold check and final output.
```

---

### Ollama (local model execution)

```bash
./scripts/llm_execute.sh <prompt_file> <stage_key> <model>

# Example — Stage 2 (policy: local_allowed):
./scripts/llm_execute.sh prompts/ats_prompt.txt stage_2_ats_optimize deepseek-r1:14b

# Stage 0/1 requires --override (policy: manual_only — 3/3 models failed this):
./scripts/llm_execute.sh prompts/variant_rank_prompt.txt stage_0_1_variant_rank llama3.1:8b --override

# Check policy:
python3 policy/policy_check.py stage_2_ats_optimize

# Configurable timeout (default 1800s):
LLM_TIMEOUT_SECONDS=3600 ./scripts/llm_execute.sh ...
```

---

## Policy and execution trust

All rules in `policy/execution_policy.json`. Edit that file to change
system behavior — no script hardcodes rules.

| Stage | Script | Policy | Local trust |
|---|---|---|---|
| Stage 0/1 Variant Rank | variant_rank.sh | `manual_only` | — |
| Stage 1.5 Prepare | prepare_variant.sh | `local_always` | N/A (no LLM) |
| Stage 2 ATS Optimize | ats_optimize.sh | `local_allowed` | advisory |
| Stage 3 ATS Recommend | ats_recommend.sh | `manual_only`* | — |
| Stage 3.5 Evidence Gap | ats_evidence_gap.sh | `manual_only`* | — |
| Stage 5 Synthesize | synthesize_resume.py | `local_always` | N/A (no LLM — parses Stage 2-4's own already-reviewed output) |
| Stage 6 Review & Merge | web `/review/{JD}` | `local_always` | N/A (human approves every change; zero LLM) |
| Stage 7 Trim to Fit | trim_review.py | `local_always` | N/A (frequency-scores existing bullets against the real JD text; zero LLM) |
| Cover Letter | cover_letter.sh | `untested` | — |

\* Pro tier can run Stage 3/4 via API (Claude or Vertex/Gemini) with an
explicit override — the CLI/UI print the untested-model warning and require
acknowledgment before proceeding non-interactively. This is not a policy
downgrade: `execution_policy.json` still classifies these `manual_only`;
override is a per-run user decision, not a default.

**Why Stages 5-7 aren't on the trust spectrum above.** Every row above is
answering "should we trust a model to do this." Stages 5-7 aren't a trust
question at all — they never call a model. Stage 5/6 parse and apply the
judgments Stage 2-4 already made; Stage 7 counts word frequency against the
JD's own text. `local_always` here means something different from Stage
1.5's `local_always`: it's not "safe enough to skip human review," it's
"there's no model in the loop to have a trust level about." Human review
still happens — Stage 6's whole UI *is* that review — it just isn't gating
a model's output, it's gating what to keep from output a human is looking
at directly.

**Why manual_only for Stage 0/1 and Stage 3:** three models tested
(llama3:8b, llama3.1:8b, deepseek-r1:14b). All three failed Stage 0/1
(hallucinated variant names) and Stage 3 (formatting audits instead of
paraphrase edits). Stage 2 worked adequately on all three. Gemini has been
run against Stages 2/3/4 in production use (5 JDs, all three stages) with
no fabricated filenames observed in that sample — but unlike the local-model
tests above, this wasn't scored against a labeled correctness set. Treat as
"ran without an obvious failure," not "evidence-tested to the same bar."

---

## Known gotchas

**Clipboard corruption:** always upload prompt files as file attachments —
never paste. Clipboard corrupts em-dashes and smart quotes.

**Rebuild variant bank after adding resumes:** `build_variant_bank.sh`
compares mtimes and warns if any variant in `output/resume/` is newer than
the bank. Force rebuild: `./scripts/build_variant_bank.sh --rebuild`

**JD chunks in retrieval pool:** `batch_prep.sh` removes JD chunks from
`chunks/` automatically. Manual workflow: `rm -f chunks/<jd_basename>_part_*.md`

**FORCE=1 re-runs completed JDs:** without it, `--continue` skips JDs that
already have `4_ats_evidence_gap_prompt.txt`.

**Port 5001 blocked:** `lsof -i :5001` shows what's using it.

**gcloud token expiry:** run `gcloud auth application-default login` on
the Mac host when Stage 2 returns `Reauthentication is needed`.

**A code fix that "didn't work" is usually a deploy that didn't happen.**
`docker restart` re-runs whatever is already on disk *inside* the
container — it does not fetch new file content from anywhere. Two
different failure modes look identical from the browser (old behavior
after you were told it's fixed):
- **Bind mount** (check: `docker inspect <container> --format '{{json .Mounts}}'`
  shows a `"Type":"bind"` entry mapping your project folder to `/app`) —
  the host file *is* the container file. If a fix doesn't appear, the
  file on your **host** was never actually overwritten; verify with
  `grep` or `wc -l` run directly on the host path, not through
  `docker exec`. Once the host file is correct, `docker restart` is
  genuinely enough — no rebuild needed.
- **Image-baked** (no bind mount; `Dockerfile` uses `COPY`) — the file
  is frozen into the image at build time. No restart, however many times
  repeated, will ever see a host-side edit. This needs
  `docker compose build` (or `--no-cache` if layer caching is stale)
  followed by `up -d`.
Confirming which situation applies *before* debugging further saves
real time — a stale file and a stale image look the same from a
browser tab but need entirely different fixes.

**LLM output format can drift between runs of the same prompt.** Stage
3/4 responses have, in real sessions, changed heading case
(`# Phase B` vs `# PHASE B`), dropped quote-wrapping around
Current/Paraphrase text, and swapped blockquote (`>`) markers for plain
dash bullets under "Proposed Addition to Resume" — all with an unchanged
prompt template. Stage 5's parsers were made tolerant of every variant
observed so far, but this list is not guaranteed exhaustive: if a JD's
Review Board shows "No candidate changes parsed" or a suspicious "0
changes" despite real Stage 3/4 content on disk, check whether the raw
response actually matches the expected heading/quote shape before
assuming the resume is genuinely edit-free.


## Evidence checks and JD status

The review page can check each proposed change against your own evidence files (numbers, do-not-claim rules, titles, dates, skills).
See [docs/EVIDENCE_PIPELINE.md](docs/EVIDENCE_PIPELINE.md). Quick start: put role/project Markdown files in `input/evidence/`, run
`scripts/ingest_evidence.sh`, then `python3 scripts/jd_status.py` to see which JDs are ready to review or need a stage re-run. Your own
employers, claims and name go in `policy/evidence_policy.local.json` (git-ignored); `policy/evidence_policy.json` holds shared defaults.
