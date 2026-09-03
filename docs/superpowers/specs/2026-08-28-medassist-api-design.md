# MediAssist — Headless RAG API (Option 3 Free-tier) — Design Spec

**Date:** 2026-08-28
**Status:** Approved §§1-2, §3 Frontend DEFERRED
**Goal:** Resume-grade separation of concerns — FastAPI headless RAG service + custom frontend, deployable on Render free tier. No Streamlit in prod. Frontend deferred per owner request.

---

## 1. Context & Scope

**Existing repo:** `C:\coding\medical RAG`
- `app.py` (Streamlit), `rag_pipeline.py` (MedicalRAG: BAAI/bge-small-en-v1.5 embed + BAAI/bge-reranker-base CrossEncoder → top 10→4, ChromaDB `chroma_db/medical_kb`, Groq `allam-2-7b` via `groq` SDK direct), `ingest.py`, `safety.py` (L0 emergency/injection/validity), `prompts.py`
- `data/pdfs/medical_handbook.pdf` (demo, 9 chunks)
- No API, no Docker, no `/health`, no custom frontend

**Out of scope (explicit):** Custom `static/index.html` UI — stubbed only, full design deferred. Auth/multi-user, streaming, Postgres history, Vercel split — deferred.

**Success criteria:** `POST /ask` returns same guarded answer as Streamlit uses now; `GET /health` returns KB count; Docker image runs on 512MB-1GB free tier; one env var controls reranker swap; `curl` and browser both work.

**Alternatives considered:**
- A: Single FastAPI serving static (chosen) — one image, one URL, fits free RAM
- B: Hybrid Streamlit + API in prod — rejected (2 ports, RAM, blurs story)
- C: Split API (Render) + frontend (Vercel) — rejected (2 deploys, CORS overhead, no resume gain)

---

## 2. Architecture

```
[Browser / curl / any client]
        |  POST /ask {query, chat_history}
        v
[FastAPI api.py] — lifespan startup loads MedicalRAG once
        |  rag.generate() → safety L0 → retrieve → similarity gate 0.35 → rerank → Groq/groq or stub
        v
[ChromaDB chroma_db/] + sentence-transformers + groq API
        |
        v
      {answer, sources, is_emergency, latency}

GET /health → {status, kb_chunks}
GET /       → FileResponse("static/index.html") // stub deferred
```

Isolation: `api.py` is HTTP boundary only. `rag_pipeline.py` remains pure domain logic. Components communicate via typed `AskRequest/AskResponse`. Changing reranker or prompt never touches `api.py`.

---

## 3. Components

### 3.1 api.py (new)
- **Framework:** FastAPI 0.111.0, Uvicorn standard
- **Startup:** `@app.on_event("startup")` or lifespan → `global rag = MedicalRAG()` once. Warn if `kb_count==0`.
- **Schemas (Pydantic v2):**
  - `ChatTurn{role: 'user'|'assistant', content: str}`
  - `AskRequest{query: str 1..2000, chat_history?: ChatTurn[]}`
  - `SourceRef{source: str, page: any, section: str}`
  - `AskResponse{answer: str, sources: SourceRef[], is_emergency: bool, latency: float}`
- **Routes:**
  - `GET /health` — no auth, for Docker/Render probe
  - `POST /ask` — validates, calls `rag.generate()`, `try/except → 500`
  - `GET /` — serves `static/index.html` (deferred stub)
- **CORS:** `CORSMiddleware allow_origins=["*"]` (lock to domain before prod)
- **Logging:** `logging.INFO` per request

### 3.2 rag_pipeline.py (edit)
- One line: `RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")`
- Init: `self.reranker = CrossEncoder(RERANK_MODEL)` — no other change
- Enables free-tier swap via `ENV RERANK_MODEL=cross-encoder/ms-marco-TinyBERT-L-2-v2` in Docker, local dev keeps 1.1GB model if unset

### 3.3 Docker & Deploy

**Dockerfile:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV RERANK_MODEL=cross-encoder/ms-marco-TinyBERT-L-2-v2
ENV PYTHONUNBUFFERED=1
RUN python ingest.py || echo "no PDFs at build - ingest at runtime"
EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

**.dockerignore:**
```
venv/
__pycache__/
.git/
.env
chroma_db/
```

**Keep** `data/pdfs/medical_handbook.pdf` — pre-baked KB at build, so container starts with 9 chunks, no volume needed.

**requirements.txt add:**
```
fastapi==0.111.0
uvicorn[standard]==0.30.5
pydantic==2.8.2
```

**render.yaml:**
```yaml
services:
  - type: web
    name: medassist-api
    env: docker
    dockerfilePath: ./Dockerfile
    healthCheckPath: /health
    envVars:
      - key: GROQ_API_KEY
        sync: false
      - key: RERANK_MODEL
        value: cross-encoder/ms-marco-TinyBERT-L-2-v2
```

Local: `docker build -t medassist && docker run -p 8000:8000 -e GROQ_API_KEY=$GROQ_API_KEY medassist` → `curl localhost:8000/health`

**Trade-off:** TinyBERT ~100MB vs 1.1GB → image ~1.2GB smaller, fits 512MB-1GB, cold start ~8s vs 30s, small rerank quality dip acceptable for demo.

### 3.4 Frontend (§3 DEFERRED)
- `static/index.html` — contains `<!-- TODO: custom chat UI — deferred per 2026-08-28 -->` only
- `GET /` serves it; no CSS/JS work until owner triggers

---

## 4. Data Flow & Error Handling

1. Client `POST /ask` → Pydantic validates 1..2000 chars → `rag.generate()`
2. `safety.py` gates: invalid → 200 with message, injection → refusal, emergency → `is_emergency:true`
3. `retrieve(k=10)` → similarity gate 0.35 → `rerank top4` → `groq.chat.completions.create(model="allam-2-7b")` or `_stub_answer()` if no key
4. Response always `AskResponse` shape; `is_emergency` never 4xx — client renders banner

Errors: validation `422`, pipeline not loaded `503`, Groq failure `500` with `logger.exception` but user-facing generic message.

---

## 5. Testing

- `tests/test_api.py` (new): `test_health`, `test_ask_dengue_mocked_rag`, `test_ask_emergency`, `test_ask_injection_blocked`, `test_empty_kb`
- `pytest` in CI and `docker build` smoke `curl /health`
- Existing `safety.py` edge cases remain in `pytest` via GitHub Action (if already present)

---

## 6. YAGNI & Isolation Notes

- No auth, no DB, no streaming — add only when requested
- Each unit testable independently: `api.py` via TestClient with mocked `MedicalRAG`, `rag_pipeline` unchanged
- Small files: `api.py` <150 lines, `Dockerfile` <15 lines

---

## 7. Open Questions (Resolved)

- Q1 Goal: B resume-grade — done
- Q2 Frontend: Option 3 single static served by FastAPI, no Streamlit in prod — approved, but implementation deferred
- Q3 Free-tier: A TinyBERT swap via env var — approved

---

## 8. Next Steps

1. User review of this spec
2. Invoke `writing-plans` skill to create implementation plan (per brainstorming terminal state)
3. Plan will cover §1 + §2 only; §3 frontend plan created later when owner triggers

