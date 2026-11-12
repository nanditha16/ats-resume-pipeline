# Writing Rules: Summary vs Highlights, and AI-pattern checks

Status: BUILT and tested 2026-10-30; role-metric framing added 2026-10-31.
Source of truth: `scripts/style_rules.py` (prompt text AND checks come from it).

## Why

Measured on the 12 final resumes in `output/review_resume/` (2026-10-30):

| Finding | Count |
|---------|-------|
| Summary repeats a Career Highlights metric | 12 / 12 (`$1B+` in all 12, `99.99%` in 10) |
| Summary length | 67 to 109 words, 3 to 4 sentences |
| "at scale" in Summary or Highlights | 12 / 12 |
| Resumes with em dashes | 5 (one with 6) |

Feedback adopted: Summary and Career Highlights must not overlap; the Summary
should hook, not list everything.

## Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| W1 | Summary: at most 2 sentences, 45 words. Positioning, then one hook. | Feedback; current Summaries run 67 to 109 words. |
| W2 | No metric in the Summary that also appears in Career Highlights. Highlights own the numbers. | 12/12 resumes repeat at least one. |
| W3 | Keywords are relocated to Highlights or Experience, never deleted. Stage 9 rescore shows any ATS cost. | Agreed mitigation for the risk that a shorter Summary drops keywords. |
| W4 | Prevent at generation: rules embedded in Stage 3, 4 and 8 prompts. | $0 incremental: those stages already call Claude. |
| W5 | Pattern list follows blader/humanizer's public categories (MIT): AI vocabulary, vague significance, "-ing" commentary, em dashes, rule of three. | Readable, auditable list; no model or dependency. |
| W6 | Rule of three is prompt-only, not checked. | Resume bullets legitimately list three tools or teams; a regex would mostly misfire. |
| W7 | No rewriting model (GoHumanize 4B, jialinyyzz 12B) and no Rust tool (llmstrip). | Rewriters can alter facts and drop keywords after human review; the Ollama route failed 3/3 earlier; Rust adds a toolchain for rules Python covers. |
| W8 | Prefer the candidate's own wording from the resume and cited evidence files. | Per the research shared on 2026-10-30, only text built from the writer's own sentences scored human. |
| W9 | Prompts fail loudly if the rules cannot load. | A prompt silently missing its rules would look normal and be wrong. |
| W10 | The Summary may carry ONE proof metric, provided Career Highlights does not repeat it. Years of experience are positioning, not proof. | Replaces "Highlights own all metrics", which overstated the rule. A number lives in one place only. |
| W11 | The proof is framed in the business metrics the JD's role type is judged on (table below), only with a real number from the resume or evidence. | Adopted role/metric table, 2026-10-31. Prevents reaching for metrics a role can't credibly claim (e.g. revenue growth for a TPM). |
| W12 | Role type wins over domain when classifying ("Engineering Manager - Platform" is engineering management; "Principal Product Manager, AI" is product). | The table is organised by role; the domain only decides when no role type is named. |
| W13 | Rule-of-three ban has one exception: Summary sentence 1 may name three strengths when each maps to one of the JD's top three capabilities. | Deliberate mapping to the JD, not a reflexive triplet. |

## Resume Summary vs TMAY

The same framework (decode the JD into 3 capabilities, Position, Proof) serves two artifacts:

| | Resume Summary | TMAY (spoken) |
|---|---|---|
| Reader | Recruiter, 6-second scan for alignment | Hiring manager, judging problem-solving |
| Length | 2 sentences, at most 45 words | about 150 to 200 words, 60 to 90 seconds |
| Position | Sentence 1: role, years, domain, strengths mapped to the JD | Same, plus why this role |
| Proof | ONE metric, not repeated in Highlights | Two stories: the decision you made and the business result |

Only the Summary is enforced by the tool. TMAY is prepared by hand from the same proof points.

## Role type -> business metrics (W11)

| Role type | Frame the proof as |
|-----------|--------------------|
| product management | revenue growth, product adoption, customer retention |
| technical program management | time to market, cost efficiency, risk reduction |
| engineering management | engineering productivity, platform reliability, cost efficiency |
| cloud and platform leadership | infrastructure cost optimization, availability, scalability |
| data and analytics leadership | revenue impact, decision efficiency, cost savings |
| AI/ML leadership | productivity gains, revenue impact, model-driven business value |
| security leadership | risk reduction, financial exposure avoided, compliance |
| IT and infrastructure leadership | operational cost, service reliability, business continuity |
| digital transformation | cost savings, productivity improvement, revenue enablement |
| supply chain technology | cost per shipment/order, on-time delivery, operational efficiency |
| unclassified title | a business outcome: time to market, cost, risk, reliability, revenue |

