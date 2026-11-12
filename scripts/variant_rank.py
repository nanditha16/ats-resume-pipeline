#!/usr/bin/env python3
"""
variant_rank.py — Stage 1 of the multi-variant ATS workflow.

Ranks resume variants by semantic similarity to a JD, comparing the JD
ONLY against each resume's role-specific sections (Summary, Career
Highlights, Professional Experience, AI/project sections) — excluding
boilerplate sections (Core Competencies, Technical Stack, Education,
Certifications) that are near-identical across all variants.

WHY THIS MATTERS (found via direct measurement, not assumption):
Two genuinely different resume variants (built for different companies)
measured at 0.81 cosine similarity to EACH OTHER on whole-document
embeddings, while either one was only 0.27-0.34 similar to a real JD. The
shared boilerplate (same name, same skills list, same certs) was
drowning out the actual JD-relevant signal. Excluding boilerplate before
embedding directly targets that problem.

Section matching is heading-text-based (case-insensitive substring match),
not position-based — this mirrors smart_chunk.sh's own heading detection
(any ALL-CAPS line or markdown heading) rather than assuming a fixed
part-number-to-section mapping, since heading order/count can vary
between variants and a hardcoded position would silently misread content
the moment one variant adds or removes a section.

Usage:
    python variant_rank.py "<jd text>" output/resume/
"""
from sentence_transformers import SentenceTransformer
from pathlib import Path
import numpy as np
import re
import sys

# Sections to EXCLUDE from comparison — boilerplate shared across variants.
# Matched as case-insensitive substrings against each detected heading, so
# "CAREER HIGHLIGHT" and "CAREER HIGHLIGHTS" both match correctly without
# needing an exact-string list per variant.
BOILERPLATE_HEADINGS = [
    "core competencies",
    "technical stack",
    "education",
    "certifications",
    "work authorization",
]

HEADING_PATTERN = re.compile(r"^([A-Z][A-Z ]+)$|^#{1,6}\s+(.+)$")


def extract_role_specific_sections(text):
    """
    Split a resume's markdown into (heading, body) sections using the same
    heading-detection rule as smart_chunk.sh (ALL-CAPS line or markdown
    heading), then return only the body text of sections NOT in
    BOILERPLATE_HEADINGS. Falls back to the full text if no headings are
    detected at all (so a differently-formatted resume still gets compared
    rather than silently producing an empty string).
    """
    lines = text.split("\n")
    sections = []  # list of (heading, [body_lines])
    current_heading = None
    current_body = []

    for line in lines:
        stripped = line.strip()
        match = HEADING_PATTERN.match(stripped) if stripped else None
        if match:
            if current_heading is not None:
                sections.append((current_heading, current_body))
            current_heading = (match.group(1) or match.group(2)).strip()
            current_body = []
        else:
            current_body.append(line)

    if current_heading is not None:
        sections.append((current_heading, current_body))

    if not sections:
        # No headings detected — don't silently return nothing; compare
        # the whole document rather than producing a misleadingly empty
        # (and therefore zero-similarity) result.
        return text

    kept_body_text = []
    for heading, body in sections:
        heading_lower = heading.lower()
        is_boilerplate = any(b in heading_lower for b in BOILERPLATE_HEADINGS)
        if not is_boilerplate:
            kept_body_text.append(heading)
            kept_body_text.extend(body)

    result = "\n".join(kept_body_text).strip()
    # Safety net: if section-filtering happened to strip everything (e.g.
    # a resume that's ALL boilerplate-labeled, unlikely but possible),
    # fall back to the full text rather than comparing against nothing.
    return result if result else text


query = sys.argv[1] if len(sys.argv) > 1 else None
variant_dir = sys.argv[2] if len(sys.argv) > 2 else None

if not query or not variant_dir:
    print("❌ Usage: python variant_rank.py \"<jd text>\" <variant_directory>")
    exit(1)

variant_path = Path(variant_dir)
if not variant_path.is_dir():
    print(f"❌ Directory not found: {variant_dir}")
    exit(1)

print("🧠 Ranking resume variants against JD (role-specific section similarity)...")

model = SentenceTransformer("all-MiniLM-L6-v2")

documents = []
files = []
excluded_sections_log = []

for f in sorted(variant_path.glob("*.md")):
    full_text = f.read_text(encoding="utf-8").strip()
    if not full_text:
        continue
    role_specific = extract_role_specific_sections(full_text)
    documents.append(role_specific)
    files.append(f)
    # Track how much was stripped, for transparency in the output
    excluded_pct = 100 * (1 - len(role_specific) / max(len(full_text), 1))
    excluded_sections_log.append((f.name, excluded_pct))

if not documents:
    print(f"❌ No .md files found in {variant_dir}")
    print("   (If your variants are .pdf, convert them first — this script")
    print("   only reads .md, same as the rest of the pipeline.)")
    exit(1)

doc_embeddings = model.encode(documents, convert_to_numpy=True, normalize_embeddings=True)
query_embedding = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]

scores = np.dot(doc_embeddings, query_embedding)
ranked_indices = np.argsort(scores)[::-1]

output_file = "variant_ranking.txt"

with open(output_file, "w", encoding="utf-8") as out:
    out.write("RESUME VARIANT RANKING\n")
    out.write("=" * 50 + "\n\n")
    out.write("Ranked by semantic similarity to the JD, comparing ONLY\n")
    out.write("role-specific sections (Summary, Career Highlights,\n")
    out.write("Professional Experience, project sections) — boilerplate\n")
    out.write("(Core Competencies, Technical Stack, Education,\n")
    out.write("Certifications) excluded to avoid the shared-boilerplate\n")
    out.write("problem confirmed in earlier measurement (two different\n")
    out.write("variants scored 0.81 similar to EACH OTHER on whole-\n")
    out.write("document embeddings, swamping the actual JD signal).\n")
    out.write("Still a coarse signal, not a hard-requirement check —\n")
    out.write("use Stage 2 (ats_optimize.sh) on your top pick(s) for an\n")
    out.write("actual scored assessment.\n\n")

    for rank, idx in enumerate(ranked_indices, 1):
        out.write(f"{rank}. {files[idx].name}  (similarity: {scores[idx]:.4f})\n")

    out.write("\n" + "-" * 50 + "\n")
    out.write("Boilerplate excluded per file (% of text removed before scoring):\n")
    for name, pct in excluded_sections_log:
        out.write(f"  {name}: {pct:.0f}% excluded\n")

print(f"✅ Ranking saved to {output_file}\n")
with open(output_file, "r", encoding="utf-8") as f:
    print(f.read())
