#!/usr/bin/env python3
"""
callback_score.py -- Stage 10 Callback Score (deterministic, zero LLM calls).

Answers "is this JD worth outreach effort?" with more than the Stage 9
Interview Probability, which only measures resume-to-JD text match and cannot
see the filters recruiters apply first (location, onsite days, level).
See docs/STAGE10_CALLBACK_OUTREACH_PLAN.md, sections 2 and 4.

  base            = w_interview * interview_prob + w_domain * domain_alignment
  shortlist_bonus = policy.shortlist_bonus[YES|MAYBE|NO]
  freshness_bonus = by days since posting (0 when unknown)
  gap_penalty     = min(each * critical_gaps, cap)
  title_penalty   = policy.title_mismatch_penalty if families differ
  callback_score  = round(base + shortlist + freshness - gaps - title)
  gated_in        = score >= threshold AND no knockout == FAIL

Knockouts are PASS / FAIL / UNKNOWN / NA. UNKNOWN never blocks (D9).
Authorization is never FAIL and citizenship/clearance is always UNKNOWN (D13):
this tool must not infer anyone's legal status.

CLI:
  python3 scripts/callback_score.py JD7            # print + write 10_callback_score.json
  python3 scripts/callback_score.py --all          # table for every JD with a Stage 9 rescore
  python3 scripts/callback_score.py JD7 --no-write # print only
"""
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ats_parse   # noqa: E402
import jd_meta     # noqa: E402

ROOT = jd_meta.ROOT
PROMPTS = ROOT / "prompts"
OUTPUT = ROOT / "output"
POLICY_PATH = ROOT / "policy" / "callback_policy.json"

DEFAULT_POLICY = {
    "threshold": 80, "w_interview": 0.5, "w_domain": 0.5,
    "shortlist_bonus": {"YES": 5, "MAYBE": 0, "NO": -10},
    "freshness_bonus": [[3, 10], [7, 5], [14, 0], [30, -5], [99999, -10]],
    "gap_penalty_each": 3, "gap_penalty_cap": 15, "title_mismatch_penalty": 10,
    "commutable_cities": [],
}


def load_policy() -> dict:
    pol = dict(DEFAULT_POLICY)
    if POLICY_PATH.exists():
        try:
            pol.update({k: v for k, v in json.loads(POLICY_PATH.read_text()).items() if not k.startswith("_")})
        except ValueError:
            pass
    return pol


# ── Inputs ───────────────────────────────────────────────────────────────────
def chosen_variant(jd: str):
    for p in (PROMPTS / "JD_Analysis" / jd / ".chosen_variant", PROMPTS / f"{jd}_PREP" / ".chosen_variant"):
        if p.exists() and p.read_text().strip():
            return p.read_text().strip()
    return None


def resume_for(jd: str):
    """Finalized resume if Stage 5/6 produced one, else the chosen base variant."""
    final = OUTPUT / "review_resume" / f"{jd}_new_resume.md"
    if final.exists():
        return final.read_text(encoding="utf-8", errors="replace"), "final"
    cv = chosen_variant(jd)
    if cv and (OUTPUT / "resume" / f"{cv}.md").exists():
        return (OUTPUT / "resume" / f"{cv}.md").read_text(encoding="utf-8", errors="replace"), "base_variant"
    return "", None


# ── Title family ─────────────────────────────────────────────────────────────
def title_family(title: str):
    """product / program / eng_mgmt / ic_engineer / leadership, or None.
    Order matters: 'Senior Manager, Technical Product Management' is product,
    'Director, Strategy & Program Delivery' is program, 'Director, AI Platform
    Engineering' is eng_mgmt. None means 'unclassified' and never penalises."""
    t = title or ""
    if re.search(r"\bproduct (manager|management|owner|lead)\b", t, re.I):
        return "product"
    if re.search(r"\b(program|project|delivery) (manager|management|lead|delivery)\b", t, re.I) or re.search(r"\bT?PM\b", t):
        return "program"
    if re.search(r"\bengineering manager\b|\bhead of engineering\b|\bdirector\b.*\bengineering\b|\bmanager,? platform engineering\b", t, re.I):
        return "eng_mgmt"
    if re.search(r"\bengineer\b", t, re.I):
        return "ic_engineer"
    if re.search(r"\b(director|head of|vp|vice president|leader)\b", t, re.I):
        return "leadership"
    return None


