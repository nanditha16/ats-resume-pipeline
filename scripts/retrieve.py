#!/usr/bin/env python3
"""
Two-stage semantic retrieval: chunk-level → paragraph-level reranking.
Produces prompts/semantic_prompt.txt with the most relevant resume sections for a query/JD.
"""
from sentence_transformers import SentenceTransformer
from pathlib import Path
import numpy as np
import sys
import os

query = sys.argv[1] if len(sys.argv) > 1 else None

if not query:
    print("❌ Provide a query")
    exit(1)

print("🧠 Semantic retrieval (hierarchical)...")

model = SentenceTransformer("all-MiniLM-L6-v2")

chunks_dir = Path("chunks")

documents = []
files = []

# ✅ Load chunk-level documents
for f in chunks_dir.glob("*.md"):
    text = f.read_text(encoding="utf-8").strip()
    if text:
        documents.append(text)
        files.append(f)

if not documents:
    print("❌ No chunks found")
    exit(1)

# ✅ Stage 1 — Chunk-level retrieval
doc_embeddings = model.encode(documents, convert_to_numpy=True, normalize_embeddings=True)
query_embedding = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]

chunk_scores = np.dot(doc_embeddings, query_embedding)

# ✅ Pick top chunks (coarse)
top_chunk_indices = np.argsort(chunk_scores)[-4:][::-1]

# ✅ Stage 2 — Fine-grained reranking within chunks
selected_sections = []

for idx in top_chunk_indices:
    chunk_text = documents[idx]

    # ✅ split into paragraphs / lines
    parts = []
    for block in chunk_text.split("\n"):
        line = block.strip()
        if len(line) > 30:  # filter noise
            parts.append(line)

    if not parts:
        continue

    # ✅ encode parts
    part_embeddings = model.encode(parts, convert_to_numpy=True, normalize_embeddings=True)

    # ✅ score parts
    part_scores = np.dot(part_embeddings, query_embedding)

    # ✅ select top lines from this chunk
    top_part_indices = np.argsort(part_scores)[-5:][::-1]

    for i in top_part_indices:
        selected_sections.append((parts[i], part_scores[i]))

# ✅ sort across all selected parts
selected_sections = sorted(selected_sections, key=lambda x: x[1], reverse=True)

# ✅ limit output
final_sections = selected_sections[:12]

# ✅ build output
os.makedirs("prompts", exist_ok=True)
output_file = "prompts/semantic_prompt.txt"

with open(output_file, "w", encoding="utf-8") as out:
    out.write("You are analyzing relevant document sections.\n")
    out.write("Answer using ONLY the context below.\n\n")
    out.write("QUESTION:\n")
    out.write(query + "\n\n")
    out.write("RELEVANT CONTEXT:\n\n")

    for text, score in final_sections:
        out.write(f"- {text}\n")

print(f"✅ Saved to {output_file}")
