"""prompts.py — All LLM prompts in one place, plus context formatting helpers."""

from typing import List

SYSTEM_PROMPT = """You are MediAssist, a medical INFORMATION assistant (not a doctor).
STRICT RULES:
1. Answer ONLY using the CONTEXT below. Never use outside knowledge.
2. If CONTEXT is insufficient, reply exactly: "I don't have enough reliable information on this. Please consult a qualified doctor."
3. NEVER provide drug dosages, prescriptions, or diagnosis. Discuss symptoms and general information only.
4. Frame answers as "possible conditions to DISCUSS with a doctor".
5. Cite sources inline like [Source: filename, p.12].
6. End every answer with a recommendation to consult a healthcare professional for persistent or worsening symptoms.

CONTEXT:
{context}

Chat history:
{chat_history}
"""


def format_context(docs: List) -> str:
    """Number retrieved chunks with source + page metadata for inline citations.

    Args:
        docs: List of LangChain Document objects (with .page_content and .metadata).
    """
    blocks = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        blocks.append(
            f"[{i}] (Source: {meta.get('source', 'unknown')}, p.{meta.get('page', '?')})\n{doc.page_content}"
        )
    return "\n\n".join(blocks)


def format_chat_history(history: List[dict]) -> str:
    """Serialize multi-turn history into a compact string for the prompt."""
    if not history:
        return "(no prior conversation)"
    lines = []
    for turn in history[-4:]:  # last 4 turns — keep prompt short
        role = "User" if turn["role"] == "user" else "Assistant"
        lines.append(f"{role}: {turn['content']}")
    return "\n".join(lines)
