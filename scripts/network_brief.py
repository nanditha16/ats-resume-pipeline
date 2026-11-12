#!/usr/bin/env python3
"""
network_brief.py -- Stage 10 outreach brief and targets store.

For a JD that passes the Callback Score gate, writes:
  prompts/{JD}_PREP/resp/10_network_brief.md   LinkedIn people-search links for
                                               3 slots + resume proof points
  prompts/{JD}_PREP/resp/10_targets.json       where the 3 people you pick, their
                                               outreach status, and the
                                               application outcome are stored

Decisions (docs/STAGE10_CALLBACK_OUTREACH_PLAN.md):
  D1  no automated LinkedIn access -- the brief contains links YOU open in
      your own logged-in browser; nothing here fetches LinkedIn
  D4  slots: recruiter, hiring manager, skip-level director (no VPs)
  D8  stored per target: name, title, profile URL, why relevant, recent
      activity, warm path, status. No emails, phone numbers, profile content.

Every status / outcome change is appended to policy/run_log.jsonl as a
stage "10" event, so outcomes can later be compared against callback scores.

CLI:
  python3 scripts/network_brief.py JD7           # only if gated in
  python3 scripts/network_brief.py JD7 --force   # write even if gated out
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
import ats_parse        # noqa: E402
import callback_score   # noqa: E402

ROOT = callback_score.ROOT
PROMPTS = ROOT / "prompts"
RUN_LOG = ROOT / "policy" / "run_log.jsonl"

SLOTS = ["recruiter", "hiring_manager", "skip_level"]
# Fallback only: a peer on the team (same level) can refer but does not own
# the req. A real scout run returned three peer TPMs and no recruiter; peers
# are now kept in their own slot so they never stand in for slots 1-3.
FALLBACK_SLOT = "referrer"
ALL_SLOTS = SLOTS + [FALLBACK_SLOT]
MAX_REFERRERS = 2
TARGET_STATUSES = ["not_contacted", "sent", "replied", "referred", "interview", "no_response", "declined"]
OUTCOMES = ["pending", "call", "rejected", "no_response"]
TARGET_FIELDS = ["name", "title", "linkedin_url", "why_relevant", "recent_activity", "warm_path"]
SEARCH = "https://www.linkedin.com/search/results/people/?keywords="

FAMILY_WORDS = {"program": "program management", "product": "product management",
                "eng_mgmt": "engineering", "ic_engineer": "engineering", "leadership": ""}


def targets_path(jd: str) -> Path:
    return PROMPTS / f"{jd}_PREP" / "resp" / "10_targets.json"


def brief_path(jd: str) -> Path:
    return PROMPTS / f"{jd}_PREP" / "resp" / "10_network_brief.md"


def _core_title(title: str) -> str:
    """'Senior Staff Technical Program Manager, Search Signals' ->
    'Technical Program Manager'. Drops seniority words and anything after
    the first comma / dash / parenthesis, which is usually team detail."""
    t = re.split(r",| - | – |\(", title or "")[0]
    t = re.sub(r"\b(Senior|Sr\.?|Staff|Principal|Lead|III|II|I)\b", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def _team_hint(title: str) -> str:
    """Team detail after the first comma/dash: 'Search Signals', 'Security IP'."""
    parts = re.split(r",| - | – ", title or "", maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _url(*words) -> str:
    q = " ".join(w for w in words if w).strip()
    return SEARCH + quote(q)


def build_brief(jd: str, score: dict) -> str:
    meta = score["meta"]
    company = meta.get("company") or "COMPANY"
    title = meta.get("role_title") or ""
    core, team = _core_title(title), _team_hint(title)
    fam_word = FAMILY_WORDS.get(score["title_match"]["jd_family"] or "", "")
    rescore = (PROMPTS / f"{jd}_PREP" / "resp" / "ats_rescore_response.txt").read_text(errors="replace")
    strengths = ats_parse.extract_section_bullets(rescore, "strength", limit=3)

    lines = [
        f"# Outreach brief: {jd} ({company}, {title})",
        "",
        f"Callback score: {score['callback_score']} (threshold {score['threshold']}), "
        f"{'gated IN' if score['gated_in'] else 'gated OUT'}",
    ]
    if meta.get("job_url"):
        lines.append(f"Job posting: {meta['job_url']}")
    if not meta.get("company"):
        lines += ["", "WARNING: company could not be derived from the job link. Set it on the Stage 10 page, "
                      "then regenerate this brief; the searches below use a placeholder."]
    lines += [
        "",
        "Order: apply first, then recruiter, then hiring manager. Skip-level only after about a week of silence.",
        "",
        f"Preferred: in Claude with Chrome connected, run  /linkedin-target-scout {jd}",
        "It reads this JD's context from the app, finds named people, and imports them into this page after you confirm.",
        "Manual fallback: open the links below in your own logged-in LinkedIn session.",
        "",
        "## 1. Recruiter / Talent Partner",
        f"- Search: {_url(company, 'recruiter', fam_word or core)}",
        f"- Alternate: {_url(company, 'talent acquisition', team or core)}",
        "- Look for: a recruiter who posted this role, or who recruits for this function.",
        "",
        "## 2. Hiring Manager",
        f"- Search: {_url(company, team, fam_word or core, 'manager')}",
        f"- Alternate: {_url(company, core, 'manager')}",
        "- Look for: the manager of the team named in the JD. If not identifiable, record "
        "\"Hiring manager not publicly identifiable\" and use a team member.",
        "",
        "## 3. Skip-level Director",
        f"- Search: {_url(company, 'director', team or fam_word or core)}",
        "- Look for: the director over the org in the JD.",
        "",
        "## Proof points to reference (Stage 9 Strengths)",
    ]
    lines += [f"- {s}" for s in strengths] or ["- (none parsed from the rescore)"]
    lines += ["", f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}"]
    return "\n".join(lines) + "\n"


def empty_targets(jd: str, meta: dict) -> dict:
    return {
        "jd": jd,
        "company": meta.get("company"),
        "role_title": meta.get("role_title"),
        "application": {"applied_on": None, "outcome": "pending"},
        "targets": [{"slot": s, **{f: "" for f in TARGET_FIELDS},
                     "captured_at": None, "status": "not_contacted", "status_history": []} for s in SLOTS],
    }


def load_targets(jd: str):
    p = targets_path(jd)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            return None
    return None


def _has_data(t: dict) -> bool:
    return bool(t) and (any(x.get("name") or x.get("linkedin_url") for x in t.get("targets", []))
                        or (t.get("application") or {}).get("applied_on"))


def generate(jd: str, force: bool = False) -> dict:
    score = callback_score.compute(jd)
    if score["status"] != "scored":
        return {"ok": False, "error": f"{jd} not scored: {score['reason']}"}
    if not score["gated_in"] and not force:
        why = ", ".join(f"{k} FAIL" for k in score["failed_knockouts"]) or \
              f"score {score['callback_score']} < {score['threshold']}"
        return {"ok": False, "error": f"{jd} is gated out ({why}). Use --force to generate anyway.", "score": score}
    brief = build_brief(jd, score)
    brief_path(jd).parent.mkdir(parents=True, exist_ok=True)
    brief_path(jd).write_text(brief)
    existing = load_targets(jd)
    created = False
    if not _has_data(existing):                     # never overwrite real data
        targets_path(jd).write_text(json.dumps(empty_targets(jd, score["meta"]), indent=2))
        created = True
    return {"ok": True, "brief": brief, "brief_path": str(brief_path(jd).relative_to(ROOT)),
            "targets_created": created, "score": score}


# ── Saving targets + run_log events ──────────────────────────────────────────
def append_event(event: dict) -> None:
    """Same file and run_id scheme as claude_execute.log_run (sequential id =
    line count + 1), so cost rows and Stage 10 outcome rows share one history."""
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    run_id = 1
    if RUN_LOG.exists():
        with open(RUN_LOG, encoding="utf-8") as f:
            run_id = sum(1 for _ in f) + 1
    row = {"run_id": run_id, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "stage": "10", **event}
    with open(RUN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def save_targets(jd: str, incoming: dict) -> dict:
    """Validate and save. Logs one run_log event per status/outcome change."""
    cur = load_targets(jd) or empty_targets(jd, callback_score.jd_meta.get_meta(jd))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_slot = {t["slot"]: t for t in cur["targets"]}
    events = []
    for t in incoming.get("targets", []):
        slot = t.get("slot")
        idx = t.get("idx")
        if isinstance(idx, int) and 0 <= idx < len(cur["targets"]) and cur["targets"][idx]["slot"] == slot:
            by_slot = {**by_slot, slot: cur["targets"][idx]}     # position wins; referrers share a slot name
        if slot == FALLBACK_SLOT and slot not in by_slot:
            by_slot[slot] = {"slot": slot, **{f: "" for f in TARGET_FIELDS},
                             "captured_at": None, "status": "not_contacted", "status_history": []}
            cur["targets"].append(by_slot[slot])
        if slot not in by_slot:
            continue
        old = by_slot[slot]
        for f in TARGET_FIELDS:
            if f in t:
                old[f] = str(t[f] or "").strip()[:500]
        if old.get("name") and not old.get("captured_at"):
            old["captured_at"] = now
        new_status = t.get("status", old["status"])
        if new_status not in TARGET_STATUSES:
            return {"ok": False, "error": f"invalid status '{new_status}'"}
        if new_status != old["status"]:
            old["status_history"].append({"from": old["status"], "to": new_status, "at": now})
            events.append({"jd": jd, "event": "target_status", "slot": slot, "from": old["status"], "to": new_status})
            old["status"] = new_status
    app_in = incoming.get("application") or {}
    app_cur = cur.setdefault("application", {"applied_on": None, "outcome": "pending"})
    if "applied_on" in app_in and (app_in["applied_on"] or None) != app_cur.get("applied_on"):
        app_cur["applied_on"] = app_in["applied_on"] or None
        events.append({"jd": jd, "event": "applied", "applied_on": app_cur["applied_on"]})
    if "outcome" in app_in and app_in["outcome"] != app_cur.get("outcome"):
        if app_in["outcome"] not in OUTCOMES:
            return {"ok": False, "error": f"invalid outcome '{app_in['outcome']}'"}
        score = callback_score.compute(jd)
        events.append({"jd": jd, "event": "application_outcome", "from": app_cur.get("outcome"),
                       "to": app_in["outcome"], "callback_score": score.get("callback_score")})
        app_cur["outcome"] = app_in["outcome"]
    targets_path(jd).parent.mkdir(parents=True, exist_ok=True)
    targets_path(jd).write_text(json.dumps(cur, indent=2))
    for e in events:
        append_event(e)
    return {"ok": True, "targets": cur, "events_logged": len(events)}


# ── Scout hand-off (Phase 5): context for the skill, import of its results ──
LINKEDIN_PROFILE_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?", re.I)
APP_BASE = "http://localhost:5001"

SCOUT_RULES = {
    "priority": [
        "recruiter: recruiter / talent partner for THIS req (LinkedIn job page 'Meet the hiring team', "
        "the person who posted it, or a talent partner for this function and location)",
        "hiring_manager: the manager who would own this hire (manages the team or function named in the JD). "
        "NOT a peer at the same level as the role",
        "skip_level: director over that org",
        "referrer: fallback only, a peer on the team; never fills the three slots above",
    ],
    "verify": [
        "current employer from the profile's Experience section, not the headline (headlines go stale)",
        "use LinkedIn's Current company filter in people search",
        "prefer the job's location",
        "prefer activity within the last 30 days; say so honestly when not verified",
    ],
    "limits": [
        "read-only: never click Connect, Follow, Message, or InMail; never send anything",
        "stop and tell the user on any CAPTCHA, checkpoint, or unusual-activity warning",
        "about 15 profile views per JD; at most 3 JDs per sitting",
        "no emails, phone numbers, or profile content beyond the fields in output_contract",
    ],
    "if_hiring_manager_unknown": "set hiring_manager_identifiable=false, leave hiring_manager empty, "
                                 "and use skip_level plus a referrer instead",
}

OUTPUT_CONTRACT = {
    "jd": "JDx",
    "hiring_manager_identifiable": True,
    "notes": "one or two sentences on anything uncertain",
    "targets": [{"slot": "recruiter | hiring_manager | skip_level | referrer", "name": "", "title": "",
                 "linkedin_url": "https://www.linkedin.com/in/...", "why_relevant": "one sentence",
                 "recent_activity": "one sentence", "warm_path": "shared employer/school if any, else empty",
                 "verified_current_employer": True}],
}


def _job_id(meta: dict, jd_text: str):
    m = re.search(r"Job ID\s*:?\s*\n?\s*([A-Z]*\d{5,})", jd_text) or \
        re.search(r"(?:req(?:uisition)?(?: id)?|Req ID:?)\s*:?\s*([A-Z]{0,3}-?\d{5,})", jd_text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{6,})(?:[/?]|$)", meta.get("job_url") or "")
    return m.group(1) if m else None


def scout_context(jd: str) -> dict:
    score = callback_score.compute(jd)
    meta = score.get("meta") or callback_score.jd_meta.get_meta(jd)
    jd_text, _ = callback_score.jd_meta.jd_raw_text(jd)
    cities = callback_score._jd_cities(jd_text) if jd_text else []
    gated = score.get("status") == "scored" and score.get("gated_in")
    return {
        "ok": True,
        "jd": jd,
        "company": meta.get("company"),
        "role_title": meta.get("role_title"),
        "job_url": meta.get("job_url"),
        "job_id": _job_id(meta, jd_text or ""),
        "locations": cities[:3] or (["Remote"] if callback_score.REMOTE_RE.search(jd_text or "") else []),
        "callback_score": score.get("callback_score"),
        "gated_in": bool(gated),
        "gate_reason": None if gated else (score.get("reason") or
                                           (", ".join(f"{k} FAIL" for k in score.get("failed_knockouts", []))
                                            or f"score {score.get('callback_score')} < {score.get('threshold')}")),
        "missing_meta": meta.get("missing", []),
        "rules": SCOUT_RULES,
        "output_contract": OUTPUT_CONTRACT,
        "import_page": f"{APP_BASE}/network/{jd}",
    }


def infer_slot(title: str) -> str:
    """Slot from a title, used only when the scout output has no explicit slot.
    'Senior Manager, Program Management' manages PMs -> hiring_manager;
    'Senior Technical Program Manager' is a peer -> referrer."""
    t = (title or "").lower()
    if re.search(r"recruit|talent|sourcer|\bta\b", t):
        return "recruiter"
    if re.search(r"\b(director|head of|vp|vice president)\b", t):
        return "skip_level"
    if re.search(r"\b(engineering manager|senior manager|group manager|manager,|manager of|^manager\b)", t):
        return "hiring_manager"
    return FALLBACK_SLOT


def _extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for cand in ([m.group(1)] if m else []) + [text.strip()]:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and isinstance(obj.get("targets"), list):
                return obj
        except ValueError:
            continue
    return None


def _parse_text_blocks(text: str) -> dict:
    """The skill's human-readable format: '### Target N' / 'Target N' blocks
    with Name:, Title:, LinkedIn URL:, Why Relevant:, Recent Activity:,
    optional Slot:. Field values may continue on the next line."""
    blocks = re.split(r"(?im)^\s*#*\s*Target\s+\d+\s*$", text)[1:]
    labels = {"slot": "slot", "name": "name", "title": "title", "linkedin url": "linkedin_url",
              "why relevant": "why_relevant", "recent activity": "recent_activity", "warm path": "warm_path"}
    targets = []
    for b in blocks:
        b = re.split(r"(?im)^\s*(?:#+\s|One process note|Process note|Notes?:)", b)[0]
        t, key = {}, None
        for ln in b.splitlines():
            m = re.match(r"^\s*([A-Za-z ]{3,20}):\s*(.*)$", ln)
            if m and m.group(1).strip().lower() in labels:
                key = labels[m.group(1).strip().lower()]
                t[key] = m.group(2).strip()
            elif key and ln.strip():
                t[key] = (t[key] + " " + ln.strip()).strip()
        if t.get("name"):
            targets.append(t)
    hm_unknown = bool(re.search(r"hiring manager not publicly identifiable", text, re.I))
    return {"targets": targets, "hiring_manager_identifiable": not hm_unknown, "notes": ""}


def import_scout(jd: str, payload) -> dict:
    """Import linkedin-target-scout results. Accepts the JSON contract (object,
    or text containing it) or the skill's text format. Never overwrites a
    slot whose outreach has started with a different person."""
    if isinstance(payload, dict) and isinstance(payload.get("targets"), list):
        data = payload
    else:
        text = payload.get("text", "") if isinstance(payload, dict) else str(payload or "")
        data = _extract_json(text) or _parse_text_blocks(text)
    if not data.get("targets"):
        return {"ok": False, "error": "no targets found (expected the JSON block or 'Target N' sections)"}

    cur = load_targets(jd) or empty_targets(jd, callback_score.jd_meta.get_meta(jd))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = {t["slot"]: t for t in cur["targets"] if t["slot"] in SLOTS}

    # Normalise every incoming record first.
    warnings, recs = [], []
    for raw in data["targets"]:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        url_m = LINKEDIN_PROFILE_RE.search(str(raw.get("linkedin_url") or ""))
        if not url_m:
            warnings.append(f"{name}: no valid linkedin.com/in/ URL, stored without link")
        slot = str(raw.get("slot") or "").strip().lower().replace(" ", "_").replace("-", "_")
        if slot not in ALL_SLOTS:
            slot = infer_slot(raw.get("title", ""))
        if raw.get("verified_current_employer") is False:
            warnings.append(f"{name}: current employer not verified")
        recs.append({"slot": slot, "name": name[:120], "title": str(raw.get("title") or "")[:200],
                     "linkedin_url": url_m.group(0).rstrip("/") + "/" if url_m else "",
                     "why_relevant": str(raw.get("why_relevant") or "")[:500],
                     "recent_activity": str(raw.get("recent_activity") or "")[:500],
                     "warm_path": str(raw.get("warm_path") or "")[:200]})

    def same_person(a, b):
        return bool(a.get("linkedin_url")) and a.get("linkedin_url") == b.get("linkedin_url")

    # Someone already in their stated slot (a refresh) is placed first, so a
    # re-import of the same recruiter updates them instead of being bumped
    # to referrer as a duplicate of themselves.
    recs.sort(key=lambda r: 0 if r["slot"] in existing and same_person(existing[r["slot"]], r) else 1)

    imported, core_taken, new_refs = [], {}, []
    for rec in recs:
        slot = rec["slot"]
        if slot in SLOTS:
            if slot in core_taken:
                warnings.append(f"{rec['name']}: {slot} already filled in this import, kept as referrer")
                slot = rec["slot"] = FALLBACK_SLOT
            else:
                old = existing[slot]
                if old.get("status") != "not_contacted" and old.get("name") and not same_person(old, rec):
                    warnings.append(f"{slot}: outreach already started with {old['name']} ({old['status']}); "
                                    f"kept, {rec['name']} not imported")
                    core_taken[slot] = old
                    continue
                old.update({k: v for k, v in rec.items() if k != "slot"})
                old["captured_at"] = now
                core_taken[slot] = old
                imported.append(f"{slot}: {rec['name']}")
                continue
        new_refs.append(rec)

    # Referrers: keep ones already contacted; replace not-yet-contacted ones
    # with this import's; never duplicate a core-slot person; cap the total.
    core_people = [t for t in cur["targets"] if t["slot"] in SLOTS]
    kept = [t for t in cur["targets"] if t["slot"] == FALLBACK_SLOT and t.get("status") != "not_contacted"]
    if not new_refs:
        kept += [t for t in cur["targets"] if t["slot"] == FALLBACK_SLOT and t.get("status") == "not_contacted"]
    refs = list(kept)
    for rec in new_refs:
        if any(same_person(rec, t) for t in core_people + refs):
            continue
        if len(refs) >= MAX_REFERRERS:
            warnings.append(f"{rec['name']}: more than {MAX_REFERRERS} referrers, skipped")
            continue
        refs.append({**rec, "slot": FALLBACK_SLOT, "captured_at": now, "status": "not_contacted", "status_history": []})
        imported.append(f"{FALLBACK_SLOT}: {rec['name']}")
    cur["targets"] = core_people + refs

    cur["scout"] = {"imported_at": now, "hiring_manager_identifiable": bool(data.get("hiring_manager_identifiable", True)),
                    "notes": str(data.get("notes") or "")[:1000], "warnings": warnings}
    targets_path(jd).parent.mkdir(parents=True, exist_ok=True)
    targets_path(jd).write_text(json.dumps(cur, indent=2))
    append_event({"jd": jd, "event": "targets_imported", "source": "linkedin-target-scout",
                  "imported": imported, "hiring_manager_identifiable": cur["scout"]["hiring_manager_identifiable"],
                  "warnings": len(warnings)})
    return {"ok": True, "imported": imported, "warnings": warnings, "targets": cur}


def _main(argv):
    if not argv:
        print(__doc__)
        return 1
    r = generate(argv[0], force="--force" in argv)
    if not r["ok"]:
        print(f"ERROR: {r['error']}", file=sys.stderr)
        return 1
    print(r["brief"])
    print(f"Brief:   {r['brief_path']}", file=sys.stderr)
    print(f"Targets: {'created' if r['targets_created'] else 'kept existing'} "
          f"{targets_path(argv[0]).relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
