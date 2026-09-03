"""api.py — FastAPI wrapper. Run: uvicorn api:app --reload"""
import logging
import re
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz
import chromadb
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_pipeline import MedicalRAG, CHROMA_DIR, COLLECTION_NAME, EMBED_MODEL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# simple in-memory rate limit (10 req/min per IP would be ideal, this is global demo)
_last_req = 0
MIN_INTERVAL = 0.0  # set 0 for dev; raise to 0.5 on prod if needed

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, separators=["\n\n","\n",". "," ",""])
HEADING_RE = re.compile(r"^(?:[A-Z][A-Z\s/&\-\'']{5,80}|(?:\d+\.)*\d+\s+[A-Z][\w\s,\-]{3,80})\s*$")

def _clean_text(t:str)->str:
    t=re.sub(r"^\s*\d+\s*$"," ",t,flags=re.MULTILINE)
    t=re.sub(r"^\s*Page\s+\d+(\s+of\s+\d+)?\s*$"," ",t,flags=re.IGNORECASE|re.MULTILINE)
    t=re.sub(r"(\w)-\s*\n\s*(\w)",r"\1\2",t)
    t=re.sub(r"[ \t]+"," ",t)
    t=re.sub(r"\n{3,}","\n\n",t)
    return t.strip()

def _find_section(text:str, default:str)->str:
    sec=default
    for line in text.split("\n"):
        if HEADING_RE.match(line.strip()):
            sec=line.strip()
    return sec

class ChatTurn(BaseModel):
    role: str = Field(..., description="'\''user'\'' or '\''assistant'\''")
    content: str
class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    chat_history: Optional[List[ChatTurn]] = None
    offline: bool = Field(default=False, description="true = raw passages only, no Groq call")
class SourceRef(BaseModel):
    source: str
    page: object
    section: str = ""
class AskResponse(BaseModel):
    answer: str
    sources: List[SourceRef]
    is_emergency: bool
    latency: float

rag: Optional[MedicalRAG] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag
    rag = MedicalRAG()
    if rag.kb_count == 0:
        logger.warning("KB empty — POST /upload to add PDFs or run ingest.py")
    else:
        logger.info(f"KB loaded: {rag.kb_count} chunks")
    yield

app = FastAPI(title="MediAssist API", version="1.1.0", description="Safety-gated RAG with hybrid retrieval — grounded, cited, not medical advice.", lifespan=lifespan)

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status":"ok","kb_chunks": rag.kb_count if rag else 0}

@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    global _last_req
    now=time.time()
    if now - _last_req < MIN_INTERVAL:
        raise HTTPException(status_code=429, detail="Too many requests — slow down.")
    _last_req=now
    if rag is None:
        raise HTTPException(status_code=503, detail="Pipeline not loaded yet.")
    try:
        result = rag.generate(req.query, [t.model_dump() for t in (req.chat_history or [])], offline=req.offline)
        return AskResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Query failed")
        raise HTTPException(status_code=500, detail="Internal error.") from exc

@app.post("/upload")
async def upload(files: List[UploadFile] = File(...)):
    """New upload UI — replaces Streamlit drag&drop. Accepts multiple PDFs."""
    if rag is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    # lazy embedder for ingest
    embedder = SentenceTransformer(EMBED_MODEL)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    col = client.get_or_create_collection(COLLECTION_NAME)
    pdf_dir = Path("data/pdfs")
    pdf_dir.mkdir(parents=True, exist_ok=True)
    added = 0
    saved_names=[]
    for up in files:
        if not up.filename.lower().endswith(".pdf"):
            continue
        # save to data/pdfs
        dest = pdf_dir / Path(up.filename).name
        content = await up.read()
        dest.write_bytes(content)
        saved_names.append(dest.name)
        # parse
        pages=[]
        try:
            with fitz.open(dest) as doc:
                for i, page in enumerate(doc):
                    try: text=page.get_text("text")
                    except: continue
                    pages.append({"text":text,"page":i+1})
        except Exception as e:
            logger.warning(f"failed to open {dest}: {e}")
            continue
        chunks=[]
        for pg in pages:
            cleaned=_clean_text(pg["text"])
            if not cleaned: continue
            cur=_find_section(cleaned, dest.stem)
            for piece in splitter.split_text(cleaned):
                sec=_find_section(piece, cur)
                chunks.append({"text":f"[{sec}]\n{piece}","metadata":{"source":dest.name,"page":pg["page"],"section":sec}})
        if not chunks: continue
        # embed & add
        ids=[f"{dest.stem}_{int(time.time()*1000)}_{i}" for i in range(len(chunks))]
        embs = embedder.encode([c["text"] for c in chunks], normalize_embeddings=True).tolist()
        # dedupe by id if exists
        col.add(ids=ids, documents=[c["text"] for c in chunks], metadatas=[c["metadata"] for c in chunks], embeddings=embs)
        added += len(chunks)
    # rebuild bm25 on next query (lazy)
    if hasattr(rag, "_bm25"):
        rag._bm25 = None
        rag._bm25_docs=[]
    return {"added_chunks": added, "kb_chunks": col.count(), "files": saved_names}

@app.get("/")
def serve_frontend():
    p = Path("web/out/index.html")
    if p.exists():
        return FileResponse(str(p))
    # fallback: try static legacy
    p2 = Path("static/index.html")
    if p2.exists():
        return FileResponse(str(p2))
    return {"message":"MediAssist API — see /docs. Frontend at / (web/out) after next build."}

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
