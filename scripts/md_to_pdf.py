#!/usr/bin/env python3
"""
md_to_pdf.py — Convert a markdown file to a presentable PDF using
reportlab (pure Python, no external binary dependency — avoids the
apt-package-availability problems hit with pandoc+wkhtmltopdf, which
isn't installable on Debian Trixie, the current python:3.12-slim base).

Uses Liberation Serif (metrically identical to Times New Roman; the real
Times New Roman is a licensed Microsoft font and can't be redistributed
via apt). Basic **bold** / *italic* markdown is converted to reportlab's
inline markup.

TWO RENDERING PATHS, auto-detected:

  RESUME path — for output/review_resume/{JD}_new_resume.md and
  output/resume/*.md.
  These files use this project's specific resume-bank convention: a bare
  name line, a title line, a contact line, ALL-CAPS section names with no
  '#'/'##' markdown, "Role - Company | dates" role headers, and '•'
  bullets — with NO blank lines separating most of these. Confirmed by
  running the original generic-paragraph renderer against a real output
  file: it produced 4 pages with the name, title, contact line, and
  "SUMMARY" heading all fused into one unreadable paragraph, because the
  generic renderer only splits on blank lines and this format doesn't
  have them between those elements. The resume path parses line-by-line
  against known structural patterns instead of blank-line blocks, reusing
  the exact same KNOWN_SECTIONS list and is_role_header() detector Stage
  5/6 already use — one definition of "what a section header looks like"
  for the whole pipeline, not two that can quietly disagree.

  GENERIC path — unchanged from the original implementation, for cover
  letters, reports, or any other blank-line-separated markdown with real
  '#'/'##' headings.

After rendering, actual page count is read back from the PDF via pypdf
and printed — replacing the review board's ~550-words/page ESTIMATE with
the real number for whatever file was just exported.
"""
import sys
import re
import html
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_CENTER

sys.path.insert(0, __file__.rsplit("/", 1)[0])
try:
    from synthesize_resume import KNOWN_SECTIONS, is_role_header
except ImportError:
    # Fallback copies if synthesize_resume.py isn't importable for some
    # reason (e.g. run from outside scripts/) -- kept in sync manually;
    # the import above is the source of truth.
    KNOWN_SECTIONS = [
        "SUMMARY", "CORE COMPETENCIES", "TECHNICAL STACK", "CAREER HIGHLIGHTS",
        "PROFESSIONAL EXPERIENCE", "AI PLATFORM PROJECT", "AI ENABLED PROJECT",
        "EDUCATION", "CERTIFICATIONS",
    ]

    def is_role_header(line: str) -> bool:
        return "|" in line and bool(re.search(r"\b(19|20)\d{2}\b|Present", line))

FONT_DIR = "/usr/share/fonts/truetype/liberation"

pdfmetrics.registerFont(TTFont("LiberationSerif", f"{FONT_DIR}/LiberationSerif-Regular.ttf"))
pdfmetrics.registerFont(TTFont("LiberationSerif-Bold", f"{FONT_DIR}/LiberationSerif-Bold.ttf"))
pdfmetrics.registerFont(TTFont("LiberationSerif-Italic", f"{FONT_DIR}/LiberationSerif-Italic.ttf"))


