// web/static/js/review.js — Stage 6 diff-review page.
//
// Loads the candidate-change manifest from /api/stage6-manifest (computed
// by scripts/synthesize_resume.py --manifest-only -- this file never
// computes a diff or an accept/reject decision itself, it only renders
// what that script already decided and lets the user override it), tracks
// checkbox state client-side, and POSTs the approved id list to
// /api/stage6-apply on demand.

let currentJD = null;
let currentChanges = [];
let baseWordCount = 0;
const WORDS_PER_PAGE = 550; // rough heuristic -- see updateAcceptedCount() note

async function initReview(jd) {
  currentJD = jd;
  const list = document.getElementById("changeList");
  const meta = document.getElementById("reviewMeta");

  let data;
  try {
    const res = await fetch(`/api/stage6-manifest?jd=${encodeURIComponent(jd)}`);
    data = await res.json();
  } catch (e) {
    list.innerHTML = `<p class="empty-state">Failed to load: ${e}</p>`;
    return;
  }

  if (!data.ok) {
    meta.textContent = "Error";
    list.innerHTML = `<p class="empty-state">${escapeHtml(data.error || "Unknown error")}</p>`;
    return;
  }

  meta.textContent = `Variant: ${data.variant} · Stage 2 score ${data.s2.score}/100 · Verdict ${data.s2.verdict}`;
  baseWordCount = data.base_word_count || 0;

  if (data.gated) {
    const banner = document.getElementById("gatedBanner");
    banner.style.display = "block";
    banner.innerHTML = `
      <strong>Stage 5/6 gated — Stage 2 verdict is NO.</strong>
      <div class="gate-reasoning">${escapeHtml(data.s2.reason)}</div>
      <div class="gate-reasoning">Paraphrasing or adding evidence-backed bullets can't fix a structural
      mismatch. No candidate changes were computed for this JD.</div>`;
    list.innerHTML = "";
    document.getElementById("applyBtn").disabled = true;
    return;
  }

  currentChanges = data.changes;
  window.lastManifest = data;
  renderWarnings(data);
  renderChanges();
}

