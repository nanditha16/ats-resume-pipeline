import pdfplumber
from pathlib import Path

input_dir = Path("input/pdf")
output_dir = Path("output")
resume_dir = output_dir / "resume"
output_dir.mkdir(exist_ok=True)
resume_dir.mkdir(exist_ok=True)

for pdf_file in input_dir.glob("*.pdf"):
    out_file = output_dir / f"{pdf_file.stem}.md"
    resume_file = resume_dir / f"{pdf_file.stem}.md"

    if resume_file.exists():
        print(f"Skipping {pdf_file.name} → already converted at {resume_file}")
        continue

    print(f"Processing {pdf_file.name}...")

    with pdfplumber.open(pdf_file) as pdf:
        pages = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)

    raw = "\n\n".join(pages)

    # ✅ fallback check
    if len(raw.strip()) < 100:
        print("⚠️ Weak extraction → OCR fallback recommended")
    
    with open(out_file, "w") as f:
        f.write(raw)

    print(f"Saved: {out_file}")
