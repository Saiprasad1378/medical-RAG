"""
rag_pipeline.py — Retrieval + rerank + guardrailed LLM generation.
Hybrid: dense (bge-small) + BM25 -> RRF -> CrossEncoder
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import chromadb
from langchain_core.documents import Document
from sentence_transformers import CrossEncoder, SentenceTransformer

from prompts import SYSTEM_PROMPT, format_chat_history, format_context
from safety import EMERGENCY_RESPONSE, check_emergency, is_valid_query, sanitize_input

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CHROMA_DIR = Path("chroma_db")
COLLECTION_NAME = "medical_kb"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-TinyBERT-L-2-v2")
SIM_THRESHOLD = 0.35
NO_INFO_ANSWER = "I don'\''t have enough reliable information on this. Please consult a qualified doctor."
RRF_K = 60

class MedicalRAG:
    def __init__(self) -> None:
        self.embedder = SentenceTransformer(EMBED_MODEL)
        self.reranker = CrossEncoder(RERANK_MODEL)
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.collection = client.get_or_create_collection(COLLECTION_NAME)
        self.groq_client = None
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        if self.api_key:
            try:
                import groq
                self.groq_client = groq.Groq(api_key=self.api_key)
                logger.info("Groq client loaded.")
            except Exception as exc:
                logger.warning("Groq init failed: %s", exc)
        else:
            logger.warning("GROQ_API_KEY not found.")
        self._bm25 = None
        self._bm25_docs: List[Document] = []
        self._bm25_ids: List[str] = []

    @property
    def kb_count(self) -> int:
        return self.collection.count()

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _build_bm25(self, docs: List[Document]):
        try:
            from rank_bm25 import BM25Okapi
            corpus = [self._tokenize(d.page_content) for d in docs]
            self._bm25 = BM25Okapi(corpus) if corpus else None
            self._bm25_docs = docs
        except Exception as e:
            logger.warning("BM25 build failed: %s", e)
            self._bm25 = None

    def retrieve(self, query: str, k: int = 10) -> List[Document]:
        if self.kb_count == 0:
            return []
        # dense
        emb = self.embedder.encode([query], normalize_embeddings=True)[0].tolist()
        result = self.collection.query(query_embeddings=[emb], n_results=min(k, self.kb_count), include=["documents","metadatas","distances"])
        dense_docs: List[Document] = []
        dense_ids: List[str] = result.get("ids", [[]])[0] if "ids" in result else []
        # fallback if ids not returned
        for idx, (doc, meta, dist) in enumerate(zip(result["documents"][0], result["metadatas"][0], result["distances"][0])):
            sim = 1.0 - dist
            d = Document(page_content=doc, metadata={**meta, "score": sim, "_id": dense_ids[idx] if idx < len(dense_ids) else str(idx)})
            dense_docs.append(d)

        # build full corpus for BM25 (lazy, rebuild when count changes)
        try:
            all_data = self.collection.get(include=["documents","metadatas"])
            all_docs = [Document(page_content=d, metadata=m) for d,m in zip(all_data["documents"], all_data["metadatas"])]
            # map chroma ids
            all_ids = all_data.get("ids", [])
            for d,i in zip(all_docs, all_ids):
                d.metadata["_id"] = i
            if len(all_docs) != len(self._bm25_docs):
                self._build_bm25(all_docs)
            if self._bm25 is not None:
                q_tokens = self._tokenize(query)
                scores = self._bm25.get_scores(q_tokens)
                # top k by bm25
                ranked = sorted(zip(all_docs, scores), key=lambda x: x[1], reverse=True)[:k]
                bm25_docs = [d for d,_ in ranked]
                # RRF fusion
                rrf: Dict[str, float] = {}
                doc_map: Dict[str, Document] = {}
                for rank, d in enumerate(dense_docs):
                    key = d.metadata.get("_id", d.page_content[:50])
                    rrf[key] = rrf.get(key, 0) + 1/(RRF_K + rank + 1)
                    doc_map[key] = d
                for rank, d in enumerate(bm25_docs):
                    key = d.metadata.get("_id", d.page_content[:50])
                    # carry dense score if exists, else use bm25 doc
                    if key not in doc_map:
                        # need distance score — approximate as 0 for bm25-only docs, will be reranked anyway
                        d.metadata["score"] = 0.2
                        doc_map[key] = d
                    rrf[key] = rrf.get(key, 0) + 1/(RRF_K + rank + 1)
                # sort by rrf
                fused = sorted(rrf.items(), key=lambda x: x[1], reverse=True)[:k]
                fused_docs = []
                for key, rrf_score in fused:
                    d = doc_map[key]
                    d.metadata["rrf_score"] = float(rrf_score)
                    fused_docs.append(d)
                logger.info("hybrid retrieve: dense %d + bm25 %d -> fused %d (rrf)", len(dense_docs), len(bm25_docs), len(fused_docs))
                return fused_docs
        except Exception as e:
            logger.warning("hybrid fallback to dense: %s", e)
        return dense_docs

    def rerank(self, query: str, docs: List[Document], top_n: int = 4) -> List[Document]:
        if not docs:
            return []
        try:
            pairs = [[query, d.page_content] for d in docs]
            scores = self.reranker.predict(pairs, show_progress_bar=False)
            for doc, score in zip(docs, scores):
                doc.metadata["rerank_score"] = float(score)
            docs.sort(key=lambda d: d.metadata["rerank_score"], reverse=True)
        except Exception as exc:
            logger.warning("Reranker failed (%s) — using embedding scores.", exc)
            docs.sort(key=lambda d: d.metadata.get("score", 0.0), reverse=True)
        return docs[:top_n]

    def _stub_answer(self, docs: List[Document]) -> str:
        if not docs:
            return NO_INFO_ANSWER
        src = docs[0].metadata
        return "[OFFLINE MODE - no GROQ_API_KEY] " + str(src.get("source")) + " p." + str(src.get("page")) + ": " + docs[0].page_content[:500] + "..."

    def _call_llm(self, prompt: str, query: str) -> str:
        response = self.groq_client.chat.completions.create(
            model="allam-2-7b", temperature=0.0,
            messages=[{"role":"system","content":prompt},{"role":"user","content":f"User question: {query}\nAnswer:"}],
        )
        return response.choices[0].message.content

    def generate(self, query: str, chat_history: Optional[List[dict]] = None, offline: bool = False) -> Dict[str, Any]:
        t0 = time.perf_counter()
        if not is_valid_query(query):
            return {"answer":"Please enter a valid medical question.","sources":[],"is_emergency":False,"latency":0.0}
        refusal = sanitize_input(query)
        if refusal:
            return {"answer":refusal,"sources":[],"is_emergency":False,"latency":round(time.perf_counter()-t0,3)}
        if check_emergency(query):
            return {"answer":EMERGENCY_RESPONSE,"sources":[],"is_emergency":True,"latency":round(time.perf_counter()-t0,3)}
        t1=time.perf_counter()
        docs=self.retrieve(query,k=10)
        logger.info("retrieve: %.3fs (%d docs)", time.perf_counter()-t1, len(docs))
        if not docs or max(d.metadata.get("score",0) for d in docs) < SIM_THRESHOLD:
            # also allow rrf high even if dense low — if top rrf docs exist, don'\''t refuse prematurely
            # but keep gate: need at least one dense > threshold OR rrf > 0.015
            top_rrf = max((d.metadata.get("rrf_score",0) for d in docs), default=0)
            if top_rrf < 0.015:
                return {"answer":NO_INFO_ANSWER,"sources":[],"is_emergency":False,"latency":round(time.perf_counter()-t0,3)}
        t2=time.perf_counter()
        top_docs=self.rerank(query, docs, top_n=4)
        logger.info("rerank: %.3fs", time.perf_counter()-t2)
        t3=time.perf_counter()
        if offline or self.groq_client is None:
            answer=self._stub_answer(top_docs)
        else:
            prompt=SYSTEM_PROMPT.format(context=format_context(top_docs), chat_history=format_chat_history(chat_history or []))
            try: answer=self._call_llm(prompt, query)
            except Exception as exc:
                logger.error("LLM failed: %s", exc)
                answer="Sorry, I'\''m having trouble generating an answer right now. Please try again."
        logger.info("generate: %.3fs", time.perf_counter()-t3)
        sources=[{"source":d.metadata.get("source","unknown"),"page":d.metadata.get("page","?"),"section":d.metadata.get("section","")} for d in top_docs]
        return {"answer":answer,"sources":sources,"is_emergency":False,"latency":round(time.perf_counter()-t0,3)}
