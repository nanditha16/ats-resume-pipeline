# Evidence pipeline

The review page (Stage 5/6) can check every proposed resume change against *your own evidence files*, so a reworded or newly added
line is only offered when something you wrote down supports it. This document describes what is read, what is checked, and how to
configure it. Nothing here calls a model: the checks are deterministic and run when the page loads.

## Evidence files

Put one Markdown file per role or project in `input/evidence/` (a LinkedIn export may sit beside them). `scripts/ingest_evidence.sh`
converts them to `output/evidence/` and builds `output/evidence/evidence_index.json`; it is incremental (hash sidecars) and prints a
diff of what changed. The index is built from **tables**, not headings:

| Table | Columns used | Becomes |
|---|---|---|
| Number Ledger | label, value, notes | verified numbers (a row's own wording gives the number its meaning) |
| Do-Not-Claim | claim, why, say instead | rules a resume line must not trip |
| Approved Claim Copy | bullets or italic paragraphs | vetted wording, also verified |
| `Title (Resume & Public Profiles)` row | options separated by ` / `, optional `(Corporate Title: X)` | the titles you may use for that role |
| `Dates` row, or "working dates ...", or "records dating from at least ..." | `Mon YYYY to Mon YYYY` or `present` | tenure the evidence states |

A number is **verified** only if it is in the ledger or approved copy *and the surrounding words agree* (a row must share a distinctive
word or a companion number with the claim; a coincidental `35%` elsewhere does not count). Other numbers are classed `NARRATIVE`
(only in prose), `PROFILE_ONLY` (only on the LinkedIn file, which is never sent to a model), `CONTEXT_MISMATCH` (the number exists, but for
something else) or `NOT_FOUND`.

## Commands

| Command | Purpose |
|---|---|
| `scripts/ingest_evidence.sh` | convert evidence files and rebuild the index (also the page's "Refresh evidence") |
| `python3 scripts/evidence_index.py audit NAME` | audit one resume variant: unbacked numbers, do-not-claim lines |
| `python3 scripts/jd_status.py [JD...]` | one line per JD: ready to review, or which stage to re-run (read-only) |
| `python3 scripts/parse_diagnostics.py JD` | which labels a Stage 3/4 response actually used, and whether it was cut off |
| `python3 scripts/variant_pick.py RESPONSE.txt` | which variant a ranking response chose (fallback for `batch_prep.sh`) |

`jd_status.py` actions: `run Stage N` (no response yet), `re-run Stage 3/4` (cut off or unreadable), `re-run Stage 4 only` (written
before the evidence last changed; Stage 3 never reads the evidence), `skip` (Stage 2 verdict NO), `review`.

## What the page checks

| Check | Catches | Default |
|---|---|---|
| Numbers in context | a figure with no ledger or approved support, or support for something else | note; blocks a card that *adds* one |
| Do-not-claim rules | a line that trips a rule, scoped to the role the line sits under | blocks a card that adds one; **unticks a card that keeps one** (`untick_when_keeping_dnc`) |
| Phrase anchors | a distinctive phrase from a rule ("multi-agent", "zero outages") on any line, any role | same as above |
| New acronyms | an acronym the change adds that no resume or evidence file contains | blocks |
| Audience nouns | "hospital", "customer", "investor"... added to a role whose own evidence never names them | blocks |
| Title change | a reword that gives the opening title a new level word or people-management noun (Principal, Director, Manager) | blocks |
| Skills no evidence names | tools in the Technical Stack that appear in no evidence file; and cards that add one | variant-level list (information); blocks the card |
| Two roles in one bullet | a Stage 4 card citing two roles' evidence for a single-role placement | blocks |
| Dropped specifics | an acronym or number removed that exists nowhere else in the resume | note |
| Dates vs tenure | a role whose start is earlier, or end later, than the evidence states | information |
| Titles vs menu | a role title not on the evidence's title menu for that role | information (titles are a per-variant choice) |
| Stage 4 "Flags" | model-written conflicts, each checked: quotes verified word for word, "confirms" claims tested against the ledger, "does not appear" claims tested against the resume, "matches" date claims tested against tenure | shown with a verdict line |
| Overlap | a new line restating an existing one, with a hint when the *existing* line is the problem | note |
| Repeated wording | one JD phrase pasted into several cards | style note; kept separate from evidence flags in the Apply dialog |
| Staleness and truncation | a Stage 4 response older than the evidence, citing files that no longer exist, or cut off at the output limit | banner |

Stage 4 output is also cleaned before it becomes a card: placement instructions (`Add to ... (new bullet):`), wrapper emphasis and quotes are
stripped so they are never written into a resume, and an instruction to edit an existing line is listed instead of becoming a card.

## Configuration

`load_policy()` merges, in order: built-in defaults, `policy/evidence_policy.json` (shared, committed), then
`policy/evidence_policy.local.json` (**yours, git-ignored**). Put your employers, claims and name in the local file. Keys starting with `_` are comments.

| Key | Meaning |
|---|---|
| `role_map` | `[["regex of how a role is named in a resume header", "substring of that role's evidence file name"], ...]`; scopes rules to roles |
| `role_labels` | evidence-file substring to the display name on the page |
| `dnc_phrase_anchors` | distinctive phrases tied to a rule (`rule`: text in the rule's claim) or carrying their own `claim`/`why`/`file` |
| `untick_when_keeping_dnc` | unticks cards that rewrite a line still tripping a rule (default true) |
| `audience_terms`, `title_level_words`, `title_people_words` | word lists for the audience and title checks |
| `skill_aliases`, `skill_generic_words` | spellings of one tool (`postgresql`: `postgres`) and words that are not tool names |
| `variant_owner_prefix` | regex for the owner-name prefix on resume file names |
| `narrative_counts_as_verified`, `profile_counts_as_verified`, `dnc_unchecks_card`, `dnc_stem_len`, `generic_numbers` | matching strictness |

Example local file:

```json
{
  "role_map": [["acme corp|acme", "acme"], ["globex", "globex"]],
  "role_labels": {"acme": "Acme Corp", "globex": "Globex"},
  "variant_owner_prefix": "^Jane[_ ]+Doe[_ ]+(?:resume[_ ]*)?",
  "dnc_phrase_anchors": [{"pattern": "\\bmulti-agent\\b", "rule": "Multi-agent orchestration"}]
}
```

## How the checks were validated

Each check was measured on real review data before it was kept: run over every existing Stage 3 edit and count false alarms; compare
default-ticking rules with an independent reviewer's accept/reject decisions (unticking on kept do-not-claim lines agreed on 18 of 19
cards; unticking on unbacked numbers alone agreed on only 81%, so it is not done). Before delivering a change, build every JD's manifest with the
old and new code and diff the card text, flags and ticked state; only intended differences are acceptable.

## Limits

Plain words used for something that was not done ("led" for "coordinated"), a reworded verb, a count of one thing renamed as another,
and whether a quoted *title recommendation* suits you are judgement calls the checks cannot make. Roles with no evidence file are not
judged. Role scoping is by keyword (`role_map`). Dates and titles are compared only for roles whose evidence states them.
