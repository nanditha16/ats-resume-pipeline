#!/usr/bin/env python3
"""
variant_pick.py -- which resume variant did the Stage 0/1 ranking response choose?

batch_prep.sh's extract_variant() looks for seven specific shapes (a bold filename on the
"Top Pick" line, a "| 1 |" table row, ...). A real response for JD11 matched none of them:
  - the rank-1 rows read "| 1 (tie) |", not "| 1 |"
  - the section was "## Top Pick", and the pick was named in prose ("The Acme variant (72).")
so the batch stopped with "Cannot extract variant". This parser reads the response the way a
person would, and is only consulted when the original patterns find nothing.

Decision order:
  1. Rank-1 rows of the ranking table ("1", "1 (tie)", "1=", "T1", "**1**").
  2. If the Top Pick section names one of those variants, that one wins a tie.
  3. A tie with no tiebreak takes the first listed (and says so on stderr).
  4. No rank-1 row: whichever variant the Top Pick section names first.
Only variants that exist in output/resume/ are ever returned.

CLI:  python3 scripts/variant_pick.py RESPONSE.txt [output/resume]
      prints the variant name (file stem); notes go to stderr; empty output = could not decide
"""
import re
import sys
from pathlib import Path


def _clean(cell: str) -> str:
    return re.sub(r"[*`]", "", cell).strip()


def _stem(cell: str) -> str:
    return re.sub(r"\.md$", "", _clean(cell)).strip()


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s).lower()


def table_rows(text: str) -> list:
    """[(rank, is_tie, variant_cell)] from any pipe-table row whose first cell starts with a rank."""
    rows = []
    for ln in text.splitlines():
        if not ln.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2 or re.fullmatch(r"[:\- ]+", cells[0]):
            continue
        m = re.match(r"^[*\s]*[Tt]?(\d{1,2})\b(.*)$", cells[0])
        if not m:
            continue
        rows.append((int(m.group(1)), bool(re.search(r"tie|=", m.group(2), re.I)), cells[1]))
    return rows


def top_pick_text(text: str) -> str:
    """The body of the Top Pick section (any heading level, or a bold 'Top Pick:' label)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^\s*(?:#{1,6}\s*|\*\*\s*)top pick\b", ln, re.I):
            body = [re.sub(r"^\W*top pick\W*", "", ln, flags=re.I)]
            for nxt in lines[i + 1:]:
                if re.match(r"^\s*#{1,6}\s", nxt):
                    break
                body.append(nxt)
            return "\n".join(body).strip()[:900]
    return ""


def _owner_prefix() -> str:
    """`variant_owner_prefix` (a regex) from policy/evidence_policy.local.json, then policy/evidence_policy.json; '' when unset."""
    import json
    import os
    root = Path(os.environ.get("ATS_ROOT", Path(__file__).resolve().parent.parent))
    out = ""
    for name in ("evidence_policy.json", "evidence_policy.local.json"):
        try:
            out = json.loads((root / "policy" / name).read_text()).get("variant_owner_prefix", out) or out
        except (OSError, ValueError):
            pass
    return out


def variant_tokens(stem: str) -> list:
    """What a person would call the variant: 'Jane_Doe_Resume_Umbrella_Corp' -> ['Umbrella Corp', 'Umbrella_Corp']."""
    prefix = _owner_prefix()
    t = (re.sub(prefix, "", stem, flags=re.I) if prefix else re.sub(r"^.*?resume[_ ]*", "", stem, count=1, flags=re.I)) or stem
    t = re.sub(r"^[Rr]esume[_ ]*", "", t)
    return list({t, t.replace("_", " ")})


def pick(text: str, resume_dir: Path) -> tuple:
    """(stem, method, tied_with). stem is '' if no decision."""
    files = {p.stem: p for p in resume_dir.glob("*.md")} if resume_dir.exists() else {}
    byname = {_norm(s): s for s in files}

    def resolve(cell: str):
        """Full filename, or the short name a table may use ('Initech', 'Umbrella_Corp')."""
        s = _norm(_stem(cell))
        if not s:
            return None
        if s in byname:
            return byname[s]
        exact = [st for st in files if any(_norm(t) == s for t in variant_tokens(st))]
        if len(exact) == 1:
            return exact[0]
        ends = [st for st in files if _norm(st).endswith(s)]
        return ends[0] if len(ends) == 1 else None

    rank1 = []
    for rank, tie, cell in table_rows(text):
        r = resolve(cell)
        if rank == 1 and r and r not in rank1:
            rank1.append(r)

    named, best = None, 10 ** 9
    section = top_pick_text(text)
    if section:
        for stem in files:
            for tok in variant_tokens(stem):
                tok_pat = re.escape(tok).replace("\\ ", "[ _-]")      # a space in a name matches space, underscore or hyphen
                m = re.search(rf"(?<![A-Za-z0-9]){tok_pat}(?![A-Za-z0-9])", section, re.I)
                if m and (m.start() < best or (m.start() == best and named and len(tok) > len(named))):
                    best, named = m.start(), stem
                    if m.start() == 0:
                        break

    if rank1:
        if named in rank1:
            return named, "rank-1 row, tie broken by Top Pick" if len(rank1) > 1 else "rank-1 row", [r for r in rank1 if r != named]
        return rank1[0], "rank-1 row" + (" (tie, first listed)" if len(rank1) > 1 else ""), rank1[1:]
    if named:
        return named, "named in Top Pick", []
    return "", "", []


def _main(argv):
    if not argv:
        print(__doc__)
        return 1
    text = Path(argv[0]).read_text(encoding="utf-8", errors="replace")
    stem, method, tied = pick(text, Path(argv[1] if len(argv) > 1 else "output/resume"))
    if stem:
        print(stem)
        print(f"variant_pick: {method}" + (f"; also rank 1: {', '.join(tied)}" if tied else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
