"""app.py — Streamlit UI for MediAssist."""

import json
import shutil
import tempfile
from pathlib import Path

import chromadb
import fitz  # PyMuPDF
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
import streamlit as st

from rag_pipeline import MedicalRAG, CHROMA_DIR, COLLECTION_NAME, EMBED_MODEL

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

st.set_page_config(page_title="MediAssist", page_icon="🏥", layout="wide")

PDF_DIR = Path("data/pdfs")
PDF_DIR.mkdir(parents=True, exist_ok=True)

HEADING_RE = __import__("re").compile(
    r"^(?:[A-Z][A-Z\s/&\-']{5,80}|(?:\d+\.)*\d+\s+[A-Z][\w\s,\-]{3,80})\s*$"
)

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def _clean_text(text: str) -> str:
    import re
    text = re.sub(r"^\s*\d+\s*$", " ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*Page\s+\d+(\s+of\s+\d+)?\s*$", " ", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _find_section(text: str, default: str) -> str:
    section = default
    for line in text.split("\n"):
        line = line.strip()
        if HEADING_RE.match(line):
            section = line
    return section


def ingest_uploaded_pdf(pdf_path: Path) -> int:
    """Ingest a single PDF into ChromaDB. Returns number of chunks added."""
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(COLLECTION_NAME)
    embedder = SentenceTransformer(EMBED_MODEL)

    pages = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            try:
                text = page.get_text("text")
            except Exception:
                continue
            pages.append({"text": text, "page": i + 1})

    chunks = []
    for page in pages:
        cleaned = _clean_text(page["text"])
        if not cleaned:
            continue
        current_section = _find_section(cleaned, pdf_path.stem)
        for piece in splitter.split_text(cleaned):
            section = _find_section(piece, current_section)
            chunks.append({
                "text": f"[{section}]\n{piece}",
                "metadata": {"source": pdf_path.name, "page": page["page"], "section": section},
            })

    if not chunks:
        return 0

    batch = 64
    for i in range(0, len(chunks), batch):
        batch_chunks = chunks[i:i + batch]
        embeddings = embedder.encode(
            [c["text"] for c in batch_chunks], normalize_embeddings=True, show_progress_bar=False
        ).tolist()
        collection.add(
            ids=[f"{pdf_path.stem}_{i + j}" for j in range(len(batch_chunks))],
            documents=[c["text"] for c in batch_chunks],
            metadatas=[c["metadata"] for c in batch_chunks],
            embeddings=embeddings,
        )
    return len(chunks)


# ---------- Session state ----------
if "chat" not in st.session_state:
    st.session_state.chat = []  # [{"role": "user"|"assistant", "content": str, "sources": [...]}, ...]
if "online_mode" not in st.session_state:
    st.session_state.online_mode = True  # default: online (use LLM)

# ---------- Cached pipeline ----------
@st.cache_resource(show_spinner="Loading models...")
def get_rag() -> MedicalRAG:
    return MedicalRAG()

rag = get_rag()

# ---------- Sidebar ----------
with st.sidebar:
    st.header("ℹ️ About")
    st.warning("⚠️ This is NOT medical advice. Always consult a doctor.")
    st.caption(
        "MediAssist answers ONLY from your uploaded PDFs. "
        "It never diagnoses, prescribes, or gives dosages."
    )
    st.metric("KB chunks", rag.kb_count)

    # ---------- Online Mode Toggle ----------
    st.divider()
    online = st.toggle(
        "🌐 Online Mode (Groq LLM)",
        value=st.session_state.online_mode,
        help="ON = AI-generated answers via Groq API. OFF = show raw retrieved passages only.",
    )
    st.session_state.online_mode = online
    if online:
        if rag.groq_client is not None:
            st.success("✅ Groq LLM connected")
        else:
            st.warning("⚠️ No GROQ_API_KEY found — will show raw passages")
    else:
        st.info("📖 Offline mode — showing retrieved passages only")

    st.divider()

    sources = set()
    try:
        result = rag.collection.get(limit=1000, include=["metadatas"])
        sources = {m["source"] for m in (result["metadatas"] or [])}
    except Exception:
        pass
    st.write("**Sources loaded:**")
    for s in sorted(sources):
        st.caption(f"📄 {s}")

    st.divider()

    # ---------- Upload PDFs ----------
    st.header("📤 Upload PDFs")
    uploaded_files = st.file_uploader(
        "Drop medical PDFs (WHO, CDC, textbooks, etc.)",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_uploader",
    )

    if uploaded_files:
        if st.button("📥 Ingest Uploaded PDFs", use_container_width=True):
            progress = st.progress(0, text="Saving files...")
            total = len(uploaded_files)
            total_chunks = 0
            for idx, f in enumerate(uploaded_files):
                save_path = PDF_DIR / f.name
                save_path.write_bytes(f.read())
                progress.progress((idx) / total, text=f"Ingesting {f.name}...")
                n = ingest_uploaded_pdf(save_path)
                total_chunks += n
                progress.progress((idx + 1) / total, text=f"✅ {f.name} → {n} chunks")
            st.success(f"Done! Ingested {total} PDFs → {total_chunks} chunks.")
            st.rerun()

    st.divider()

    # ---------- Controls ----------
    if st.button("🔄 Rebuild Index", use_container_width=True):
        shutil.rmtree("chroma_db", ignore_errors=True)
        st.rerun()

    with st.expander("⚙️ Advanced"):
        st.caption("Re-ingest all PDFs from `data/pdfs/` folder")
        if st.button("Re-ingest All PDFs", use_container_width=True):
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from ingest import main as ingest_main
            shutil.rmtree("chroma_db", ignore_errors=True)
            ingest_main()
            st.rerun()

# ---------- Header ----------
st.title("🏥 MediAssist — Medical Information Assistant")
st.error("⚠️ This is NOT medical advice. Always consult a doctor.", icon="🚨")

# ---------- Render chat history ----------
for msg in st.session_state.chat:
    with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "🩺"):
        if msg.get("is_emergency"):
            st.error(msg["content"], icon="🚨")
        else:
            st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander("📚 Sources"):
                for s in msg["sources"]:
                    st.caption(f"📄 **{s['source']}** — p.{s['page']} · {s['section']}")
        if msg.get("latency") is not None:
            st.caption(f"⏱ {msg['latency']}s")

