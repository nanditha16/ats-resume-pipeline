#!/usr/bin/env python3
"""
jd_meta.py -- company, role title, posting date and job URL for a JD.

Revised decision D10 (Stage 10 plan): this data is mostly ALREADY on disk in
input/other/JDx.txt, so it is derived, not typed in. Surveyed across all 28
real JDs before writing this:
  - job URL ("Link: ..." line)        28/28
  - company from the URL              most (Workday subdomain, Ashby /
                                       SmartRecruiters path, careers.<co>.com);
                                       NOT derivable from Oracle Cloud, ADP,
                                       SAP SuccessFactors, Dayforce, LinkedIn,
                                       Haystack links
  - title from the first line         most; JD4 "Overview", JD18 "Background",
                                       JD24 "About the job" need the fallback
  - posting date ("Posted ...")       6/28
Whatever cannot be derived is left null and flagged; the user fills it once
in prompts/JD_Analysis/JDx/.jd_meta.json (edited from the Stage 10 page),
and that override always wins.

CLI:
  python3 scripts/jd_meta.py JD7          # print derived + override, with sources
  python3 scripts/jd_meta.py --all        # table for every JD in input/other
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("ATS_ROOT", "/app"))
if not ROOT.exists():
    ROOT = Path(__file__).parent.parent.resolve()
INPUT_OTHER = ROOT / "input" / "other"
OUTPUT = ROOT / "output"
PROMPTS = ROOT / "prompts"

# URL hosts that are job-board platforms, not the employer. For these the
# company is taken from the first path segment (Ashby, SmartRecruiters,
# Lever, Greenhouse) or not derivable at all (the rest).
PATH_COMPANY_HOSTS = {"jobs.ashbyhq.com", "jobs.smartrecruiters.com", "jobs.lever.co",
                      "boards.greenhouse.io", "job-boards.greenhouse.io"}
NO_COMPANY_HOSTS = ("oraclecloud.com", "workforcenow.adp.com", "sapsf.com", "dayforcehcm.com",
                    "linkedin.com", "haystack.cv", "indeed.com", "google.com")
# First lines that are page chrome, not a job title.
TITLE_SKIP = {"overview", "application", "apply", "apply now", "background", "about the job",
              "job description", "about us", "about the role", "description"}
ROLE_WORD = r"(?:Manager|Management|Director|Engineer|Lead|Leader|Consultant|Analyst|Architect|Head|VP|Owner|PM|TPM)"


def meta_override_path(jd: str) -> Path:
    return PROMPTS / "JD_Analysis" / jd / ".jd_meta.json"


def jd_raw_text(jd: str):
    """Raw JD text: input/other first (has the Link / Posted lines), then the
    converted markdown in output/ or output/_archive/."""
    for p in (INPUT_OTHER / f"{jd}.txt", OUTPUT / f"{jd}.md", OUTPUT / "_archive" / f"{jd}.md"):
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace"), p
    return "", None


def _job_url(text: str):
    links = re.findall(r"Link:\s*(https?://\S+)", text)
    return links[-1] if links else None


def _company_from_url(url: str):
    if not url:
        return None
    m = re.match(r"https?://([^/]+)(/[^?#]*)?", url)
    if not m:
        return None
    host, path = m.group(1).lower(), (m.group(2) or "")
    if host in PATH_COMPANY_HOSTS:
        seg = [x for x in path.split("/") if x]
        return _pretty(seg[0]) if seg else None
    if any(host.endswith(h) for h in NO_COMPANY_HOSTS):
        return None
    wd = re.match(r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", host)
    if wd:
        return wd.group(1).upper() if len(wd.group(1)) <= 4 else wd.group(1).capitalize()
    parts = host.split(".")
    parts = [p for p in parts if p not in ("www", "jobs", "careers", "career", "apply")]
    if len(parts) < 2:
        return None
    name = re.sub(r"careers?$", "", parts[-2]) or parts[-2]
    return name.upper() if len(name) <= 4 else name.capitalize()


def _pretty(slug: str) -> str:
    """'turquoise-health' -> 'Turquoise Health', 'UniversityHealthNetwork' ->
    'University Health Network'. Cosmetic only; override if casing matters."""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug.replace("-", " ").replace("_", " "))
    return " ".join(w if w.isupper() else w.capitalize() for w in s.split())


def _clean_title(line: str) -> str:
    line = re.sub(r"^Career Opportunities:\s*", "", line.strip(), flags=re.IGNORECASE)
    line = re.sub(r"\s*\(\d{3,}\)\s*$", "", line)          # trailing req numbers "(13585)"
    return line.strip()


def _title(text: str):
    """First of the opening 4 non-empty lines that looks like a title: not page
    chrome ("Overview", "Background"), not a sentence, <= 12 words, and
    containing a role word. Falls back to the first capitalised role phrase in
    the prose (JD4: "... seeking a ... Senior Product Manager to lead ...").
    Phrase matching uses literal spaces, never \\s, so it cannot run across
    a line break (a real JD18 bug: "Background\\nSenior Manager")."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for ln in lines[:4]:
        cand = _clean_title(ln)
        if (cand.lower() in TITLE_SKIP or cand.endswith(".") or cand.lower().startswith("link:")
                or len(cand.split()) > 12):
            continue
        if re.search(rf"\b{ROLE_WORD}\b", cand):
            return cand, "first_lines"
    m = re.search(rf"\b((?:[A-Z][A-Za-z/&-]+ ){{0,5}}{ROLE_WORD}(?: of the (?:[A-Z][A-Za-z]* ){{1,3}}Team)?)\b",
                  "\n".join(lines[:80]))
    if m and len(m.group(1).split()) >= 2:
        return m.group(1).strip(), "prose"
    return None, None


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def _posted(text: str, captured_on: date):
    """Posting date from 'Posted N Days Ago', '30+ days ago', 'Posted 09/08/2026',
    'Posted Thursday, September 10, 2026'. Relative forms are resolved against
    the day the JD file was saved (captured_on)."""
    m = re.search(r"(?:Posted\s+)?(\d+)\+?\s+(day|week|hour)s?\s+ago", text, re.IGNORECASE)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * 7 if unit == "week" else (0 if unit == "hour" else n)
        return captured_on - timedelta(days=days), "relative_to_file_date"
    m = re.search(r"Posted\s+(?:on\s+)?(\d{1,2})/(\d{1,2})/(\d{4})", text, re.IGNORECASE)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2))), "absolute"
        except ValueError:
            pass
    m = re.search(rf"Posted\s+(?:\w+,\s+)?({MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})", text, re.IGNORECASE)
    if m:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date(), "absolute"
    return None, None


