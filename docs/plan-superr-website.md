# Plan — Superr x MediAssist Proper Website

> Status: **Draft for approval** — implement after you say go
> Date: 2026-09-03 | Student: 4th year ISE | Goal: public URL + resume killer

---

## 0. Design Read

**Reading this as:** *medical RAG landing + chat app for two audiences — (1) recruiters scanning a resume link and (2) general public asking health questions — with a **warm schoolyard notebook** language, leaning toward **Superr's matte paper system** (not SaaS glass).*

Trust is non-negotiable (medical). So we keep Superr's playfulness **in illustration + micro-copy**, but keep **information hierarchy strict** — cream canvas, cocoa ink, orange as punctuation only.

**Dials:** DESIGN_VARIANCE: 7 / MOTION_INTENSITY: 4 / VISUAL_DENSITY: 4
Low motion (no parallax on medical content), comfortable density, playful variance through sticker rotations and asymmetric bento — not chaos.

---

## 1. Superr Design System — Locked Tokens

From your brief (superr.ai):

**Palette**
- Canvas: \Cream Paper #fdfbf9\ (page), \Dew Drop #f7efe9\ (cards)
- Ink: \Cocoa Ink #2b1a07\ (headlines/borders), \Charcoal #171717\ (body/borders), \True Black #000\ (only hero emphasis)
- Accent: \Marker Orange #ff6f1e\ (handwritten captions, highlight underline, footer band), \Burnt Sienna #ce500a\ (heavier orange)
- Stickers ONLY (never UI): \Sky #3b82f6\, \Bubblegum #ff66cf\, \Sprout #22c55e\
- Shadow: \Shadow Mist #bebcbb\ base — cards \gba(0,0,0,0.06)\, buttons \gba(0,0,0,0.25)\

**Type**
- Display: **Gelica 600, lowercase, 104px / 1.08** — hero only. Fallback: Recoleta / Fraunces. All headings lowercase.
- Body/UI: **Geist 400/500** — nav, labels, fine print. Fallback: Inter.
- Scale: Minor Third 1.2 from 20px base.