// Batch- and document-level warnings from Stage 5. Shown above the cards so
// they're read before any single card is judged.
function renderWarnings(data) {
  const items = [];
  // Parse problems first: a truncated or reformatted Stage 3/4 response means
  // cards are missing, and nothing else on the page is trustworthy until
  // that is known. Shown even when some cards did parse.
  const dg = data.diagnostics;
  if (dg && dg.problems && dg.problems.length && !(dg.verdict === "NO")) {
    items.push(`<div><strong>Stage 3/4 response problem</strong> (parsed ${dg.edits_parsed} edit(s) and
      ${dg.recs_parsed} recommendation(s)):<ul>${dg.problems.map(p => `<li>${escapeHtml(p)}</li>`).join("")}</ul>
      <div class="warn-hint">Run <code>python3 scripts/parse_diagnostics.py ${escapeHtml(dg.jd)}</code> to see the labels the
      response actually uses. If the stage was truncated or empty, re-run it for this JD.</div></div>`);
  }
  // Gaps where Stage 4 itself said nothing needs adding: shown as a note, never as a card that could be applied.
  if (data.stage4_notes && data.stage4_notes.length) {
    const covered = data.stage4_notes.filter(n => n.kind !== "edit"), edits = data.stage4_notes.filter(n => n.kind === "edit");
    if (covered.length) items.push(`<div><strong>Stage 4 found nothing to add for ${covered.length} gap(s)</strong> (already covered, so no card):
      <ul>${covered.map(n => `<li>${escapeHtml(n.gap || "")}</li>`).join("")}</ul></div>`);
    if (edits.length) items.push(`<div><strong>Stage 4 suggested editing an existing line instead of adding text</strong> (no card; do it by hand):
      <ul>${edits.map(n => `<li>${escapeHtml(n.gap || "")}: <span class="warn-hint">${escapeHtml(n.text || "")}</span></li>`).join("")}</ul></div>`);
  }
  // A Stage 4 run on evidence that has since changed: say so before anything else on the page.
  const st = data.evidence_staleness;
  const stale = !!(st && st.stale);
  if (stale) {
    const why = [];
    if (st.response_older_than_evidence) why.push(`the Stage 4 response is from ${escapeHtml(st.response_at)} but your evidence last changed ${escapeHtml(st.evidence_changed_at)}`);
    if (st.missing_count) why.push(`it cites ${st.missing_count} of ${st.cited} file(s) that no longer exist: ${st.missing.map(escapeHtml).join(", ")}`);
    items.push(`<div><strong>Stage 4 was run on older evidence.</strong> ${why.join("; ")}.
      Its recommendations and the "conflicts" list below may describe evidence you have since changed.
      <div class="warn-hint">Re-run Stage 4 for this JD (it will now send your current files), then reload this page.
      Stage 3 edits do not use the evidence and are unaffected. The "This resume vs your evidence" check is always current.</div></div>`);
  }
  // Conflicts Stage 4 itself found between the evidence corpus and the resume
  // (dates, unsupported figures, do-not-claim wording). Verify before submitting.
  // Dates the resume claims beyond what the evidence states (deterministic, so it does not depend on which conflicts a
  // given Stage 4 run chooses to mention).
  if (data.date_conflicts && data.date_conflicts.length) {
    items.push(`<div><strong>Dates that claim more than your evidence supports:</strong><ul>${data.date_conflicts.map(d =>
      `<li><strong>${escapeHtml(d.role || d.file)}</strong>: your resume says <strong>${escapeHtml(d.resume)}</strong>; ${escapeHtml(d.file)} says
      ${escapeHtml(d.evidence)}.</li>`).join("")}</ul></div>`);
  }
  // Skills this variant names that no evidence file mentions: information, since a real tool may just not be written down.
  if (data.unverified_skills && data.unverified_skills.length) {
    items.push(`<div><strong>${data.unverified_skills.length} skill(s) named in this variant appear in none of your evidence files</strong> (your LinkedIn profile included):
      <br><span class="warn-hint">${data.unverified_skills.map(escapeHtml).join(", ")}</span>
      <br><span class="warn-hint">Not necessarily false: add each one you have used to your evidence, and remove the rest. Employers that screen with AI compare your resume with your profile.</span></div>`);
  }
  // Titles are a per-variant choice from the menu in each evidence file's title row. Shown as information, never as a task.
  if (data.titles && data.titles.length) {
    items.push(`<div><strong>Role titles vs your evidence</strong> (information: each variant may pick any listed title):<ul>${data.titles.map(t => {
      const menu = t.options.concat(t.corporate.length ? ["corporate title: " + t.corporate.join(" / ")] : []).map(escapeHtml).join("; ");
      const what = t.status === "not listed"
        ? `is not one of the titles your evidence lists (closest: ${escapeHtml(t.closest)})`
        : `is the corporate title in your evidence, which also states the official HR title is ${escapeHtml((t.official || ["different"])[0])}`;
      return `<li><strong>${escapeHtml(t.role)}</strong>: your resume says &ldquo;${escapeHtml(t.resume_title)}&rdquo;, which ${what}.
        <br><span class="warn-hint">${escapeHtml(t.file)} lists: ${menu}</span></li>`;
    }).join("")}</ul></div>`);
  }
  if (data.stage4_flags && data.stage4_flags.length) {
    const none = data.stage4_flags.length && /^\W*no (?:material |significant )?(?:conflicts?|discrepanc)/i.test(typeof data.stage4_flags[0] === "string" ? data.stage4_flags[0] : data.stage4_flags[0].text);
    items.push(`<div><strong>${none ? "Stage 4 reported no material conflicts. Its notes, checked against your files:" : "Stage 4 found conflicts between your evidence and the resume" + (stale ? " (from a run on older evidence; re-check each)" : "") + ". Verify before submitting:"}</strong>
      <ul>${data.stage4_flags.map(f => {
        const text = typeof f === "string" ? f : f.text, checks = typeof f === "string" ? [] : (f.checks || []);
        return `<li>${escapeHtml(text)}${checks.map(c => `<br><span class="warn-hint">${/not found|does not back|words; it is|but it does|not a conflict|your resume's/.test(c) ? "Check: " : ""}${escapeHtml(c)}</span>`).join("")}</li>`;
      }).join("")}</ul>
      <div class="warn-hint">Model-written, then checked: quotes are looked up in your files, and any figure it says the evidence confirms is checked against your ledger.</div></div>`);
  }
  // The whole resume against the evidence: shown once, because a base variant's own claims
  // are not the fault of any single card.
  const au = data.evidence_audit;
  if (au && au.indexed === false) {
    items.push(`<div><strong>Evidence index not built.</strong> Numbers and do-not-claim rules were not checked.
      Run <code>./scripts/ingest_evidence.sh</code>.</div>`);
  } else if (au && (au.unbacked.length || au.dnc_total)) {
    const nums = au.unbacked.slice(0, 16).map(m => `<li><strong>${escapeHtml(m.raw)}</strong> (x${m.count}): ` +
      `${m.status === "CONTEXT_MISMATCH"
        ? `your evidence has ${escapeHtml(m.raw)} only for "${escapeHtml((m.found_in[0] || [])[1] || "something else")}", not for this claim`
        : {NARRATIVE: "only in narrative text, not in a ledger", PROFILE_ONLY: "only on your LinkedIn profile", NOT_FOUND: "in no evidence file"}[m.status]}</li>`).join("");
    const groups = au.dnc_groups || [];
    const dnc = groups.slice(0, 5).map(g => `<li>[${escapeHtml(g.file)}] "${escapeHtml(g.claim)}" <strong>(${g.count} line${g.count > 1 ? "s" : ""})</strong>
      <br><span class="warn-hint">e.g. "${escapeHtml(g.example.slice(0, 130))}"</span>
      ${g.say_instead ? `<br><span class="warn-hint">Say instead: ${escapeHtml(g.say_instead)}</span>` : ""}</li>`).join("");
    items.push(`<div><strong>This resume vs your evidence:</strong> ${au.unbacked.length} number(s) not backed by a ledger;
      ${au.dnc_total} line(s) trip ${(au.dnc_groups || []).length} do-not-claim rule(s).<ul>${nums}${dnc}</ul>
      <div class="warn-hint">These are in the base variant, not caused by the cards below. Fix them at the variant, or add the
      figure to the Number Ledger if it is true. Evidence index built ${escapeHtml(au.built_at || "")}.</div></div>`);
  }
  // Verified numbers the Summary could use as its ONE proof, per business metric this role is judged on.
  const sp = data.summary_proof;
  if (sp && sp.indexed && sp.metrics) {
    const rows = sp.metrics.map(bm => {
      const got = (sp.candidates || []).filter(c => c.for === bm);
      if (!got.length) return `<li><strong>${escapeHtml(bm)}</strong>: <em>no verified number in your evidence. Do not claim one.</em></li>`;
      return `<li><strong>${escapeHtml(bm)}</strong>:<ul>${got.map(c => `<li><strong>${escapeHtml(c.value)}</strong>
        ${escapeHtml(c.text || c.metric)}${c.in_highlights ? " <em>(already in your Career Highlights, so the Summary should not repeat it)</em>" : ""}
        <span class="warn-hint">[${escapeHtml(c.file)}]</span></li>`).join("")}</ul></li>`;
    }).join("");
    items.push(`<div><strong>Summary proof: pick ONE verified number</strong> (${escapeHtml(sp.role || "this role")} is judged on these):
      <ul>${rows}</ul><div class="warn-hint">Only achieved results are listed; targets and baselines are skipped. The Summary takes one proof and
      Career Highlights keeps the rest.</div></div>`);
  }
  const sm = data.summary;
  if (sm && sm.after_defaults && sm.after_defaults.length) {
    const words = sm.base_words === sm.after_defaults_words
      ? `${sm.base_words} words` : `${sm.base_words} → ${sm.after_defaults_words} words with checked changes`;
    items.push(`<div><strong>Summary</strong> (${words}; limit ${sm.limits.words} words, ${sm.limits.sentences} sentences):
      <ul>${sm.after_defaults.map(w => `<li>${escapeHtml(w.message)}</li>`).join("")}</ul>
      <div class="warn-hint">Fix by hand after applying: sentence 1 positions you (role, years, domain, strengths
      mapped to the JD); sentence 2 is ONE proof${sm.role && sm.role.family ? ` framed as a ${escapeHtml(sm.role.label)} outcome:
      <strong>${sm.role.metrics.map(escapeHtml).join(", ")}</strong>` : ` framed as a business outcome`}, using a real number
      that Career Highlights does not repeat. Move any dropped JD keyword to Highlights or Experience; Stage 9 shows the effect.</div></div>`);
  }
  (data.pattern_warnings || []).forEach(w =>
    items.push(`<div><strong>${escapeHtml(w.label)}</strong>: ${escapeHtml(w.message)}</div>`));
  // Card numbers as shown on the cards (1-based, as generated), not the internal ids (p3-13 is card 14).
  const cardNo = id => { const i = currentChanges.findIndex(c => c.id === id); return i >= 0 ? `card ${i + 1}` : id; };
  (data.repetition_warnings || []).forEach(w =>
    items.push(`<div><strong>Repeated phrase</strong> "${escapeHtml(w.phrase)}" in ${w.change_ids.length} cards
      (${w.change_ids.slice().sort((x, y) => currentChanges.findIndex(c => c.id === x) - currentChanges.findIndex(c => c.id === y)).map(cardNo).join(", ")})</div>`));
  const el = document.getElementById("warnBanner");
  if (!el) return;
  el.style.display = items.length ? "block" : "none";
  el.innerHTML = items.join("");
}