def load_override(jd: str) -> dict:
    p = meta_override_path(jd)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def save_override(jd: str, updates: dict) -> dict:
    """Merge user-entered fields into .jd_meta.json. Empty strings clear a field."""
    allowed = {"company", "role_title", "posted_date", "job_url", "knockout_overrides", "posted_date_note"}
    cur = load_override(jd)
    for k, v in updates.items():
        if k not in allowed:
            continue
        if v in ("", None):
            cur.pop(k, None)
        else:
            cur[k] = v
    p = meta_override_path(jd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, indent=2))
    return cur


def get_meta(jd: str) -> dict:
    """Derived metadata with the user override applied. Each field has a
    matching *_source ('override', 'first_line', 'url', ...) or None."""
    text, path = jd_raw_text(jd)
    captured_on = date.fromtimestamp(path.stat().st_mtime) if path else date.today()
    url = _job_url(text)
    title, title_src = _title(text)
    posted, posted_src = _posted(text, captured_on)
    meta = {
        "jd": jd,
        "company": _company_from_url(url), "company_source": "url" if _company_from_url(url) else None,
        "role_title": title, "role_title_source": title_src,
        "posted_date": posted.isoformat() if posted else None, "posted_date_source": posted_src,
        "job_url": url, "job_url_source": "link_line" if url else None,
        "knockout_overrides": {},
    }
    # Relative dates ("Posted 29 Days Ago") depend on the file's modification
    # time, which any copy, sync or checkout resets -- a real test moved JD5's
    # posting date forward a week that way. Freeze the first derivation into
    # the override file so the date can never drift afterwards.
    override = load_override(jd)
    if posted_src == "relative_to_file_date" and "posted_date" not in override:
        save_override(jd, {"posted_date": meta["posted_date"],
                           "posted_date_note": f"derived from relative text, frozen {date.today().isoformat()}"})
        override = load_override(jd)
    for k, v in override.items():
        if k == "posted_date_note":
            continue
        meta[k] = v
        if k != "knockout_overrides":
            meta[f"{k}_source"] = "override"
    if override.get("posted_date_note") and "posted_date" in override:
        meta["posted_date_source"] = "relative_frozen"
    meta["missing"] = [k for k in ("company", "role_title", "posted_date", "job_url") if not meta.get(k)]
    return meta


def _main(argv):
    if not argv:
        print(__doc__)
        return 1
    jds = sorted((p.stem for p in INPUT_OTHER.glob("JD*.txt")), key=lambda s: int(re.sub(r"\D", "", s) or 0)) \
        if argv[0] == "--all" else argv
    for jd in jds:
        m = get_meta(jd)
        if len(jds) == 1:
            print(json.dumps(m, indent=2))
        else:
            print(f"{jd:5} | {str(m['company']):18} | {str(m['role_title'])[:48]:48} | "
                  f"{str(m['posted_date']):10} | missing: {', '.join(m['missing']) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