def resume_title(resume: str):
    lines = [l.strip() for l in resume.splitlines() if l.strip()]
    return lines[1].split("|")[0].strip() if len(lines) > 1 else None


# ── Knockouts ────────────────────────────────────────────────────────────────
REMOTE_RE = re.compile(r"\bfully remote\b|\b100% remote\b|#LI-Remote\b|\bremote role\b|\bremote-first\b|"
                       r"\bwork from anywhere\b|\bremote \((?:US|USA|Canada)\)", re.IGNORECASE)
US_STATES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
             "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
             "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|"
             "North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
             "South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming")
PROVINCES = "Ontario|Quebec|British Columbia|Alberta|Manitoba|Saskatchewan|Nova Scotia|New Brunswick|Newfoundland"
ABBRS = ("AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|"
         "OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC|ON|BC|QC|AB|MB|SK|NS|NB|NL|PE")
# A city only counts when followed by a real state/province. This is what stops
# job-title lines ("Director, AI Platform Engineering") and slogans ("Putting
# People First, ...") being read as locations -- both happened on real JDs.
CITY_REGION_RE = re.compile(rf"\b(?!In |At |Of |The )([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){{0,2}}),\s*(?:(?i:{US_STATES}|{PROVINCES})|(?:{ABBRS})\b)")


def _jd_cities(jd_text: str) -> list:
    """All candidate job cities. Sources: 'City, ST/Province' in the header or
    in any sentence mentioning 'location'; the line after a bare 'locations'
    label (Workday: 'Toronto Office', 'Toronto - Bay St'); 'Location: X'."""
    lines = [l.strip() for l in jd_text.splitlines() if l.strip() and not l.strip().lower().startswith("link:")]
    scan = lines[:40] + [l for l in lines if "location" in l.lower()]
    cities = []
    for i, ln in enumerate(lines[:40]):
        if ln.lower() in ("location", "locations") and i + 1 < len(lines):
            cities.append(re.split(r",| - | Office\b", lines[i + 1])[0].strip())
        m = re.match(r"Location:\s*([^,]+)", ln, re.I)
        if m:
            cities.append(m.group(1).strip())
    for ln in scan:
        for m in CITY_REGION_RE.finditer(ln):
            if not re.match(r"(?i)^\d", m.group(1)):
                cities.append(m.group(1).strip())
    seen, out = set(), []
    for c in cities:
        c = c.title()
        if c and c.lower() not in seen and len(c) <= 30:
            seen.add(c.lower())
            out.append(c)
    return out


def _resume_places(resume: str):
    home = None
    for ln in resume.splitlines()[:6]:
        if "@" in ln and "|" in ln:
            home = ln.split("|")[0].split(",")[0].strip().title()
            break
    reloc = []
    m = re.search(r"relocation\s*\(([^)]*)\)", resume, re.IGNORECASE)
    if m:
        for part in m.group(1).split("/"):
            city = part.split(",")[0].replace("preferred", "").strip()
            if city:
                reloc.append(city.title())
    onsite = re.search(r"onsite\s*(\d)\s*days", resume, re.IGNORECASE)
    return home, reloc, (int(onsite.group(1)) if onsite else None)