Classification on this snapshot's 28 JDs: 15 program, 5 engineering management, 3 product,
5 unclassified or missing a title. Used in: Stage 3 prompt, Stage 8 prompt, the Stage 6 Summary
banner, and `--check --jd`. Not in Stage 4, which never writes the Summary.

## What Stage 5 / Stage 6 now do

| Check | Level | Effect |
|-------|-------|--------|
| Summary over 45 words, over 2 sentences, more than ONE proof metric, shares a metric with Highlights, or uses a banned pattern | Document | Warning banner with the role's business metrics; printed after every save |
| Stage 4 insertion aimed at the Summary | Card | Unchecked by default, reason shown |
| Banned patterns the card ADDS (not ones already in the base resume) | Card | Badge on any hit; unchecked at 2 or more |
| Stage 3 edit that lengthens the Summary | Card | Note only (does not uncheck) |

The review page also now shows the reason for every flag built earlier (hedge
language, identity drift, repetition, ungrounded numbers, duplicates); before
this change those cards were unchecked with no explanation.

## Calibration (215 real cards, 2026-10-30)

- 192 cards: no pattern introduced
- 22 cards: 1 hit (17 are em dashes) -> badge only
- 1 card: 2 hits (JD17 p3-7: "foster" + trailing "-ing" commentary) -> unchecked

Regression against the untouched snapshot: every JD's card set and card text
identical; exactly two cards newly unchecked (JD13 p4-7 Summary insertion,
JD17 p3-7); none newly checked.

## Files

```
scripts/style_rules.py          NEW  rules, prompt text, checks; CLI --prompt / --check
scripts/synthesize_resume.py    CHG  card flags, summary report in manifest, check after save
scripts/ats_recommend.sh        CHG  Stage 3 prompt embeds rules (stage3)
scripts/ats_evidence_gap.sh     CHG  Stage 4 prompt embeds rules (stage4); "Summary" removed as a placement
scripts/cover_letter.sh         CHG  Stage 8 prompt embeds language rules only (cover)
web/templates/review.html       CHG  warnings banner
web/static/js/review.js         CHG  banner + per-card reasons for all flags
web/static/css/review.css       CHG  styles
docs/WRITING_RULES.md           NEW  this file
```

## Usage

```
python3 scripts/style_rules.py --check output/review_resume/JD13_new_resume.md --jd JD13
python3 scripts/style_rules.py --prompt stage3 --jd JD13
```

Tune limits in `style_rules.py`: `SUMMARY_MAX_WORDS`, `SUMMARY_MAX_SENTENCES`,
`CARD_UNCHECK_AT`, the `AI_VOCAB` list, and `ROLE_METRICS`.

## Role-metric test results (2026-10-31)

| Test | Result |
|------|--------|
| 28 JD titles classified | Correct by inspection; role type wins over domain |
| Stage 3 prompt, program / eng mgmt / unclassified JD | Correct metric row each; generic fallback for unclassified |
| Stage 4 prompt | No role line (by design) |
| Stage 8 prompt (JD13) | Opens proof with time to market, cost efficiency, risk reduction |
| One-proof-metric check on 12 final resumes | 10 flagged (2 to 3 metrics each); "13+ years" never counted |
| Compliant 2-sentence Summary with one unique metric | Passes |
| Regression vs snapshot | Card sets and text identical; still exactly 2 newly unchecked |
| Rules module missing | All three prompt scripts stop with a clear message |