// Every reason Stage 5 used to uncheck or flag a card, so an unchecked card
// is never unexplained.
function flagList(c) {
  const f = [];
  if (c.stale_citation) f.push(["block", c.stale_citation]);
  if (c.spans_roles && c.spans_roles.length) f.push(["block",
    `Draws on two roles (${c.spans_roles.join(" and ")}) but is placed under one role's bullets. A bullet belongs to a single employer: split it, or place it under Career Highlights`]);
  if (c.unsupported_terms && c.unsupported_terms.length) f.push([c.unsupported_blocking ? "block" : "note",
    `New term not found in your resume or evidence: ${c.unsupported_terms.join(", ")}. ${c.unsupported_blocking ? "It would appear in your Summary or Highlights: confirm you have this experience before ticking" : "Confirm you have this experience"}`]);
  if (c.anchor_flag) f.push(["block", c.anchor_flag]);
  if (c.resolution) f.push([c.default_accepted === false && /Not pre-checked/.test(c.resolution) ? "block" : "note", c.resolution]);
  if (c.summary_flag) f.push(["block", c.summary_flag]);
  if (c.style_hits) {
    const list = Object.entries(c.style_hits).map(([k, v]) => v > 1 ? `${k} x${v}` : k).join(", ");
    f.push([c.style_flag ? "block" : "note", `Writing pattern${Object.keys(c.style_hits).length > 1 ? "s" : ""}: ${list}`]);
  }
  if (c.hedge_flag) f.push(["block", `The model's own reasoning hedges ("${c.hedge_flag}"); treat as a guess`]);
  if (c.pattern_flag) f.push(["block", c.pattern_flag]);
  if (c.repetition_flag) f.push(["block", c.repetition_flag]);
  if (c.grounded === false) f.push(["block", `Not backed by the evidence ledger: ${(c.ungrounded_numbers || []).join(", ")}`]);
  (c.dnc_hits || []).forEach(h => f.push(["block",
    `Do-Not-Claim (${h.file}): "${h.claim}"${h.say_instead ? `. Say instead: ${h.say_instead}` : ""}`]));
  if (c.unsupported_tools && c.unsupported_tools.length) f.push(["block",
    `Adds ${c.unsupported_tools.map(t => `"${t}"`).join(", ")} to your skills, but no evidence file mentions ${c.unsupported_tools.length > 1 ? "them" : "it"} (your LinkedIn profile included): add it to your evidence if it is true`]);
  if (c.title_change) f.push(["block",
    `Changes your opening title to "${c.title_change.title}", but ${c.title_change.why}. Your titles are in the "Role titles" list above`]);
  if (c.unsupported_audience && c.unsupported_audience.length) f.push(["block",
    `Adds ${c.unsupported_audience.map(t => `"${t}"`).join(", ")} as a stakeholder, but ${c.unsupported_audience_file} never mentions ${c.unsupported_audience.length > 1 ? "them" : "it"}: confirm this is true before ticking`]);
  if (c.drops && c.drops.length) f.push(["note", `Removes ${c.drops.join(", ")}, which appears nowhere else in your resume: check that dropping it is intended`]);
  if (c.keeps_unbacked && c.keeps_unbacked.length) f.push(["note",
    `Keeps ${c.keeps_unbacked.slice(0, 4).join("; ")}${c.keeps_unbacked.length > 4 ? ` (+${c.keeps_unbacked.length - 4} more)` : ""}: fix the number in the base variant first`]);
  (c.keeps_dnc || []).forEach(h => f.push(["note",
    `This line already conflicts with your do-not-claim list: "${h.claim}"${h.say_instead ? `. Say instead: ${h.say_instead}` : ""}`]));
  if (c.summary_growth) f.push(["note", `Lengthens the Summary (+${c.summary_growth} words; limit 45)`]);
  if (c.possible_duplicate) f.push(["note", `Overlaps an existing line (${c.duplicate_overlap_score}): "${c.duplicate_match_snippet || ""}${/[.!?]$/.test(c.duplicate_match_snippet || "") ? "" : "…"}"${c.duplicate_hint ? " → " + c.duplicate_hint : ""}`]);
  return f;
}
function cardFlags(c) {
  const f = flagList(c);
  if (!f.length) return "";
  return `<div class="change-flags">${f.map(([k, t]) =>
    `<span class="change-flag change-flag--${k}">${escapeHtml(t)}</span>`).join("")}</div>`;
}