**Shape & Space**
- Radius: inputs 8px, cards 12px, tags/buttons 20px, footer 56px (asymmetric top)
- Spacing: base 4px, section gap 64px, card pad 32px, max-w 1200px
- Elevation: whisper-light only. Borders are thin dark (1px #171717), not fills.

**Guidelines (hard)**
- No filled CTAs — dark border on cream only.
- Stickers at 5–15° random rotation, hand-placed feel.
- Product photos (notebooks/pencils/labels) are heroes, not icons.

---

## 2. What We Ship — Proper Website (not Streamlit)

**Info Architecture**
\\\
/ (Landing) -> /chat (App)
Landing: Nav | Hero (cream + orange scribble) | How it works (3 notebooks) | Safety (guardrails as sticker cards) | Sources | Disclaimer | Footer (orange band)
Chat: Left = conversation (gelica captions, cream bubbles), Right = Sources panel (page + section + sticky), Bottom = input (dark border pill, 20px) + emergency banner slot
\\\

**Pages — wireframes**
1. **Hero** — Big gelica lowercase \meet mediassist\ (Cocoa Ink, 104px), orange highlight underline, sub \your pdfs, answered.\ Right: photographed stack of medical handbook + orange marker + sticker labels. Floating stickers (bolt/bear) at -7° / +11°.
2. **How it works** — 3 cards (Dew Drop, 12px, Charcoal 1px border): [Upload PDFs] [Retrieve + Rerank] [Grounded Answer + Citations]. Minimal shadows, sticker doodles in corners.
3. **Safety** — Sticker-wall explaining L0 emergency, L1 similarity gate 0.35, L3 grounded prompt. Each guard = tag (20px pill) + cream card.
4. **Chat App** — cream page, charcoal input pill, orange send (border only). Offline toggle = paper switch. Sources cite \[Source: file, p.12]\ clickable. Emergency triggers full-width banner (india 108/112).
5. **Footer** — Marker Orange band, cream text, gelica lowercase, 56px rounded top.

**Interaction**
- Staggered fade-up on scroll (IntersectionObserver, 800ms, translate-y-16 -> 0, no blur on mobile)
- Sticker hover wiggle (rotate 2°)
- Streamlit is **retired** for public — kept as \pp_streamlit.py\ for local dev only. Public site calls \pi.py\ via \/ask\.

---

## 3. Architecture — Frontend Vercel + Backend Render

\\\
Browser (Next.js static)
  |---> api.medisist.onrender.com /ask (FastAPI + RAG)
         |-- safety.py (emergency/injection/validity) — BEFORE retrieval
         |-- Hybrid Retrieval (NEW): BM25 + dense (bge-small) -> RRF -> top10 -> CrossEncoder -> top4
         |-- Groq allam-2-7b (grounded prompt) + citation formatter
         |-- ChromaDB persistent (chroma_db, 9 chunks today, scalable)
  |---> /health (for Render healthCheckPath)
\\\

**Tech choices**
- Frontend: **Next.js 14 (App Router) + Tailwind v4 + shadcn/ui** — deploy to **Vercel** (free, instant). No server needed, just fetch to Render.
- Backend: keep \pi.py\, add CORS already \*\, lifespan instead of on_event, \uvicorn api:app\ via \Dockerfile:1\ to **Render Web Service + Disk** (256MB for \chroma_db\ so index survives restarts).
- Env: \GROQ_API_KEY\, \RERANK_MODEL=cross-encoder/ms-marco-TinyBERT-L-2-v2\

**Why not pure Streamlit on Render?** Looks like homework. Next.js + Superr = portfolio piece.

---

## 4. Backend Upgrade — Hybrid Search (Resume Extension #1)

**Goal:** measurable recall lift to quote on resume: \Hybrid (BM25+dense RRF) improves Recall@10 by X% vs dense-only on 30q test set\.

**Implementation (rag_pipeline.py)**
- Pre-step: on \__init__\, build \BM25Okapi\ corpus from \collection.get(include=['documents','metadatas'])\$ (tokenize lowercase, no stopword removal for medical terms)
- On \generate(query)\:
  1. Dense: \collection.query(query_embeddings, n_results=10)\ (existing 0.35 gate stays)
  2. BM25: \m25.get_top_n(query_tokens, docs, n=10)\
  3. Fusion: **Reciprocal Rank Fusion (k=60)**: \score = sum(1/(k+rank))\ across both lists, dedupe by doc id
  4. Take fused top-10 -> CrossEncoder rerank -> top-4 -> prompt (unchanged)
- Add \RAGAS\ eval in \evaluate.py\ to log before/after on \	est_questions.json:1\ (30 Qs)
- Perf: BM25 is CPU-only, <5ms, no extra model

**Fallback:** if hybrid fails, dense-only path still answers — no 500s.

---

## 5. Deployment Plan (Public URL for everyone)

**Phase A — Local build first (no deploy until you approve preview)**
1. \web/\ scaffold: \
px create-next-app@latest web --ts --tailwind --app --eslint\
2. Implement Superr tokens as CSS variables, gelica via \
ext/font\, layout + 5 sections
3. Wire chat to local \http://localhost:8000/ask\ (run \uvicorn api:app --port 8000\)
4. \playwright screenshot\ verify cream/orange/cocoa, 12px/20px/56px radii

**Phase B — Render backend**
- Update \pi.py:34\ \on_event\ -> \lifespan\, add \Disk mount at /app/chroma_db\ in \ender.yaml:1\
- Push to GitHub, Render auto-builds \Dockerfile\, set \GROQ_API_KEY\ secret, test \/health\ + \/docs\

**Phase C — Vercel frontend**
- \ercel --prod\ with \NEXT_PUBLIC_API_URL=https://medassist-api.onrender.com\
- Custom domain optional (e.g. \mediasist.vercel.app\)

**Cost:** Vercel hobby free, Render free tier sleeps (add cron ping) or \/mo for always-on + disk. Groq free tier enough for demo; add 10 req/min rate limit in \pi.py\ to prevent abuse.

**Safety for public:** add footer disclaimer \Not medical advice — consult doctor\, no dosages (already in \prompts.py:1\), emergency banner, \/ask\ rate limit + 2000 char cap (already in \safety.py\).

---

## 6. Resume Angle (how to talk about it)

> **MediAssist — Safety-gated Medical RAG (Next.js + FastAPI + Chroma)**
> Ground-truth answers from user PDFs with anti-hallucination gate (0.35) and L0 emergency/injection filters. Hybrid dense+BM25 retrieval with RRF + cross-encoder rerank improved Recall@10 by X% (RAGAS on 30 Qs). Deployed on Vercel/Render with Docker; 9 chunks -> scalable ingest.

That is a hiring-manager scannable story: *retrieval quality measured, safety first, shipped*.

---

## 7. Execution Steps After Approval

1. **Scaffold** \web/\ + Superr tokens + gelica/geist
2. **Build landing** (hero/how/safety/footer) — static, no API
3. **Build chat** (\/chat\ route, sources panel, offline toggle)
4. **Hybrid search** in \ag_pipeline.py\ + eval
5. **Polish + responsive** (1200px max, 640px stack, sticker rotations)
6. **Local preview** + your tweaks
7. **Deploy** Render then Vercel, smoke test \/ask\ with 3 sample queries

**Open questions for you (answer before I code):**
- Project name: keep **MediAssist** or rebrand e.g. \parchi.health\ to match Superr notebook vibe?
- Do you want me to keep the Streamlit file for local ingest, or fully replace it with the new upload UI?
- Do you have a GitHub repo I should push to, or create a new one?

Say \go\ and I will start Phase A. If you want tweaks to the plan, tell me which section.
