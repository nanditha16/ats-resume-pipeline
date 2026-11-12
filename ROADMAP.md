# ATS Pipeline — Product Roadmap

## What this is

A local-first, privacy-preserving job application prep pipeline.
Your resume data and career documents never leave your machine.
AI evaluation happens via Claude.ai (manual upload) or API (automated tier).

---

## Architecture: three-file system (Stage 0/1)

Stage 0/1 variant ranking uses three files uploaded together to Claude.ai.
This was redesigned in v0.1.1 to eliminate regenerating 30–50KB of identical
variant content on every JD evaluation:

| File | Built when | Changes? |
|---|---|---|
| `prompts/variant_bank.txt` | Once (Step 0, after pipeline) | Only when resumes change |
| `prompts/variant_rank_prompt.txt` | Once (Step 0, after pipeline) | Only when evaluation criteria change |
| `output/JDx.md` | Pipeline run (per JD) | Unique per JD |

---

## Tiers

### 🆓 Free — Community Edition (available now)

**Who:** Anyone who can clone a GitHub repo and run Docker.

**What's included:**
- Full pipeline: Stages 0/1 through 4 prompt generation
- Local web UI at `http://localhost:5001`
- Three-file variant ranking system (variant bank + instructions + JD)
- Evidence corpus ingestion (career documents → semantic chunks)
- Batch processing for up to 25 JDs per run
- Fit verdict display (GOOD FIT / PARTIAL FIT / POOR FIT) in the web UI
- Manual Claude.ai upload/paste workflow — no API key needed
- All generated prompts saved locally in `prompts/`

**What's NOT included:** Automated API execution (you upload prompts to Claude.ai yourself)

---

### ⚡ Pro — Automated (coming soon, BYOK)

**Who:** Regular job seekers who want to skip the manual upload/paste loop for Stage 2.

**What's included:**
- Everything in Free
- Stage 2 (ATS evaluation) automated via your own API key
  - Supported: Google Vertex AI (Gemini 2.5 Flash), Anthropic Claude
- Cost transparency: per-call token usage shown in terminal
- Dashboard shows which stages are automated vs manual

**How:** Bring your own API key. You pay the LLM provider directly.
Key stays in `~/.markitdown-codespace/config.json` on your machine — we never see it.

**Pricing:** Free (you pay only your LLM API costs — ~$0.001/JD on Gemini, ~$0.03/JD on Claude)

---

### 🏢 Team — Managed (future)

**Who:** Career coaches, outplacement firms, recruiting teams processing multiple candidates.

**What's included:**
- Everything in Pro
- Managed API access (no BYOK required)
- Per-org billing dashboard
- Multi-candidate batch processing
- Stage 3 and Stage 4 automated
- Priority support

---

## Stage automation roadmap

| Stage | Free | Pro | Team |
|---|---|---|---|
| 0/1 Variant ranking | Three-file upload to Claude.ai | Three-file upload (human gate — required) | Three-file upload (human gate — required) |
| 2 ATS evaluation | Prompt only → Claude.ai | ✅ Automated (BYOK) | ✅ Automated (managed) |
| 3 Paraphrase edits | Prompt only → Claude.ai | ⚠️ Automated (BYOK, override-gated — untested for Gemini, spot-check required) | ✅ Automated (managed) |
| 4 Evidence gaps | Prompt only → Claude.ai | ⚠️ Automated (BYOK, override-gated — untested for Gemini, spot-check required) | ✅ Automated (managed) |
| 5 Synthesize (merge Stage 2-4 into a resume) | ✅ Automated, deterministic, $0 | ✅ Automated, deterministic, $0 | ✅ Automated, deterministic, $0 |
| 6 Review & merge (human approval UI) | ✅ Web UI, deterministic, $0 | ✅ Web UI, deterministic, $0 | ✅ Web UI, deterministic, $0 |
| 7 Trim to fit (bullet-count cap vs. real JD) | ✅ Web UI, deterministic, $0 | ✅ Web UI, deterministic, $0 | ✅ Web UI, deterministic, $0 |
| PDF conversion | ✅ Automated, deterministic, $0 | ✅ Automated, deterministic, $0 | ✅ Automated, deterministic, $0 |
| Cover letter | Prompt only → Claude.ai | Prompt only | ✅ Automated |