Note: JD numbers were reused for a new batch in this snapshot (JD7 is now "Engineering
Manager - Platform", not the earlier Qualcomm role).

## Parser contract: why JD9 showed no cards, and what else it exposed (2026-11-03)

JD9's review page showed the Summary banner but no cards. Reproduced with JD9's own prompts and responses:
Stage 3 parsed 0 of 6 edits and Stage 4 parsed 1 of 5 recommendations (and that one had empty text).

**Root cause: the prompt and the parser disagreed.** The Stage 3 prompt asks for plain labels
(`Current: [exact quoted text]`); the parser required bold `**Current:**`. Earlier runs worked only because
the model happened to bold them. This run followed the prompt literally.

Fixes, each verified against real responses:

| Area | Problem found | Fix |
|------|---------------|-----|
| Stage 3 labels | plain `Current:` / backtick values / `### FILE:` not recognised | tolerant fallback parser; the original regex stays primary, so every older format parses exactly as before |
| Stage 4 recommendations | numbered `**1. Gap Addressed:**`, no `---` between them, text inline after `**Proposed Addition:**` | split at each Confidence line; accept numbered gap fields and inline text |
| Stage 4 `Phase B` heading | `# Evidence-Based Gap Resolution -- Phase B` never matched, so JD17's 6 recommendations never appeared | match any heading containing "Phase B" |
| Placement notes | `*Add bullet under "Employer A" experience block:*` glued to the proposed text (18 of 82 recommendations) | strip an italic span ending in `:*` with 5+ words after it; italicised bullets are left alone |
| **Quote decoration** | quotes wrapped in code fences (JD18, JD20), `> ` blockquote markers (JD13), or ending in `...` (JD14) could never be found in the resume | one `clean_quote()` for both parsers |
| Described edits | `Current: the full 5-sentence SUMMARY` and `Paraphrase: same items, reordered: ...` are descriptions, not text | resolvers that verify their own claim; the real before/after text appears in the diff |
| Unanchorable edits | the apply step skipped them silently while printing OK | every Stage 3 card is pre-checked against the resume; unanchorable ones are unchecked with a reason |
| Stage 4 "Flags" section | evidence-vs-resume conflicts (dates, unsupported figures) were never shown | parsed and shown in a banner on the review page |
| Evidence files | only backticked names were recognised | bare names too |

**The significant finding is not JD9.** Anchoring every real Stage 3 edit against its base resume showed that
saved resumes for **JD13, JD18 and JD20 contained none of their Stage 3 edits** (0 of 8, 16 and 9 applied),
JD14 lost 3 and JD24 lost 1, while the run still printed OK. After the fix (real apply, old vs new code):

| JD | Stage 3 applied / skipped, before | after |
|----|-----------------------------------|-------|
| JD13 | 0 / 8 | 8 / 0 |
| JD14 | 8 / 3 | 11 / 0 |
| JD18 | 0 / 16 | 16 / 0 |
| JD20 | 0 / 9 | 9 / 0 |
| JD24 | 12 / 1 | 12 / 0 |
| JD12, 16, 21, 22, 23 | unchanged | unchanged |

**Action for you:** those five JDs' saved resumes predate the fix. Re-open each review page and re-apply.

**JD9 result:** 11 cards (6 Stage 3 + 5 Stage 4). Applying the six Stage 3 edits plus one insertion gives
6 applied, 0 skipped; the saved Summary is 37 words, 2 sentences and passes every rule.

**How the two description edits are handled**
- Summary: the model said "5-sentence, about 75 words"; the real Summary is 4 sentences, 83 words. The section is
  unambiguous, so the edit is made applicable and the real before-text is shown, but the card is NOT pre-checked
  because the model's own counts disagree. You confirm it by ticking it.
- Certifications: rebuilt as a pure reorder only if every item is used exactly once and the words are identical.

**A regression I introduced and caught.** My first Stage 4 change made JD14 lose all 12 of its cards (a regex that
consumed one newline instead of all whitespace after a label). Found by comparing every JD against the untouched
snapshot before delivery; fixed, and JD14 is back to 27 cards. Lesson kept: that full comparison is the gate.

**Prompt wording.** Stage 3 now states the contract (Current copied verbatim even for a whole Summary, Paraphrase
is the complete replacement text, no ellipsis, fences or blockquotes). Stage 4 asks for unnumbered `**Gap
Addressed:**`, `---` between recommendations, and a final Flags section.

Diagnostics remain available: `python3 scripts/parse_diagnostics.py JD9`.

## Known issues, not fixed here

- Existing final resumes predate the rules; their Summaries need one manual
  rewrite each (or a Stage 3/4 re-run for that JD).
- JD25's chosen variant points at `Jane_Doe_CoverLetter_Initech`, a
  cover letter, so its Stage 5 cannot run. Re-pick the variant in Stage 0/1.
- The prompt templates themselves still use em dashes in their own headings;
  the explicit rule overrides them, but watch the first runs.