def knockouts(jd_text: str, resume: str, policy: dict, overrides: dict) -> dict:
    ko = {}
    home, reloc, resume_onsite = _resume_places(resume)
    commutable = {c.lower() for c in policy.get("commutable_cities", [])}
    ok_cities = commutable | {c.lower() for c in reloc} | ({home.lower()} if home else set())

    # Location
    cities = _jd_cities(jd_text)
    if REMOTE_RE.search(jd_text):
        ko["location"] = {"result": "PASS", "reason": "JD is remote"}
    elif cities:
        hit = [c for c in cities if c.lower() in ok_cities]
        if hit:
            ko["location"] = {"result": "PASS", "reason": f"JD in {', '.join(cities[:3])}; covered by home/commutable/relocation ({hit[0]})"}
        else:
            ko["location"] = {"result": "FAIL", "reason": f"JD in {', '.join(cities[:3])}; resume home {home}, relocation {reloc or 'none'}"}
    else:
        ko["location"] = {"result": "UNKNOWN", "reason": "no JD location found"}

    # Onsite days
    req = None
    for sent in re.split(r"(?<=[.;\n])", jd_text):
        if re.search(r"on-?site|in-?office|in-person|in the office", sent, re.IGNORECASE):
            d = re.search(r"(\d)\s*days?\s*(?:per|a|/)\s*week", sent, re.IGNORECASE)
            if d:
                req = max(req or 0, int(d.group(1)))
            elif re.search(r"full-?time on-?site|fully on-?site|100% on-?site", sent, re.IGNORECASE):
                req = 5
    if req is None:
        ko["onsite"] = {"result": "NA", "reason": "no onsite-days requirement found"}
    elif resume_onsite is not None:
        ok = resume_onsite >= req
        ko["onsite"] = {"result": "PASS" if ok else "FAIL",
                        "reason": f"JD requires {req} days/week onsite; resume states {resume_onsite}"}
    else:
        ko["onsite"] = {"result": "UNKNOWN", "reason": f"JD requires {req} days/week onsite; resume silent"}

    # Authorization (D13: never FAIL, never infer citizenship)
    # Requirement phrasing only. A bare "citizenship" also appears in equal-
    # opportunity lists ("...age, citizenship, marital status...") where it
    # means the employer does NOT discriminate on it -- 4 real JDs had exactly
    # that and were wrongly flagged before this was narrowed.
    if re.search(r"(?:must|required to|need to)\s+(?:be|hold|have)\s+.{0,25}citizen|citizenship\s+(?:is\s+)?required|"
                 r"\bU\.?S\.? citizen|\bCanadian citizen|security clearance|\bclearance required\b|\bITAR\b|export control",
                 jd_text, re.IGNORECASE):
        ko["authorization"] = {"result": "UNKNOWN", "reason": "JD mentions citizenship/clearance; verify yourself"}
    elif re.search(r"\b(?:not|unable to|cannot|won't|will not)\s+(?:provide\s+|offer\s+)?(?:visa\s+)?sponsor|"
                   r"without\s+(?:visa\s+|employer\s+)*sponsorship|authori[sz]ed to work|eligible to work",
                   jd_text, re.IGNORECASE):
        us_ok = re.search(r"without (?:employer )?(?:visa )?sponsorship", resume, re.IGNORECASE)
        canada_job = bool(re.search(r"\b(Canada|CAN|Ontario|ON)\b", jd_text[:1500]))
        if us_ok and not canada_job:
            ko["authorization"] = {"result": "PASS", "reason": "JD: no sponsorship; resume states eligible without sponsorship"}
        else:
            ko["authorization"] = {"result": "UNKNOWN", "reason": "JD has a work-eligibility clause the resume does not explicitly answer"}
    else:
        ko["authorization"] = {"result": "NA", "reason": "no work-eligibility clause found"}

    # Years of experience
    req_years = [int(m.group(1)) for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:or more\s+)?years?", jd_text, re.IGNORECASE)
                 if int(m.group(1)) <= 25 and re.search(r"experience", jd_text[m.end():m.end() + 80], re.IGNORECASE)]
    have = [int(x) for x in re.findall(r"(\d{1,2})\+?\s*years", resume[:1500]) if int(x) <= 40]
    if not req_years:
        ko["years"] = {"result": "NA", "reason": "no years requirement found"}
    elif not have:
        ko["years"] = {"result": "UNKNOWN", "reason": f"JD asks {max(req_years)}+; resume years not found"}
    else:
        ok = max(have) >= max(req_years)
        ko["years"] = {"result": "PASS" if ok else "FAIL", "reason": f"JD max {max(req_years)}+; resume {max(have)}+"}

    for k, v in (overrides or {}).items():
        if k in ko and v in ("PASS", "FAIL", "UNKNOWN", "NA"):
            ko[k] = {"result": v, "reason": f"manual override (was {ko[k]['result']}: {ko[k]['reason']})"}
    return ko


# ── Score ────────────────────────────────────────────────────────────────────
def _freshness_bonus(days, policy):
    if days is None:
        return 0
    for limit, bonus in policy["freshness_bonus"]:
        if days <= limit:
            return bonus
    return policy["freshness_bonus"][-1][1]


