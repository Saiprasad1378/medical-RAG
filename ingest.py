"""
ingest.py — PDF → clean text → context-aware chunks → ChromaDB.

WHY chunk_size=800 / overlap=120?
  - Medical paragraphs average 60-100 words (~120-160 tokens). chunk_size=800
    keeps 2-4 paragraphs together, so a chunk usually contains a complete
    thought (symptoms + advice), not a mid-sentence fragment.
  - overlap=120 (~1.5 sentences) prevents clinical facts that straddle a
    chunk boundary from being lost in either chunk.

WHY prepend section headers?
  - WHO/CDC PDFs are structured by ALL-CAPS headings ("TREATMENT", "SYMPTOMS").
    Vector search on BGE embeddings matches *keywords*; prepending the section
    header gives every chunk a strong topical signal, dramatically improving
    recall for queries like "dengue treatment" vs "dengue symptoms".
"""

import logging
import re
from pathlib import Path
from typing import List

import chromadb
import fitz  # PyMuPDF
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PDF_DIR = Path("data/pdfs")
CHROMA_DIR = Path("chroma_db")
COLLECTION_NAME = "medical_kb"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

# Headings: ALL-CAPS lines, or "1. / 1.1 Title Case" numbered headings
HEADING_RE = re.compile(r"^(?:[A-Z][A-Z\s/&\-']{5,80}|(?:\d+\.)*\d+\s+[A-Z][\w\s,\-]{3,80})\s*$")

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],  # split on paragraph → sentence → word
)


def extract_pdf_text(pdf_path: Path) -> List[dict]:
    """Extract per-page text from a PDF, gracefully skipping corrupted pages.

    Returns:
        List of {"text": str, "page": int} dicts.
    """
    pages: List[dict] = []
    try:
        with fitz.open(pdf_path) as doc:
            for i, page in enumerate(doc):
                try:
                    text = page.get_text("text")
                except Exception as exc:  # corrupted single page
                    logger.warning("Page %d of %s failed: %s", i + 1, pdf_path.name, exc)
                    continue
                pages.append({"text": text, "page": i + 1})
    except Exception as exc:
        logger.error("Cannot open %s: %s — skipping file.", pdf_path.name, exc)
    return pages


def clean_text(text: str) -> str:
    """Strip page headers/footers, fix broken hyphens, normalize whitespace."""
    text = re.sub(r"^\s*\d+\s*$", " ", text, flags=re.MULTILINE)          # bare page numbers
    text = re.sub(r"^\s*Page\s+\d+(\s+of\s+\d+)?\s*$", " ", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)                    # broken hyphens: "treat-\nment"
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def find_current_section(text: str, default: str) -> str:
    """Return the last ALL-CAPS / numbered heading seen in the text, else default."""
    section = default
    for line in text.split("\n"):
        line = line.strip()
        if HEADING_RE.match(line):
            section = line
    return section


def build_chunks(pdf_path: Path) -> List[dict]:
    """Clean, split, and header-tag all pages of one PDF into chunk dicts."""
    chunks: List[dict] = []
    for page in extract_pdf_text(pdf_path):
        cleaned = clean_text(page["text"])
        if not cleaned:
            continue
        current_section = find_current_section(cleaned, pdf_path.stem)
        # Chunk per page so the "page" metadata stays truthful.
        for piece in splitter.split_text(cleaned):
            section = find_current_section(piece, current_section)
            chunks.append({
                "text": f"[{section}]\n{piece}",  # WHY: header prepended → topical signal
                "metadata": {"source": pdf_path.name, "page": page["page"], "section": section},
            })
    return chunks


def main() -> None:
    """CLI entrypoint: ingest all PDFs into the persistent Chroma collection."""
    if not PDF_DIR.exists() or not list(PDF_DIR.glob("*.pdf")):
        logger.error("No PDFs found in %s. Drop WHO/CDC/Merck PDFs there first.", PDF_DIR)
        return

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(COLLECTION_NAME)

    # Skip re-ingestion — embedding thousands of chunks is expensive and deterministic.
    if collection.count() > 0:
        logger.info("Collection '%s' already has %d chunks. Nothing to do.", COLLECTION_NAME, collection.count())
        return

    embedder = SentenceTransformer(EMBED_MODEL)
    all_chunks: List[dict] = []
    for pdf in sorted(PDF_DIR.glob("*.pdf")):
        logger.info("Processing %s ...", pdf.name)
        all_chunks.extend(build_chunks(pdf))

    if not all_chunks:
        logger.error("No text extracted from any PDF — are they scanned images without OCR?")
        return

    batch = 64  # WHY: avoid embedding all at once (RAM spike) and Chroma max-batch limits
    for i in tqdm(range(0, len(all_chunks), batch), desc="Embedding + upserting"):
        batch_chunks = all_chunks[i : i + batch]
        embeddings = embedder.encode(
            [c["text"] for c in batch_chunks], normalize_embeddings=True, show_progress_bar=False
        ).tolist()
        collection.add(
            ids=[f"chunk_{i + j}" for j in range(len(batch_chunks))],
            documents=[c["text"] for c in batch_chunks],
            metadatas=[c["metadata"] for c in batch_chunks],
            embeddings=embeddings,
        )

    logger.info("✅ Ingestion complete: %d chunks in '%s'.", collection.count(), COLLECTION_NAME)


if __name__ == "__main__":
    main()
