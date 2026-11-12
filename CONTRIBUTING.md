# Contributing to ATS Pipeline

Thank you for trying this tool. This is an early-stage demo prototype — your feedback directly shapes what gets built.

---

## Quickest way to help: share your experience

**5 minutes:** Fill out the [feedback form](https://forms.gle/sw6kWLgJJe3f8nHUA)

It asks:
- Which steps worked smoothly
- Where you got stuck
- Which JD/resume combinations you tested
- Whether you'd use an automated tier (and what you'd pay)

---

## Reporting bugs

Open a [GitHub Issue](../../issues/new?template=bug_report.md) with:

1. What you were trying to do
2. What command you ran (or which UI button you clicked)
3. The exact error message (paste it, don't screenshot terminal text)
4. Your OS and Docker version (`docker --version`)

**Do NOT include:** your resume content, JD text, or any personal data in issues.

---

## Requesting features

Open a [GitHub Issue](../../issues/new?template=feature_request.md) with label `enhancement`.

Most useful format:
> "I wanted to [do X] but the tool [did Y instead / didn't support it]. My workaround was [Z]."

---

## What we're actively looking for feedback on

- **Setup experience:** Did `./setup_first_time.sh` and `./scripts/build_variant_bank.sh` work on your machine?
- **Three-file upload (Stage 0/1):** Was the Claude.ai upload flow clear? Did the fit verdict (GOOD/PARTIAL/POOR FIT) match your expectation?
- **Stage 2 quality:** How does the ATS gap analysis compare to what you'd write manually?
- **Evidence corpus (Stage 4):** Did the evidence gap prompt surface real experience you'd missed?
- **Web UI:** What's confusing or missing? Are the download buttons in Step 2 easy to find?
- **Cost:** If Stage 2 were automated at ~$0.001/JD (Gemini) or ~$0.03/JD (Claude), would you pay for that?

---

## Contributing code

This is a demo prototype. The codebase is intentionally simple (shell scripts + Python + Flask).

Before submitting a PR:
1. Open an issue first to discuss the change
2. Keep PRs focused — one change per PR
3. Test on macOS (primary target) and Linux if possible
4. Don't add new dependencies without discussion

Core design constraints that must be preserved:
- **Prompt-first:** every stage writes a prompt file — automation is always optional
- **Three-file system:** Stage 0/1 uses `variant_bank.txt` + `variant_rank_prompt.txt` + `output/JDx.md`; never merge these back into a monolithic file
- **Local-first:** no user data leaves the machine without explicit consent
- **Policy layer:** `policy/execution_policy.json` is the authoritative source for stage trust levels — no script hardcodes execution rules
- **No breaking changes to the manual workflow:** CLI users and web UI users must have identical capabilities
- **POSIX shell compatibility:** scripts must run under `/bin/sh` without `bash`-only features (`shopt`, bash arrays, `declare -A`); the web UI executes scripts via `bash -c` but scripts themselves must be portable

---

## Interest in Pro/Team tier

If you'd use an automated version with managed API access:
- [Interest form](https://forms.gle/sw6kWLgJJe3f8nHUA)
- Or open an issue with label `tier-feedback`

No commitment required.
