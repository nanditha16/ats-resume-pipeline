#!/usr/bin/env python3
"""
parse_diagnostics.py -- why does a JD have no (or too few) Stage 6 review cards?

The review page used to say only "No candidate changes parsed for this JD."
That is a dead end: it cannot tell a missing file from a truncated response
from a model that changed its output format (all three have happened in this
project). This module inspects the raw Stage 3/4 responses next to what the
parser extracted and says which it is.

Format-agnostic on purpose: it lists the bold labels and headings the response
ACTUALLY uses, so an unfamiliar format shows up as data instead of a guess.

CLI:
  python3 scripts/parse_diagnostics.py JD9
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
PROMPTS = ROOT / "prompts"
MAX_OUTPUT_TOKENS = 8192            # claude_execute.py's hardcoded max_tokens

# Plain "Current:" as well as bold "**Current:**": the Stage 3 prompt asks for plain labels.
S3_MARKERS = {"current": r"(?im)^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?Current(?:\*\*)?[ \t]*:",
              "paraphrase": r"(?im)^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?Paraphrase(?:\*\*)?[ \t]*:",
              "why": r"(?im)^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?Why(?:\*\*)?[ \t]*:"}
S4_MARKERS = {"phase_b": r"(?im)^#{1,3}\s*Phase B", "gap_heading": r"(?im)^#{2,3}\s*Gap Addressed:", "numbered_gap": r"\*\*\d+\.\s*Gap Addressed:\*\*",
              "recommendation": r"(?im)^#{2,3}\s*Recommendation\s*\d+", "new_bullet": r"\*\*New Bullet:\*\*",
              "proposed_addition": r"\*\*Proposed Addition", "blockquote_lines": r"(?m)^\s*>\s+\S"}


def _read(p: Path):
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def _labels(text: str, top: int = 10):
    """Most common bold labels ('**Something:**') and markdown headings in a response."""
    bold, heads = {}, {}
    for m in re.finditer(r"\*\*([A-Za-z][A-Za-z /&()-]{1,40}):\*\*", text):
        bold[m.group(1)] = bold.get(m.group(1), 0) + 1
    for m in re.finditer(r"(?m)^(#{1,4})\s+(.{1,70})$", text):
        key = re.sub(r"\d+", "N", m.group(2).strip())
        heads[key] = heads.get(key, 0) + 1
    f = lambda d: sorted(d.items(), key=lambda kv: -kv[1])[:top]
    return {"bold_labels": f(bold), "headings": f(heads)}


def _header_out_tokens(text: str):
    """claude_execute writes '<!-- Cost: ... (~X in + ~Y out tokens) -->' at the top."""
    m = re.search(r"~([\d,]+)\s+in\s*\+\s*~([\d,]+)\s+out tokens", text[:600])
    return int(m.group(2).replace(",", "")) if m else None


def _stage(name: str, text, markers: dict, parsed_count: int, kind: str) -> dict:
    d = {"stage": name, "file_present": text is not None, "parsed": parsed_count, "problems": []}
    if text is None:
        d["problems"].append(f"{name} response file is missing")
        return d
    d["chars"] = len(text)
    d["lines"] = text.count("\n") + 1
    d["marker_counts"] = {k: len(re.findall(rx, text)) for k, rx in markers.items()}
    d["seen"] = _labels(text)
    out_tok = _header_out_tokens(text)
    d["output_tokens"] = out_tok
    if len(text.strip()) < 200:
        d["problems"].append(f"{name} response is nearly empty ({len(text.strip())} chars); re-run the stage")
    if re.search(r"<!--\s*TRUNCATED", text[:1200]):
        d["problems"].append(f"{name} was cut off: the API reported stop_reason=max_tokens, so later sections "
                             f"(for example True Gaps and Flags) are missing. Raise max_output_tokens for this stage and re-run")
    elif out_tok and out_tok >= MAX_OUTPUT_TOKENS - 64 and not re.search(r"<!--\s*Stop:", text[:1200]):
        # older responses only: the header's counts were estimates, so this is a guess, not a fact
        d["problems"].append(f"{name} may have hit the {MAX_OUTPUT_TOKENS}-token output limit (estimated from length); "
                             f"the response may be truncated")
    known = sum(d["marker_counts"].values())
    if parsed_count == 0 and len(text.strip()) >= 200:
        if known == 0:
            d["problems"].append(f"{name} response uses a format the parser does not recognise (none of the expected "
                                 f"{kind} markers found). See 'seen' for the labels it does use")
        else:
            d["problems"].append(f"{name} has {known} expected marker(s) but 0 parsed items; markers are present "
                                 f"but not in the structure the parser needs")
    return d


def diagnose(jd: str, edits=None, recs=None, s2=None) -> dict:
    """Pass already-parsed edits/recs/s2 to avoid parsing twice; otherwise parsed here."""
    resp = PROMPTS / f"{jd}_PREP" / "resp"
    s2_text = _read(resp / "ats_prompt_response.txt")
    s3_text = _read(resp / "ats_recommend_prompt_response.txt")
    s4_text = _read(resp / "ats_evidence_gap_response.txt")
    if edits is None or recs is None or s2 is None:
        # Only import the parser when asked to parse here; synthesize_resume
        # passes its own results in, which avoids importing itself.
        sys.path.insert(0, str(Path(__file__).parent))
        import synthesize_resume as sr
    if edits is None:
        edits = sr.parse_stage3(s3_text)[0] if s3_text else []
    if recs is None:
        recs = sr.parse_stage4(s4_text) if s4_text else []
    if s2 is None and s2_text:
        s2 = sr.parse_stage2(s2_text)
    s3 = _stage("Stage 3", s3_text, S3_MARKERS, len(edits), "Current/Paraphrase/Why")
    s4 = _stage("Stage 4", s4_text, S4_MARKERS, len(recs), "Phase B recommendation")
    problems = s3["problems"] + s4["problems"]
    # A Flags section the page could not read looks exactly like "no conflicts" unless it is said out loud.
    if s4_text:
        sys.path.insert(0, str(Path(__file__).parent))
        import synthesize_resume as _sr
        fm = [m for m in re.finditer(r"(?im)^#{1,3}[^\n]*\bflags?\b[^\n]*$", s4_text)]
        if fm and not _sr.parse_stage4_flags(s4_text):
            tail = s4_text[fm[-1].end():]
            nxt = re.search(r"(?m)^#{1,3}\s", tail)
            body = (tail[:nxt.start()] if nxt else tail).strip()
            if len(body) >= 60:       # "None found." is a legitimate, empty Flags section
                problems.append(f"Stage 4 has a Flags section ({fm[-1].group(0).strip()[:70]!r}) with {len(body)} characters of "
                                f"content but no items could be read from it, so its evidence-vs-resume conflicts are not "
                                f"shown. Check the section's format")
    summary = ("; ".join(problems) if problems else
               ("parsed normally" if (len(edits) + len(recs)) else
                "both responses look well-formed but contain no changes (the model proposed nothing)"))
    return {"jd": jd, "edits_parsed": len(edits), "recs_parsed": len(recs), "verdict": (s2 or {}).get("verdict"),
            "stage3": s3, "stage4": s4, "problems": problems, "summary": summary}


def _main(argv):
    if not argv:
        print(__doc__)
        return 1
    d = diagnose(argv[0])
    print(f"{d['jd']}: Stage 3 parsed {d['edits_parsed']} edit(s), Stage 4 parsed {d['recs_parsed']} recommendation(s), "
          f"Stage 2 verdict {d['verdict']}")
    for st in (d["stage3"], d["stage4"]):
        if not st["file_present"]:
            print(f"\n{st['stage']}: FILE MISSING")
            continue
        print(f"\n{st['stage']}: {st['chars']:,} chars, {st['lines']} lines, output tokens {st['output_tokens']}")
        print(f"  expected markers found: {st['marker_counts']}")
        print(f"  bold labels in response: {st['seen']['bold_labels']}")
        print(f"  headings in response:    {st['seen']['headings']}")
    print(f"\nDIAGNOSIS: {d['summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