**Why Stages 5-7 and PDF conversion aren't tier-gated like Stages 0-4.**
Every other row's tier difference is about who's allowed to spend on an
LLM call on your behalf. Stages 5-7 and PDF conversion never call a model
at all — they parse Stage 2-4's already-produced output, score already-
written bullets against the JD's own text, and render markdown to PDF.
There's no cost or trust decision to gate by tier; withholding a $0,
zero-model feature behind a paywall would just be arbitrary. (This
replaces the previous roadmap's "5 PDF conversion: Manual docker exec /
Guided UI" and "6 Cover letter" numbering — PDF conversion is now
automated in all tiers via `scripts/md_to_pdf.py`, and cover letter has
moved to its own row without a stage number since it isn't part of the
resume-synthesis chain.)

**Why Stage 3/4 in Pro is override-gated, not a plain ✅:** the underlying
model (Gemini 2.5 Flash) has not been evidence-tested against this task
shape. Every local model tried on Stage 3/4 fabricated filenames/citations.
The UI shows this warning before every run and requires acknowledging it;
it does not block the automation, since a human still reviews the output
before acting on it.

**Why Stage 0/1 stays manual in all tiers:**
This is the go/no-go gate — a human should own the GOOD/PARTIAL/POOR FIT decision.
Three local models (llama3:8b, llama3.1:8b, deepseek-r1:14b) were all tested and
all failed (hallucinated variant names, wrong fit verdicts). This is a confirmed
capability ceiling, not a model-selection problem.

**Why Stage 3 is override-gated (not default-automated) in Pro:**
Stage 3 proposes edits to your resume. You must verify every change traces to real
experience before applying it. Local model testing confirmed all three models
produced formatting audits instead of paraphrase edits — wrong task entirely.
Gemini has been run against Stage 3/4 in production use (5 JDs, no fabricated
filenames observed in that sample) but not scored against a labeled
correctness set the way the local models above were — so Pro's automation
still requires acknowledging this risk each run rather than defaulting to it
silently. "Ran without an obvious failure" is not the same bar as "evidence-tested."

---

## Policy trust model

All execution trust rules live in `policy/execution_policy.json`:

| Stage | Policy | Local trust | Evidence basis |
|---|---|---|---|
| Stage 0/1 Variant Rank | `manual_only` | — | 3/3 models hallucinated variant names |
| Stage 1.5 Prepare | `local_always` | N/A | No LLM — deterministic file ops |
| Stage 2 ATS Optimize | `local_allowed` | advisory | 3/3 models: directionally OK, 2/3 fabricated one gap |
| Stage 3 ATS Recommend | `manual_only` | — | 3/3 models: produced formatting audits instead |
| Stage 4 Evidence Gap | `manual_only` | — | Not yet tested locally |
| Stage 5 Synthesize | `local_always` | N/A | No LLM — parses Stage 2-4's own output into a manifest |
| Stage 6 Review & Merge | `local_always` | N/A | No LLM — human approves each change directly |
| Stage 7 Trim to Fit | `local_always` | N/A | No LLM — frequency score against the real JD text |
| Cover Letter | `untested` | — | Not yet tested locally |

---

## Cost gate: Stage 2 verdict blocks Stage 3/4 spend automatically

Added after real usage showed the obvious gap: Stage 3 and Stage 4 (the
two most expensive stages — Stage 4 alone runs ~10x Stage 2's cost, see
the session cost example below) were being generated and, worse, run via
API even when Stage 2 had already said `Shortlist: NO` for that JD. A
structural mismatch that paraphrasing or evidence-mining cannot fix
doesn't become fixable by spending more on Stage 3/4 — the gate stops
that spend before it happens, in both places cost is actually incurred:

- **`claude_execute.py` / `vertex_execute.py`** (automated API execution):
  before calling the API for Stage 3 or 4, checks whether that JD's Stage
  2 response already says NO. If so, the call is skipped and logged, not
  attempted. Override with `--ignore-gate` if you have a specific reason
  to run it anyway.
- **`batch_prep.sh --continue`** (manual Claude.ai upload workflow): skips
  *generating* the Stage 3/4 prompt files at all for a JD whose Stage 2
  response already says NO — nothing sitting in `prompts/` inviting an
  upload that shouldn't happen. Set `FORCE=1` to generate anyway.

Both checks read the same `**Shortlist:** NO` (or equivalent bolded-phrase
variant — see "LLM output format drift" below) line from
`resp/ats_prompt_response.txt`; there's one definition of "gated," not two
that could disagree. Stage 5/6 also refuse to synthesize a resume for a
gated JD unless explicitly overridden — the gate is consistent across the
generation, execution, and synthesis layers, not just one of them.

---

## Architecture: the manifest pattern (Stage 6 and 7)

Both Stage 6 (review & merge) and Stage 7 (trim to fit) follow the same
three-part shape, and it's deliberate:

1. A script computes every **candidate change** as plain data (an id, the
   before/after text, a reason, a confidence/score, and a `default_*` flag
   for what a hands-off run would do) — `--manifest-only` on either script
   prints exactly this, writing nothing.
2. A web page renders that data as checkboxes, pre-checked to match the
   default policy, and lets a human override any of them before anything
   touches disk.
3. `--apply-ids` (CLI) or the page's Apply button (web) takes an **explicit**
   id list and applies precisely those changes — never more, never fewer
   than what was actually approved.

The web route and the CLI call the *same* Python functions for all three
steps. This isn't just tidiness: it means the web UI cannot silently drift
from what the command line does, because there is only one implementation
of "what counts as approved" to drift from.

**Why nothing here can auto-apply past a hard line.** Stage 5's default
policy will apply Stage 3 edits automatically (they're exact-quote
anchored rewords of existing facts — nothing new can sneak in) and Stage
4 insertions only above HIGH confidence with a located anchor. Stage 2's
own "Recommended Improvements" and Stage 7's trim candidates are never
in the default-applied set, regardless of how they score — a human has to
look and click, every time, for those two categories specifically.

## Known limitation: resume length management is heuristic, not exact

The default Stage 5 policy measurably grows resume length — accepting
every default in a real session pushed one resume from ~1,188 words to
~1,402 (+18%), because Stage 4 insertions are pure addition by definition
and even Stage 3's "reword" edits often add a clause rather than swap
words 1:1. Two design decisions follow from this, both deliberate:

- **The word/page estimate shown during review is a rough heuristic**
  (~550 words/page for a single-column technical resume at 10-11pt), not
  a real layout calculation. The actual page count only becomes exact
  once `md_to_pdf.py` renders it — the estimate exists to give a
  directional warning *before* that point, not to replace checking the
  real PDF.
- **Trimming is never automated by ATS-keyword score**, even though that
  would be the obvious lever. Tested against real data: two of the
  strongest, most evidence-backed insertions in one session (a platform
  migration bullet, a regulatory remediation bullet) both scored 0 on literal
  keyword match against that JD's missing-keywords list, simply because
  neither happened to contain one of the JD's specific missing terms —
  while a much weaker, generic skills-line addition scored positively
  just for containing the right phrase. Auto-trimming by that score would
  have cut the two strongest bullets in the resume and kept the weakest.
  Stage 7 surfaces the score as *information*, and a human still decides.

## Known limitation: Stage 7's JD-relevance score is frequency-based, not semantic

