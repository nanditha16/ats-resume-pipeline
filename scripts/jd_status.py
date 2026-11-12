#!/usr/bin/env python3
"""
jd_status.py -- one line per JD: is it ready to review, or does a stage need re-running? READ-ONLY.

Opening every review page to find out is slow, and the answer is mostly mechanical. For each JD with a prompts/JDn_PREP folder this
builds the same manifest the review page uses (nothing is written) and prints:

  JD  verdict  cards (ticked)  evidence  notes  ->  what to do

What to do, in priority order:
  - a response file is missing           -> run that stage
  - the page reports a response problem  -> re-run that stage (cut off, or its format cannot be read)
  - Stage 4 was written before your evidence last changed (or cites files that no longer exist) -> re-run Stage 4 only
  - Stage 2 verdict NO                   -> skipped by design (poor fit)
  - otherwise                            -> review

Stage 3 never uses the evidence, so a stale Stage 4 does not mean Stage 3 needs re-running.

  python3 scripts/jd_status.py            all JDs
  python3 scripts/jd_status.py JD1 JD9    just these
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("ATS_ROOT", "/app"))
if not ROOT.exists():
    ROOT = Path(__file__).parent.parent.resolve()
RESP = {"2": "ats_prompt_response.txt", "3": "ats_recommend_prompt_response.txt", "4": "ats_evidence_gap_response.txt"}


def jd_names(only):
    names = sorted({re.search(r"JD\d+", p.name).group() for p in (ROOT / "prompts").glob("JD*_PREP") if re.search(r"JD\d+", p.name)},
                   key=lambda n: int(n[2:]))
    return [n for n in names if not only or n in only]


def manifest(jd):
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "synthesize_resume.py"), jd, "--manifest-only"],
                       capture_output=True, text=True, cwd=ROOT, env={**os.environ, "ATS_ROOT": str(ROOT)})
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"ok": False, "error": (r.stderr or r.stdout)[-160:].strip()}


def status(jd):
    prep = ROOT / "prompts" / f"{jd}_PREP" / "resp"
    missing = [s for s, f in RESP.items() if not (prep / f).exists()]
    if missing:
        return {"jd": jd, "verdict": "-", "cards": "-", "evid": "-", "notes": "",
                "todo": "run Stage " + " and ".join(missing)}
    m = manifest(jd)
    if not m.get("ok"):
        err = str(m.get("error", ""))[:90]
        return {"jd": jd, "verdict": "-", "cards": "-", "evid": "-", "notes": "", "todo": f"cannot build the page: {err}"}
    verdict = (m.get("s2") or {}).get("verdict") or "-"
    ch = m.get("changes", [])
    ticked = sum(1 for c in ch if c.get("default_accepted"))
    probs = [p for p in (m.get("diagnostics") or {}).get("problems", []) if "Stage 2 verdict is NO" not in p]
    st = m.get("evidence_staleness") or {}
    au = m.get("evidence_audit") or {}
    notes = []
    if au.get("indexed"):
        notes.append(f"{len(au.get('unbacked', []))} unbacked")
        if au.get("dnc_total"):
            notes.append(f"{au['dnc_total']} do-not-claim")
    for key, label in (("date_conflicts", "date"), ("titles", "title")):
        if m.get(key):
            notes.append(f"{len(m[key])} {label}")
    if m.get("unverified_skills"):
        notes.append(f"{len(m['unverified_skills'])} unlisted skills")
    if not au.get("indexed"):
        todo = "build the evidence index first (run ingest_evidence.sh)"
    elif verdict == "NO":
        todo = "skip (Stage 2 verdict NO: poor fit)"
    elif probs:
        which = "Stage 4" if any("Stage 4" in p for p in probs) else "Stage 3"
        todo = f"re-run {which}: {probs[0][:70]}"
    elif st.get("stale"):
        todo = "re-run Stage 4 only (written before your evidence changed)"
    else:
        todo = "review"
    return {"jd": jd, "verdict": verdict, "cards": f"{len(ch)} ({ticked})", "evid": "stale" if st.get("stale") else "current",
            "notes": ", ".join(notes), "todo": todo}


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    rows = [status(n) for n in jd_names(set(argv))]
    if not rows:
        print("No JDs with a prompts/JDn_PREP folder.")
        return 1
    print(f"{'JD':6}{'verdict':9}{'cards (ticked)':16}{'Stage 4':9}{'what the page will say':62}-> what to do")
    for r in rows:
        print(f"{r['jd']:6}{r['verdict']:9}{r['cards']:16}{r['evid']:9}{r['notes'][:60]:62}-> {r['todo']}")
    ready = sum(1 for r in rows if r["todo"] == "review")
    print(f"\n{ready} of {len(rows)} ready to review; the rest need the action shown.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
