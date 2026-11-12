// Stage 10 page: Callback Score breakdown, JD metadata overrides, outreach
// brief, and the 3 target slots (recruiter, hiring manager, skip-level).
// All data comes from /api/network/<jd>; nothing here talks to LinkedIn.

let JD = null;
let state = null;

const SLOT_LABELS = {
  recruiter: "Recruiter / Talent Partner",
  hiring_manager: "Hiring Manager",
  skip_level: "Skip-level Director",
  referrer: "Referrer (peer, fallback)",
};
const FIELD_LABELS = [
  ["name", "Name"], ["title", "Title"], ["linkedin_url", "LinkedIn URL"],
  ["why_relevant", "Why relevant (one sentence)"], ["recent_activity", "Recent activity (one sentence)"],
  ["warm_path", "Warm path (shared employer / school, if any)"],
];
const KO_NAMES = ["location", "onsite", "authorization", "years"];

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function banner(id, text, cls) {
  const el = document.getElementById(id);
  if (!text) { el.style.display = "none"; return; }
  el.className = `banner ${cls || ""}`;
  el.textContent = text;
  el.style.display = "block";
}

async function initNetwork(jd) {
  JD = jd;
  await load();
}

async function load() {
  try {
    const r = await fetch(`/api/network/${encodeURIComponent(JD)}`);
    state = await r.json();
    if (!state.ok) { banner("errorBanner", state.error || "Failed to load"); return; }
    render();
  } catch (e) {
    banner("errorBanner", `Failed to load: ${e}`);
  }
}

function render() {
  const s = state.score;
  const m = s.meta || {};
  document.getElementById("netMeta").textContent =
    `${m.company || "company unknown"} | ${m.role_title || "title unknown"}` +
    (m.posted_date ? ` | posted ${m.posted_date}` : "");
  renderScore(s);
  renderMeta(m, s);
  renderBrief();
  renderApplication();
  renderTargets();
  renderScoutSummary();
  const gated = s.status === "scored" && s.gated_in;
  document.getElementById("briefBtn").textContent = gated ? "Generate brief" : "Generate brief anyway";
}

function renderScore(s) {
  const el = document.getElementById("scorePanel");
  if (s.status !== "scored") {
    el.innerHTML = `<h2 class="net-h2">Callback score</h2><p class="hint">Not scored: ${esc(s.reason)}</p>`;
    return;
  }
  const c = s.components, i = s.inputs;
  const gateCls = s.gated_in ? "net-gate--in" : "net-gate--out";
  const gateText = s.gated_in ? "Gated in: worth outreach"
    : s.failed_knockouts.length ? `Gated out: ${s.failed_knockouts.join(", ")} knockout` : "Gated out: below threshold";
  const kos = KO_NAMES.map(k => {
    const v = s.knockouts[k] || { result: "NA", reason: "" };
    return `<li class="net-ko net-ko--${v.result.toLowerCase()}"><span class="net-ko-tag">${v.result}</span>
            <span><strong>${k}</strong>: ${esc(v.reason)}</span></li>`;
  }).join("");
  el.innerHTML = `
    <h2 class="net-h2">Callback score</h2>
    <div class="net-score"><span class="net-score-num">${s.callback_score}</span>
      <span class="net-score-of">/ 100, threshold ${s.threshold}</span></div>
    <p class="net-gate ${gateCls}">${gateText}</p>
    <table class="net-breakdown">
      <tr><td>Base (interview ${i.interview_prob}, domain ${i.domain})</td><td>${c.base}</td></tr>
      <tr><td>Shortlist ${esc(i.shortlist || "?")}</td><td>${signed(c.shortlist_bonus)}</td></tr>
      <tr><td>Freshness (${i.freshness_days == null ? "posting date unknown" : i.freshness_days + " days"})</td><td>${signed(c.freshness_bonus)}</td></tr>
      <tr><td>Critical gaps (${i.critical_gaps ?? 0})</td><td>${signed(-c.gap_penalty)}</td></tr>
      <tr><td>Title family (${esc(s.title_match.jd_family || "unclassified")} vs resume ${esc(s.title_match.resume_family || "unclassified")})</td><td>${signed(-c.title_penalty)}</td></tr>
    </table>
    <h3 class="net-h3">Knockouts</h3>
    <ul class="net-kos">${kos}</ul>
    <p class="hint">UNKNOWN never blocks. A FAIL the text parsing got wrong can be overridden on the right.</p>`;
}