Stage 7 extracts keywords from the JD's own text by counting word and
bigram frequency — no external list, no model call. This means it can't
recognize that "orchestrated" and "coordinated" mean roughly the same
thing: if a JD says "coordinate" ten times and never "orchestrate," a
bullet using "orchestrated" will score as less relevant than one using
"coordinated," regardless of which describes the stronger achievement.

This is a real, acknowledged gap, not a rounding error — and it's
acceptable specifically *because* Stage 7 never auto-applies. The
manifest pattern above (every candidate shown with its score and reason,
nothing deleted without an explicit click, a browser confirmation on top
of that) is what makes an imperfect ranking safe to ship, not the
existence of Stage 2-4's more careful semantic work upstream — those are
a different task (generating correct new text) from this one (ranking
already-true text), and solving the first doesn't protect against
mistakes in the second. The worst case of a wrong ranking is keeping a
slightly weaker bullet than optimal, not a false claim reaching the
resume — that's the actual safety margin, and it comes from the review
gate, not from anything upstream.

## Known limitation: LLM output format drift within Stage 3/4

Three concrete, real-session bugs, all with the same root cause: Claude's
own output shape for Stage 3/4 varied between runs despite an unchanged
prompt template, and the original parser was written against only the
first-observed shape.

1. **Case-sensitive section matching.** One run wrote
   `# PHASE B — NEW MATERIAL RECOMMENDATIONS` (all-caps); the parser
   searched for `# Phase B` (mixed case) using a case-sensitive match and
   silently found zero Stage 4 candidates — no error, no warning, just an
   empty result that looked like "no gaps found."
2. **Optional vs. mandatory quote-wrapping.** Every prior run quoted
   Stage 3's Current/Paraphrase text in `"..."`; one run dropped the
   quotes entirely and used a bare markdown line-break instead. A
   quote-mandatory parser matched zero edits for that entire response.
3. **Blockquote vs. dash-bullet content markers.** Stage 4's "Proposed
   Addition to Resume" field was expected as `> quoted` lines; one run
   used a `- dash bullet` under a bolded sub-label instead. Gap, evidence,
   and confidence all parsed correctly — only the actual proposed text
   came back empty, producing a real but content-free candidate card.

All three are fixed by making the parser tolerant of the specific variant
observed. **This list is not guaranteed exhaustive.** If a JD's Stage 6
review board ever shows suspiciously few or zero candidates despite real
Stage 3/4 content existing on disk, check the raw response's actual
heading/quote shape before concluding the resume is genuinely gap-free —
this has been the real cause twice already, in two different runs.

---

## Timeline

- **v0.1 (released):** Free tier — full pipeline, web UI, manual workflow.
- **v0.1.1 (released):** Split-prompt architecture — three-file Stage 0/1 system; variant bank built once; `output/JDx.md` used directly (no redundant `jd_current.txt`); POSIX shell compatibility fixes; fit verdict display in web UI; `extract_variant` extended for new response format.
- **v0.1.2 (released):** Bug fix pass — see changelog above. Stage 2 API automation (Claude + Vertex/Gemini) shipped; Stage 3/4 API automation added with explicit untested-model warnings (override-gated, not default); per-JD cost display; duplicate response file cleanup; readiness-check archive fallback.
- **v0.2 (released):** Stages 5-7 shipped — deterministic resume synthesis
  (`synthesize_resume.py`), a web review/approve/apply UI for both merging
  (Stage 6) and bullet trimming (Stage 7), and a resume-aware deterministic
  PDF renderer (`md_to_pdf.py`, reportlab). Stage 3/4 cost gate added in
  both the automated (`claude_execute.py --ignore-gate` to override) and
  manual (`batch_prep.sh`, `FORCE=1` to override) execution paths. `output/
  review_resume/` introduced as the home for finalized resumes, separate
  from the untouched variant bank in `output/resume/`. Three LLM
  output-format-drift bugs found and fixed in Stage 3/4 parsing (see
  "Known limitation: LLM output format drift" above).