# ---------- Input ----------
if query := st.chat_input("Ask a health question (e.g., 'What are symptoms of dengue?')"):
    st.session_state.chat.append({"role": "user", "content": query})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(query)

    with st.chat_message("assistant", avatar="🩺"):
        with st.spinner("Searching trusted sources..."):
            if st.session_state.online_mode:
                result = rag.generate(query, st.session_state.chat[:-1])
            else:
                # Offline mode: retrieve only, no LLM
                import time
                from prompts import format_context
                t0 = time.perf_counter()
                docs = rag.retrieve(query, k=4)
                sources = [
                    {"source": d.metadata.get("source", "unknown"), "page": d.metadata.get("page", "?"),
                     "section": d.metadata.get("section", "")}
                    for d in docs
                ]
                if docs:
                    answer = f"**📖 Retrieved passages (Offline Mode):**\n\n{format_context(docs)}"
                else:
                    answer = "No relevant passages found. Upload PDFs or switch to Online Mode."
                result = {"answer": answer, "sources": sources, "is_emergency": False,
                          "latency": round(time.perf_counter() - t0, 3)}

        if result["is_emergency"]:
            st.error(result["answer"], icon="🚨")
        else:
            st.markdown(result["answer"])
            if result["sources"]:
                with st.expander("📚 Sources"):
                    for s in result["sources"]:
                        st.caption(f"📄 **{s['source']}** — p.{s['page']} · {s['section']}")
            st.caption(f"⏱ {result['latency']}s")

    st.session_state.chat.append({"role": "assistant", "content": result["answer"],
                                  "sources": result["sources"], "is_emergency": result["is_emergency"],
                                  "latency": result["latency"]})