function renderChanges() {
  const list = document.getElementById("changeList");
  if (!currentChanges.length) {
    const dg = (window.lastManifest || {}).diagnostics;
    list.innerHTML = `<p class="empty-state">No candidate changes to review for this JD.</p>` +
      (dg ? `<p class="empty-state"><strong>Why:</strong> ${escapeHtml(dg.summary)}</p>` : "");
    updateAcceptedCount();
    return;
  }

  list.innerHTML = currentChanges.map((c, i) => {
    const isInsertion = c.type === "insertion";
    const confClass = (c.confidence || "").toLowerCase().includes("high") ? "high"
                     : (c.confidence || "").toLowerCase().includes("medium") ? "medium"
                     : (c.confidence || "").toLowerCase().includes("low") ? "low" : "";
    const diffHtml = isInsertion
      ? `<div class="diff-block diff-block--insertion-only">+ ${c.diff_html.after || escapeHtml(c.after)}</div>`
      : `<div class="diff-block">
           <div>${c.diff_html.before}</div>
           <div style="margin-top:4px;">${c.diff_html.after}</div>
         </div>`;

    const sourceLabel = { stage2: "STAGE 2", stage3: "STAGE 3", stage4: "STAGE 4" }[c.source] || c.source.toUpperCase();
    const atsBadge = c.ats_score > 0
      ? `<span class="ats-score" title="Weighted count of this JD's missing keywords this text introduces">ATS +${c.ats_score}</span>`
      : "";
    const wordBadge = wordDeltaBadge(c.word_delta);

    const altBlock = c.alt_suggestion ? `
      <div class="alt-suggestion">
        <div class="alt-suggestion-label">
          💡 Stage 2 also suggested a larger rewrite here
          ${c.alt_suggestion.ats_score > 0 ? `<span class="ats-score ats-score--alt">ATS +${c.alt_suggestion.ats_score}</span>` : ""}
          ${wordDeltaBadge(c.alt_suggestion.word_delta)}
        </div>
        <div class="alt-suggestion-text">${escapeHtml(c.alt_suggestion.after)}</div>
        <div class="alt-suggestion-note">${escapeHtml(c.alt_suggestion.note)}</div>
      </div>` : "";

    return `
      <div class="change-card ${c.default_accepted ? "" : "change-card--rejected"}" id="card-${c.id}">
        <div class="change-card-top">
          <input type="checkbox" id="chk-${c.id}" ${c.default_accepted ? "checked" : ""}
                 onchange="toggleChange('${c.id}')">
          <span class="change-card-number" title="Card ${i + 1} of ${currentChanges.length} in the current sort order">${i + 1}.</span>
          <div class="change-card-body">
            <div class="change-card-meta">
              <span class="change-type change-type--${c.type}">${c.type}</span>
              <span class="change-source change-source--${c.source}">${sourceLabel}</span>
              ${confClass ? `<span class="change-confidence change-confidence--${confClass}">${escapeHtml(c.confidence)}</span>` : ""}
              ${atsBadge}
              ${wordBadge}
              <span class="change-file">${escapeHtml(c.file || "")}</span>
            </div>
            ${cardFlags(c)}
            ${diffHtml}
            <div class="change-reason">${escapeHtml(c.reason || "")}</div>
            ${altBlock}
          </div>
        </div>
      </div>`;
  }).join("");

  updateAcceptedCount();
}