- **v0.3 (next):** Pro tier settings UI polish (BYOK key management currently
  file-based); evidence-basis testing for Gemini on Stage 3/4 to potentially
  reclassify from `manual_only` if results warrant it.
- **v0.4 (future):** Team tier — managed API, multi-candidate, Stage 3/4 automation.

---

## v0.1.2 — Bug fixes from field testing 

All items below were reported during real usage and are now fixed. Grouped
by root cause, not by report order.

**Container/execution bugs**
1. **Evidence refresh failed** — `ingest_evidence.sh: line 31: docker: command not found`.
   RCA: `app.py` runs inside the `markitdown` container and sets `INSIDE_DOCKER=1`
   when calling the script, but the script never read that variable — it always
   shelled out to `docker exec markitdown`, which doesn't exist inside the
   container itself. Fixed: added `INSIDE_DOCKER` detection (honors the env var
   plus `/.dockerenv` as fallback) and `drun`/`drun_i` wrappers used across all
   10 call sites. *Risk: assumes `INSIDE_DOCKER=1` is only ever set from within
   the actual container — true for current call sites.*
2. **`router.sh`'s `drun_bash` self-recursion bug** — the "not inside docker"
   branch called itself instead of `docker exec markitdown bash -c`, which
   would have infinite-looped or errored the first time it was exercised from
   the host. Fixed.

**UX gaps**
3. **Resume filenames with spaces break Stage 3/4 prompt generation.** Added
   a warning in the Step 0 UI next to `input/pdf/` — documentation only, no
   automatic sanitization (avoids silently renaming files users expect to find
   under their original name).
4. **No way to re-run the pipeline after adding a JD without returning to
   Step 0.** Added a "Convert JDs → output/JDx.md" button directly on Step 1
   and a "Convert new JDs" button on Step 2 (2a), both calling the same
   `/api/run-pipeline` route Step 0 uses.
5. **JD5's Step 2 card didn't render** even though its ranking succeeded. RCA:
   `poor_fit` and the displayed `fit_verdict` were computed independently —
   a response mentioning "POOR FIT" for one variant among many (not the
   overall verdict) tripped `poor_fit` while `fit_verdict` still showed
   GOOD/PARTIAL, and the card was silently filtered out. Fixed: `poor_fit`
   now derives from `fit_verdict`, so they can't disagree.

**Data duplication**
6. **`variant_rank_prompt_response.txt` was written to two locations**
   (`JD_Analysis/JDx/` and `JDx_PREP/resp/`). `batch_prep.sh` no longer copies
   it into `PREP/resp/`; `JD_Analysis/JDx/` is the single source of truth.
   Old copies from prior runs are dead weight — safe to delete manually.
7. **Step 2 readiness check was live-path-only.** `prepare_variant.sh` moves
   every non-current JD's `output/{jd}.md` into `output/_archive/` to prevent
   `smart_chunk.sh` from mixing variants — so after a batch run, only the
   last-processed JD showed as "ready." Fixed: readiness (and the download
   link) now falls back to `output/_archive/{jd}.md` when the live file is
   archived.