function signed(n) { return n > 0 ? `+${n}` : `${n}`; }

function renderMeta(m, s) {
  const src = f => m[`${f}_source`] ? `<span class="net-src">${esc(m[`${f}_source`])}</span>` : `<span class="net-src net-src--missing">missing</span>`;
  const ov = m.knockout_overrides || {};
  const koSel = KO_NAMES.map(k => `
    <label class="net-field net-field--inline">${k}
      <select data-ko="${k}">
        ${["", "PASS", "FAIL", "UNKNOWN", "NA"].map(v => `<option value="${v}" ${ov[k] === v ? "selected" : ""}>${v || "auto"}</option>`).join("")}
      </select></label>`).join("");
  document.getElementById("metaPanel").innerHTML = `
    <h2 class="net-h2">JD details</h2>
    <p class="hint">Derived from input/other/${esc(JD)}.txt. Fill anything missing; your values override.</p>
    <label class="net-field">Company ${src("company")}<input id="mCompany" value="${esc(m.company)}"></label>
    <label class="net-field">Role title ${src("role_title")}<input id="mTitle" value="${esc(m.role_title)}"></label>
    <label class="net-field">Posted date ${src("posted_date")}<input id="mPosted" type="date" value="${esc(m.posted_date)}"></label>
    <label class="net-field">Job URL ${src("job_url")}<input id="mUrl" value="${esc(m.job_url)}"></label>
    <h3 class="net-h3">Knockout overrides</h3>
    <div class="net-row">${koSel}</div>
    <button class="btn btn--ghost btn--sm" onclick="saveMeta()">Save details &amp; rescore</button>`;
}