function wordDeltaBadge(delta) {
  if (delta == null) return "";
  if (delta === 0) return `<span class="word-delta word-delta--zero">±0w</span>`;
  const sign = delta > 0 ? "+" : "";
  const cls = delta > 30 ? "word-delta--high" : delta > 10 ? "word-delta--mid" : "word-delta--low";
  return `<span class="word-delta ${cls}" title="Net word count change if applied">${sign}${delta}w</span>`;
}

let sortBySize = false;
function toggleSort() {
  sortBySize = !sortBySize;
  const btn = document.getElementById("sortToggleBtn");
  if (sortBySize) {
    currentChanges = [...currentChanges].sort((a, b) => (b.word_delta || 0) - (a.word_delta || 0));
    if (btn) btn.textContent = "Sort: by word cost (highest first) ↓";
  } else {
    currentChanges = [...currentChanges].sort((a, b) => {
      const an = parseInt(a.id.split("-")[1], 10), bn = parseInt(b.id.split("-")[1], 10);
      return a.id.split("-")[0] === b.id.split("-")[0] ? an - bn : a.id.localeCompare(b.id);
    });
    if (btn) btn.textContent = "Sort: as generated";
  }
  renderChanges();
}

function toggleChange(id) {
  const card = document.getElementById(`card-${id}`);
  const checked = document.getElementById(`chk-${id}`).checked;
  card.classList.toggle("change-card--rejected", !checked);
  updateAcceptedCount();
}