**Stage 2/3/4 API automation (new capability, not just a fix)**
8. **No way to view/download a saved response after running Stage 2 via
   API.** The button was wired to the *prompt* file regardless of state.
   Fixed: added a "View response" button when a response exists.
   — **Stage 3 and 4 API automation added** (previously prompt-file-only in
   Free/Pro per the table below). Both Claude and Vertex/Gemini backends now
   support `--stages 2,3,4`. Vertex extension is **explicitly untested** —
   `execution_policy.json` marks Stage 3/4 `manual_only` because every local
   model tried fabricated filenames/citations on this task shape; Gemini
   hasn't been evidence-tested against it either. UI shows this warning
   before every Stage 3/4 API run and requires it to proceed non-interactively
   without hanging (fixed a separate bug where the confirmation prompt blocked
   forever when invoked from the web UI's non-interactive subprocess).
   — **Per-stage and per-JD cost now displayed** in the UI, parsed from the
   `Cost: ~$X.XXXXX` header each API response already wrote to disk.

**Evidence ingestion noise**
9. **CamScanner watermark polluting every evidence prompt.** A scanned PDF
   in `input/evidence/` had "Scanned by CamScanner" stamped on every page —
   real embedded text, not part of the image, so it survived extraction
   verbatim (37 occurrences in one file). Two-part fix: (a) strip the
   watermark line before measuring the pdfplumber OK/WEAK threshold — a
   watermark-only scan was clearing the 200-char threshold on noise alone
   and skipping the OCR fallback that should have run; (b) strip it again
   post-chunking, since OCR re-introduces it from the visible page image.

## v0.2 — Bug fixes and field-testing findings

**Deployment confusion (not a code bug, but cost real debugging time)**
1. **A correct code fix appeared to "not work" — the container was never
   updated.** `docker restart` reloads whatever is already on disk inside
   the container; it does not fetch new file content from anywhere. Two
   different root causes produce the identical symptom (old behavior
   persisting after a fix is applied): a bind-mounted project directory
   where the *host* file was never actually overwritten (verify with
   `grep`/`wc -l` run directly on the host path — running the same check
   through `docker exec` will show the same stale content and can look
   like independent confirmation when it isn't), or a non-bind-mounted
   image where the file is baked in via `COPY` and needs
   `docker compose build`, not just `restart`.
   `docker inspect <container> --format '{{json .Mounts}}'` distinguishes
   the two immediately — check this first, not after several rounds of
   "still broken."
2. **A response's own printed log is more trustworthy than a UI banner
   built from a separate hardcoded string.** During the same deployment
   confusion, a success banner's *header* (built from a string already
   fixed in `app.py`) claimed the correct new save path while the *log
   block beneath it* (Stage 5's own `print()` output, from a still-stale
   `synthesize_resume.py`) printed the old one — proof the wrong file was
   actually running, visible in the same screenshot, missed on first read.
   When a UI's summary and its own subprocess log disagree, trust the log.

See "Known limitation: LLM output format drift" above for the three
Stage 3/4 parsing bugs found and fixed this cycle.

**Session cost Example:** 
Claude (claude-sonnet-4-5):
Stage 2: 0.03593+0.03324+0.03376+0.03666+0.03708 = $0.17667
Stage 3: 0.11604+0.09957+0.11257+0.11846+0.10138 = $0.54802
Stage 4: 0.46027+0.45805+0.42910+0.45497+0.43892 = $2.24131
Claude total: $2.96600

Gemini (gemini-2.5-flash):
Stage 2: not run (no Gemini-suffixed Stage 2 file exists)
Stage 3: 0.00049+0.00077+0.00052+0.00053+0.00048 = $0.00279
Stage 4: 0.00901+0.00903+0.00907+0.00906+0.00906 = $0.04523
Gemini total: $0.04802

Grand total (both providers, all stages, 5 JDs each): $3.01402

Stages 5-7 (synthesize, review & merge, trim to fit) and PDF conversion,
run against all 5 of those same JDs: **$0.00** — no LLM call exists in
any of the four. The cost gate above (Stage 2 verdict blocking Stage 3/4
spend) is the other lever on this total: any JD in this batch that came
back `Shortlist: NO` at Stage 2 would have had its Stage 3 (~$0.10-0.55
per provider) and Stage 4 (~$0.045-0.46) spend skipped entirely rather
than added to this total.

---

## Feedback

- **General feedback:** [GitHub Discussions](../../discussions)
- **Bug reports:** [GitHub Issues](../../issues)
- **Feature requests:** [GitHub Issues](../../issues) with label `enhancement`
- **Interest in Pro/Team:** [Interest form](https://forms.gle/sw6kWLgJJe3f8nHUA)

Your feedback directly shapes what gets built next.