def compute(jd: str, policy: dict = None) -> dict:
    policy = policy or load_policy()
    rescore = PROMPTS / f"{jd}_PREP" / "resp" / "ats_rescore_response.txt"
    meta = jd_meta.get_meta(jd)
    if not rescore.exists():
        return {"jd": jd, "status": "not_scored", "reason": "no Stage 9 rescore yet", "meta": meta}

    rtext = rescore.read_text(encoding="utf-8", errors="replace")
    subs = ats_parse.extract_subscores(rtext)
    interview, domain = subs["interview_probability"], subs["domain_alignment"]
    if interview is None or domain is None:
        return {"jd": jd, "status": "not_scored", "reason": "rescore missing Interview Probability or Domain Alignment",
                "meta": meta}
    shortlist = ats_parse.extract_shortlist(rtext)
    gaps = ats_parse.count_critical_gaps(rtext)

    jd_text, _ = jd_meta.jd_raw_text(jd)
    resume, resume_src = resume_for(jd)
    ko = knockouts(jd_text, resume, policy, meta.get("knockout_overrides") or {})

    fresh_days = None
    if meta.get("posted_date"):
        try:
            fresh_days = (date.today() - date.fromisoformat(meta["posted_date"])).days
        except ValueError:
            fresh_days = None

    jd_fam, res_fam = title_family(meta.get("role_title")), title_family(resume_title(resume))
    mismatch = bool(jd_fam and res_fam and jd_fam != res_fam)

    base = policy["w_interview"] * interview + policy["w_domain"] * domain
    comp = {
        "base": round(base, 1),
        "shortlist_bonus": policy["shortlist_bonus"].get(shortlist or "MAYBE", 0),
        "freshness_bonus": _freshness_bonus(fresh_days, policy),
        "gap_penalty": min(policy["gap_penalty_each"] * (gaps or 0), policy["gap_penalty_cap"]),
        "title_penalty": policy["title_mismatch_penalty"] if mismatch else 0,
    }
    score = max(0, min(100, round(comp["base"] + comp["shortlist_bonus"] + comp["freshness_bonus"]
                                  - comp["gap_penalty"] - comp["title_penalty"])))
    failed = [k for k, v in ko.items() if v["result"] == "FAIL"]
    unknown = [k for k, v in ko.items() if v["result"] == "UNKNOWN"]
    return {
        "jd": jd,
        "status": "scored",
        "callback_score": score,
        "threshold": policy["threshold"],
        "gated_in": score >= policy["threshold"] and not failed,
        "failed_knockouts": failed,
        "unknown_knockouts": unknown,
        "knockouts": ko,
        "title_match": {"jd_title": meta.get("role_title"), "jd_family": jd_fam,
                        "resume_family": res_fam, "mismatch": mismatch},
        "inputs": {"interview_prob": interview, "domain": domain, "shortlist": shortlist,
                   "critical_gaps": gaps, "freshness_days": fresh_days, "resume_source": resume_src},
        "components": comp,
        "meta": meta,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write(result: dict) -> Path:
    out = PROMPTS / f"{result['jd']}_PREP" / "resp" / "10_callback_score.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    return out


def _jd_sort(s):
    return int(re.sub(r"\D", "", s) or 0)


def _main(argv):
    if not argv:
        print(__doc__)
        return 1
    write_file = "--no-write" not in argv
    args = [a for a in argv if not a.startswith("--no-")]
    if args[0] == "--all":
        jds = sorted({p.parent.parent.name.replace("_PREP", "")
                      for p in PROMPTS.glob("JD*_PREP/resp/ats_rescore_response.txt")}, key=_jd_sort)
        print(f"{'JD':5} {'score':>5}  gate  knockouts(FAIL/UNKNOWN)          title")
        for jd in jds:
            r = compute(jd)
            if r["status"] != "scored":
                print(f"{jd:5}   --   {r['reason']}")
                continue
            if write_file:
                write(r)
            ko = ",".join([f"{k}=FAIL" for k in r["failed_knockouts"]] + [f"{k}=?" for k in r["unknown_knockouts"]]) or "-"
            t = r["title_match"]
            print(f"{jd:5} {r['callback_score']:5}  {'IN ' if r['gated_in'] else 'out'}   {ko:34} "
                  f"{t['jd_family']}/{t['resume_family']}{' MISMATCH' if t['mismatch'] else ''}")
        return 0
    r = compute(args[0])
    print(json.dumps(r, indent=2))
    if write_file and r["status"] == "scored":
        print(f"\nWrote {write(r).relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