def inline_markup(text: str) -> str:
    """Escape for reportlab's mini-XML markup FIRST, then apply **bold**/
    *italic* -> <b>/<i>. Escaping first matters: raw '&' (e.g. "R&D",
    "Java & Cloud") is literal text here, not markup, and reportlab's
    Paragraph parser is XML -- an unescaped '&' is a latent crash (or
    silent mis-render, version-dependent) waiting for the wrong input."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    return text


def is_resume_format(text: str) -> bool:
    """Detected by presence of >=2 of this project's known ALL-CAPS resume
    section names as standalone lines -- specific enough that a cover
    letter or report won't accidentally match, general enough to survive
    which sections a given resume variant happens to include."""
    lines = {ln.strip() for ln in text.splitlines()}
    return sum(1 for s in KNOWN_SECTIONS if s in lines) >= 2


def build_resume_styles():
    # Tightened from an earlier version after a real resume overflowed by
    # just ~6 lines onto a 3rd page. Small reductions spread across many
    # dimensions (margins, leading, inter-element spacing) reclaim that
    # space without any single change reading as cramped -- a single big
    # font-size cut would have been more noticeable than this combination.
    return {
        "name": ParagraphStyle("Name", fontName="LiberationSerif-Bold", fontSize=17,
                                leading=19, spaceAfter=2, alignment=TA_CENTER),
        "title": ParagraphStyle("Title", fontName="LiberationSerif", fontSize=10.5,
                                 leading=12.5, spaceAfter=2, alignment=TA_CENTER),
        "contact": ParagraphStyle("Contact", fontName="LiberationSerif", fontSize=9,
                                   leading=11, spaceAfter=8, alignment=TA_CENTER),
        "section": ParagraphStyle("Section", fontName="LiberationSerif-Bold", fontSize=11,
                                   leading=13, spaceBefore=6, spaceAfter=3,
                                   borderWidth=0, borderPadding=0),
        "role": ParagraphStyle("Role", fontName="LiberationSerif-Bold", fontSize=10,
                                leading=12, spaceBefore=4, spaceAfter=1),
        "bullet": ParagraphStyle("Bullet", fontName="LiberationSerif", fontSize=9.5,
                                  leading=11.3, spaceAfter=2, leftIndent=16,
                                  bulletIndent=4, alignment=TA_LEFT),
        "body": ParagraphStyle("ResumeBody", fontName="LiberationSerif", fontSize=9.5,
                                leading=11.5, spaceAfter=4, alignment=TA_LEFT),
    }


CATEGORY_LABEL_RE = re.compile(r"^[A-Z][A-Za-z0-9 /&]{2,40}:\s")


def render_resume(text: str):
    styles = build_resume_styles()
    lines = [ln.rstrip() for ln in text.split("\n")]
    story = []
    i = 0
    n = len(lines)

    def next_nonblank(idx):
        while idx < n and not lines[idx].strip():
            idx += 1
        return idx

    # Header block: name, title, contact -- first three non-blank lines,
    # by this project's own established resume-bank convention (confirmed
    # against every variant used throughout this pipeline).
    i = next_nonblank(i)
    if i < n:
        story.append(Paragraph(inline_markup(lines[i].strip()), styles["name"]))
        i += 1
    i = next_nonblank(i)
    if i < n and lines[i].strip() not in KNOWN_SECTIONS:
        story.append(Paragraph(inline_markup(lines[i].strip()), styles["title"]))
        i += 1
    i = next_nonblank(i)
    if i < n and lines[i].strip() not in KNOWN_SECTIONS:
        story.append(Paragraph(inline_markup(lines[i].strip()), styles["contact"]))
        i += 1

    # Body loop: this project's resume .md files are PDF-extracted text,
    # which hard-wraps mid-sentence ("...operational stability of
    # enterprise-scale\nJava/API platforms...") -- a naive one-line-one-
    # paragraph renderer turns every such wrap into a visibly separate
    # paragraph with its own spacing (confirmed by rendering: "Object-
    # Oriented" and "Design & Implementation" appeared as two choppy
    # paragraphs instead of one flowing phrase). Plain lines and bullet
    # continuations are accumulated into a buffer and only flushed as one
    # Paragraph when a real structural boundary (section header, role
    # header, or a NEW bullet) is reached.
    pending_kind = None   # "body" | "bullet" | None
    pending_lines = []

    def flush():
        if not pending_lines:
            return
        joined = " ".join(pending_lines).strip()
        if pending_kind == "bullet":
            story.append(Paragraph(inline_markup(joined), styles["bullet"], bulletText="•"))
        else:
            story.append(Paragraph(inline_markup(joined), styles["body"]))
        pending_lines.clear()

    while i < n:
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s:
            flush()
            pending_kind = None
            continue
        if s in KNOWN_SECTIONS:
            flush()
            pending_kind = None
            story.append(Paragraph(inline_markup(s), styles["section"]))
        elif is_role_header(s):
            flush()
            pending_kind = None
            story.append(Paragraph(inline_markup(s), styles["role"]))
        elif s.startswith("•"):
            flush()
            pending_kind = "bullet"
            pending_lines.append(s.lstrip("•").strip())
        else:
            # A "Label: value, value..." line (e.g. "Languages / Frameworks:
            # Java 17, Spring Boot...") is its own row in the source resume,
            # not a wrapped continuation of the previous line -- e.g.
            # TECHNICAL STACK lists one category per line. Flush before
            # starting it so categories don't run together into one
            # paragraph. A plain wrapped continuation (no leading label)
            # still accumulates into whatever's already pending.
            if CATEGORY_LABEL_RE.match(s) and pending_kind in ("body", "bullet"):
                flush()
                pending_kind = None
            pending_kind = pending_kind or "body"
            pending_lines.append(s)
    flush()
    return story


def render_generic(text: str):
    body_style = ParagraphStyle(
        "Body", fontName="LiberationSerif", fontSize=12,
        leading=12 * 1.4, spaceAfter=12, alignment=TA_LEFT,
    )
    h1_style = ParagraphStyle(
        "H1", fontName="LiberationSerif-Bold", fontSize=16,
        leading=16 * 1.3, spaceAfter=14, spaceBefore=6,
    )
    h2_style = ParagraphStyle(
        "H2", fontName="LiberationSerif-Bold", fontSize=14,
        leading=14 * 1.3, spaceAfter=10, spaceBefore=10,
    )

    story = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if block.startswith("# "):
            story.append(Paragraph(inline_markup(block[2:].strip()), h1_style))
        elif block.startswith("## "):
            story.append(Paragraph(inline_markup(block[3:].strip()), h2_style))
        else:
            para = " ".join(line.strip() for line in block.split("\n"))
            story.append(Paragraph(inline_markup(para), body_style))
    return story


def md_to_pdf(input_path, output_path):
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    resume_mode = is_resume_format(text)
    margin = 0.5 * inch if resume_mode else 1 * inch
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        topMargin=margin, bottomMargin=margin,
        leftMargin=margin, rightMargin=margin,
    )

    story = render_resume(text) if resume_mode else render_generic(text)
    doc.build(story)

    try:
        from pypdf import PdfReader
        page_count = len(PdfReader(output_path).pages)
        print(f"  Mode: {'resume' if resume_mode else 'generic'} | Pages: {page_count}"
              + ("  ⚠️  over the usual 2-page resume target" if resume_mode and page_count > 2 else ""))
    except ImportError:
        print("  (install pypdf to get an exact page count printed here)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: md_to_pdf.py <input.md> <output.pdf>", file=sys.stderr)
        sys.exit(1)
    md_to_pdf(sys.argv[1], sys.argv[2])