function linkify(text) {
  return esc(text).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderBrief() {
  const el = document.getElementById("briefPanel");
  el.innerHTML = state.brief
    ? `<h2 class="net-h2">Outreach brief</h2><pre class="net-brief">${linkify(state.brief)}</pre>`
    : `<h2 class="net-h2">Outreach brief</h2><p class="hint">No brief yet. Click <strong>Generate brief</strong>.</p>`;
}

function currentTargets() {
  return state.targets || {
    application: { applied_on: null, outcome: "pending" },
    targets: Object.keys(SLOT_LABELS).map(slot => ({ slot, status: "not_contacted", status_history: [] })),
  };
}

function renderApplication() {
  const t = currentTargets();
  document.getElementById("appliedOn").value = (t.application || {}).applied_on || "";
  document.getElementById("appOutcome").innerHTML = state.outcomes
    .map(o => `<option value="${o}" ${((t.application || {}).outcome || "pending") === o ? "selected" : ""}>${o}</option>`).join("");
}

function renderScoutSummary() {
  const sc = (state.targets || {}).scout;
  const el = document.getElementById("scoutSummary");
  if (!sc) { el.textContent = "No scout import yet."; return; }
  el.innerHTML = `Last import ${esc(sc.imported_at.slice(0, 16).replace("T", " "))} UTC` +
    (sc.hiring_manager_identifiable ? "" : " | hiring manager not publicly identifiable") +
    (sc.warnings && sc.warnings.length ? ` | ${sc.warnings.length} warning(s)` : "") +
    (sc.notes ? ` | ${esc(sc.notes)}` : "");
}

function renderTargets() {
  const t = currentTargets();
  document.getElementById("targetSlots").innerHTML = t.targets.map((x, idx) => {
    const fields = FIELD_LABELS.map(([f, label]) => `
      <label class="net-field">${label}
        <input data-idx="${idx}" data-slot="${x.slot}" data-field="${f}" value="${esc(x[f])}"></label>`).join("");
    const hist = (x.status_history || []).map(h => `${esc(h.from)} → ${esc(h.to)} (${esc(h.at.slice(0, 10))})`).join("; ");
    const link = x.linkedin_url ? `<a href="${esc(x.linkedin_url)}" target="_blank" rel="noopener noreferrer">open profile</a>` : "";
    return `
      <div class="net-panel net-target ${x.name ? "" : "net-empty-slot"}">
        <h2 class="net-h2">${SLOT_LABELS[x.slot] || esc(x.slot)} ${link}</h2>
        ${fields}
        <label class="net-field">Status
          <select data-idx="${idx}" data-slot="${x.slot}" data-field="status">
            ${state.statuses.map(s => `<option value="${s}" ${x.status === s ? "selected" : ""}>${s}</option>`).join("")}
          </select></label>
        ${hist ? `<p class="hint">History: ${hist}</p>` : ""}
      </div>`;
  }).join("");
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

async function generateBrief() {
  banner("errorBanner", ""); banner("resultBanner", "");
  const gated = state.score.status === "scored" && state.score.gated_in;
  if (!gated && !confirm("This JD is gated out. Generate the brief anyway?")) return;
  const r = await post("/api/network-brief", { jd: JD, force: !gated });
  if (!r.ok) { banner("errorBanner", r.error); return; }
  banner("resultBanner", `Brief saved to ${r.brief_path}${r.targets_created ? "; empty targets file created" : ""}.`, "banner--ok");
  await load();
}

async function saveTargets() {
  banner("errorBanner", ""); banner("resultBanner", "");
  // Keyed by position, not slot name: there can be two referrers.
  const byIdx = {};
  document.querySelectorAll("[data-idx]").forEach(el => {
    const i = el.dataset.idx;
    byIdx[i] = byIdx[i] || { idx: Number(i), slot: el.dataset.slot };
    byIdx[i][el.dataset.field] = el.value;
  });
  const r = await post(`/api/targets/${encodeURIComponent(JD)}`, {
    targets: Object.values(byIdx),
    application: { applied_on: document.getElementById("appliedOn").value || null,
                   outcome: document.getElementById("appOutcome").value },
  });
  if (!r.ok) { banner("errorBanner", r.error); return; }
  banner("resultBanner", `Saved. ${r.events_logged} change(s) logged to run_log.jsonl.`, "banner--ok");
  await load();
}

async function saveMeta() {
  banner("errorBanner", ""); banner("resultBanner", "");
  const overrides = {};
  document.querySelectorAll("[data-ko]").forEach(el => { overrides[el.dataset.ko] = el.value; });
  const r = await post(`/api/jd-meta/${encodeURIComponent(JD)}`, {
    company: document.getElementById("mCompany").value.trim(),
    role_title: document.getElementById("mTitle").value.trim(),
    posted_date: document.getElementById("mPosted").value,
    job_url: document.getElementById("mUrl").value.trim(),
    knockout_overrides: overrides,
  });
  if (!r.ok) { banner("errorBanner", r.error); return; }
  banner("resultBanner", `Details saved. Callback score is now ${r.score.callback_score ?? "not scored"}.`, "banner--ok");
  await load();
}

async function importScout() {
  banner("errorBanner", ""); banner("resultBanner", "");
  const text = document.getElementById("importText").value.trim();
  if (!text) { banner("errorBanner", "Paste the skill's output first."); return; }
  const r = await post(`/api/targets/${encodeURIComponent(JD)}/import`, { text });
  if (!r.ok) { banner("errorBanner", r.error); return; }
  const w = r.warnings && r.warnings.length ? ` Warnings: ${r.warnings.join("; ")}` : "";
  banner("resultBanner", `Imported ${r.imported.length}: ${r.imported.join(", ") || "none"}.${w}`,
         r.warnings && r.warnings.length ? "banner--warn" : "banner--ok");
  document.getElementById("importText").value = "";
  await load();
}