function getAcceptedIds() {
  return currentChanges
    .filter(c => document.getElementById(`chk-${c.id}`) && document.getElementById(`chk-${c.id}`).checked)
    .map(c => c.id);
}

function updateAcceptedCount() {
  const accepted = currentChanges.filter(c => document.getElementById(`chk-${c.id}`) && document.getElementById(`chk-${c.id}`).checked);
  const n = accepted.length;
  const netWords = accepted.reduce((sum, c) => sum + (c.word_delta || 0), 0);
  const total = baseWordCount + netWords;
  // Rough heuristic only: ~550 words/page is a common estimate for a
  // single-column technical resume at 10-11pt with standard margins.
  // Actual page count depends on font, margins, and bullet wrapping once
  // this markdown is laid out as a real document -- treat this as a
  // directional warning, not a guarantee, and check the real export.
  const pages = total / WORDS_PER_PAGE;
  document.getElementById("acceptedCount").innerHTML =
    `${n} selected · ~${total}w (${netWords >= 0 ? "+" : ""}${netWords}w) · ` +
    `<span class="${pages > 2 ? "page-est page-est--over" : "page-est"}">~${pages.toFixed(1)} pages (est.)</span>`;
  document.getElementById("applyBtn").disabled = currentChanges.length === 0;
}

