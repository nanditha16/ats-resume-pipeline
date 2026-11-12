// web/static/js/trim.js — Stage 7 trim-review page.
//
// Loads candidates from /api/trim-manifest (computed by scripts/trim_review.py
// -- this file never scores a bullet itself, only renders what that script
// already decided and lets the user override it), grouped by role, with a
// checkbox per bullet defaulting to whatever that script marked as the
// weakest bullets in an over-cap role. Posts the approved id list to
// /api/trim-apply on demand -- nothing is deleted until that click.

let currentJD = null;
let currentCandidates = [];

async function initTrim(jd) {
  currentJD = jd;
  await reloadManifest();
}

async function reloadManifest() {
  const meta = document.getElementById("trimMeta");
  const groups = document.getElementById("roleGroups");
  const errorBanner = document.getElementById("errorBanner");
  const cap = document.getElementById("capInput").value || "5";
  errorBanner.style.display = "none";
  groups.innerHTML = `<p class="empty-state">Loading…</p>`;

  let data;
  try {
    const res = await fetch(`/api/trim-manifest?jd=${encodeURIComponent(currentJD)}&cap=${encodeURIComponent(cap)}`);
    data = await res.json();
  } catch (e) {
    groups.innerHTML = "";
    errorBanner.style.display = "block";
    errorBanner.textContent = `Failed to load: ${e}`;
    return;
  }

  if (!data.ok) {
    meta.textContent = "Error";
    groups.innerHTML = "";
    errorBanner.style.display = "block";
    errorBanner.textContent = data.error || "Unknown error";
    return;
  }

  meta.textContent = `Cap: ${data.cap} bullets/role · ${data.roles_over_cap} role(s) over cap`;
  currentCandidates = data.candidates;
  renderGroups();
}

function renderGroups() {
  const container = document.getElementById("roleGroups");
  if (!currentCandidates.length) {
    container.innerHTML = `<p class="empty-state">No role has more bullets than the cap — nothing to trim.</p>`;
    updateDeleteCount();
    return;
  }

  const byRole = {};
  currentCandidates.forEach(c => { (byRole[c.role] = byRole[c.role] || []).push(c); });

  container.innerHTML = Object.entries(byRole).map(([role, items]) => {
    const cutCount = items.filter(c => c.default_deleted).length;
    const cards = items.map(c => {
      if (!c.default_deleted && c.reason.startsWith("kept")) {
        return `
          <div class="trim-card trim-card--keep">
            <span class="trim-keep-label">KEEP</span>
            <div class="trim-card-body">
              <div class="trim-bullet-text">${escapeHtml(c.bullet)}</div>
            </div>
          </div>`;
      }
      const metricBadge = c.has_metric
        ? `<span class="trim-badge trim-badge--metric">has metric</span>`
        : `<span class="trim-badge trim-badge--nometric">no metric</span>`;
      return `
        <div class="trim-card" id="trimcard-${c.id}">
          <input type="checkbox" id="trimchk-${c.id}" ${c.default_deleted ? "checked" : ""}
                 onchange="toggleTrimCard('${c.id}')">
          <div class="trim-card-body">
            <div class="trim-card-meta">
              <span class="trim-badge">ATS ${c.ats_score}</span>
              ${metricBadge}
              <span class="trim-badge">${c.word_count}w</span>
            </div>
            <div class="trim-bullet-text">${escapeHtml(c.bullet)}</div>
            <div class="trim-reason">${escapeHtml(c.reason)}</div>
          </div>
        </div>`;
    }).join("");

    return `
      <div class="role-group">
        <div class="role-group-title">
          ${escapeHtml(role)}
          <span class="role-group-count">${items.length} bullets · ${cutCount} marked for removal</span>
        </div>
        ${cards}
      </div>`;
  }).join("");

  currentCandidates.forEach(c => {
    if (c.default_deleted) {
      const card = document.getElementById(`trimcard-${c.id}`);
      if (card) card.classList.add("trim-card--marked");
    }
  });

  updateDeleteCount();
}

function toggleTrimCard(id) {
  const card = document.getElementById(`trimcard-${id}`);
  const checked = document.getElementById(`trimchk-${id}`).checked;
  card.classList.toggle("trim-card--marked", checked);
  updateDeleteCount();
}

function getSelectedIds() {
  return currentCandidates
    .filter(c => document.getElementById(`trimchk-${c.id}`) && document.getElementById(`trimchk-${c.id}`).checked)
    .map(c => c.id);
}

function updateDeleteCount() {
  const n = getSelectedIds().length;
  document.getElementById("deleteCount").textContent = `${n} selected to delete`;
  document.getElementById("applyBtn").disabled = n === 0;
}

async function applyTrim() {
  const btn = document.getElementById("applyBtn");
  const resultBanner = document.getElementById("resultBanner");
  const ids = getSelectedIds();
  if (!ids.length) return;
  if (!confirm(`Delete ${ids.length} bullet(s) from ${currentJD}'s resume? This edits the file directly.`)) return;

  btn.disabled = true;
  btn.textContent = "Deleting…";
  resultBanner.style.display = "none";

  try {
    const res = await fetch("/api/trim-apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd: currentJD, accepted_ids: ids }),
    });
    const data = await res.json();
    resultBanner.style.display = "block";
    if (data.ok) {
      resultBanner.style.background = "var(--green-bg)";
      resultBanner.style.color = "var(--green)";
      resultBanner.innerHTML = `✅ Deleted ${data.deleted} bullet(s).<br><pre style="white-space:pre-wrap;font-size:11px;margin-top:6px;">${escapeHtml(data.log)}</pre>`;
      await reloadManifest();
    } else {
      resultBanner.style.background = "";
      resultBanner.style.color = "";
      resultBanner.className = "banner banner--warn";
      resultBanner.innerHTML = `❌ ${escapeHtml(data.error || data.log || "Delete failed")}`;
    }
  } catch (e) {
    resultBanner.style.display = "block";
    resultBanner.className = "banner banner--warn";
    resultBanner.innerHTML = `❌ ${escapeHtml(String(e))}`;
  } finally {
    btn.textContent = "Delete selected bullets";
    updateDeleteCount();
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}