async function applyApproved() {
  // A ticked card that your own evidence argues against should not go in on a single click. (A card carrying a banned claim
  // was ticked and applied, so the saved resume had it twice.)
  const ids = new Set(getAcceptedIds());
  // Evidence flags only. "This phrasing repeats ..." is a style note about reused wording, not a claim your evidence argues
  // against, so it is counted separately. (A reviewer saw "7 flagged" with 6 listed, 5 of them only repeat-phrase notes.)
  const isStyle = t => /^This phrasing repeats|^Part of a batch-wide/.test(t);
  const marked = currentChanges.filter(c => ids.has(c.id)).map(c => ({c, why: flagList(c).filter(([k]) => k === "block").map(([, t]) => t)}));
  const risky = marked.filter(x => x.why.some(t => !isStyle(t))).map(x => ({c: x.c, why: x.why.filter(t => !isStyle(t))}));
  const styleOnly = marked.filter(x => x.why.length && x.why.every(isStyle));
  if (risky.length || styleOnly.length) {
    const shown = risky.slice(0, 12).map(x => `  - card ${currentChanges.indexOf(x.c) + 1}: ${x.why[0].slice(0, 130)}`).join("\n");
    const more = risky.length > 12 ? `\n  ...and ${risky.length - 12} more` : "";
    const tail = styleOnly.length ? `\n\n${styleOnly.length} more ticked card(s) only reuse wording that other cards use (cards ${styleOnly.map(x => currentChanges.indexOf(x.c) + 1).join(", ")}); that is style, not evidence.` : "";
    const head = risky.length ? `${risky.length} ticked card(s) are flagged against your own evidence:\n\n${shown}${more}` : "No ticked card is flagged against your evidence.";
    if (!window.confirm(`${head}${tail}\n\nApply the ticked cards?`)) return;
  }
  const btn = document.getElementById("applyBtn");
  const resultBanner = document.getElementById("resultBanner");
  btn.disabled = true;
  btn.textContent = "Applying…";
  resultBanner.style.display = "none";

  try {
    const res = await fetch("/api/stage6-apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd: currentJD, accepted_ids: getAcceptedIds() }),
    });
    const data = await res.json();
    resultBanner.style.display = "block";
    if (data.ok) {
      resultBanner.className = "banner banner--warn";
      resultBanner.style.background = "var(--green-bg)";
      resultBanner.style.color = "var(--green)";
      resultBanner.innerHTML = `✅ Saved <strong>${data.resume_path}</strong>.<br><pre style="white-space:pre-wrap;font-size:11px;margin-top:6px;">${escapeHtml(data.log)}</pre>`;
    } else {
      resultBanner.className = "banner banner--warn";
      resultBanner.innerHTML = `❌ ${escapeHtml(data.error || data.log || "Apply failed")}`;
    }
  } catch (e) {
    resultBanner.style.display = "block";
    resultBanner.className = "banner banner--warn";
    resultBanner.innerHTML = `❌ ${escapeHtml(String(e))}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Apply approved changes → save resume";
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}
