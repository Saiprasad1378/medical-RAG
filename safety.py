"""
safety.py — Layer-0 guardrails. Runs BEFORE retrieval/LLM so emergency and
injection queries never consume tokens or produce dangerous answers.
"""

import re
from typing import Optional

EMERGENCY_KEYWORDS = [
    "chest pain", "difficulty breathing", "stroke", "seizure", "unconscious",
    "severe bleeding", "suicidal", "suicide", "overdose", "poisoning",
    "anaphylaxis", "heart attack",
]

EMERGENCY_RESPONSE = (
    "⚠️ This may be a MEDICAL EMERGENCY. "
    "Call 108/112 (India) or your local emergency number IMMEDIATELY. "
    "Do not wait — every minute matters."
)

# Prompt-injection / out-of-scope patterns
INJECTION_PATTERNS = [
    r"ignore (all\s+)?previous instructions",
    r"you are now",
    r"developer mode",
    r"\bprescri\w*",           # prescribe / prescription
    r"\d+\s*(mg|mcg|ml|iu|g)\s+of\b",  # exact dosage asks: "500 mg of paracetamol"
]

REFUSAL = (
    "I'm sorry — I can't help with that. I'm an information assistant only. "
    "For prescriptions, dosages, or diagnoses, please consult a qualified doctor."
)


def check_emergency(query: str) -> bool:
    """Return True if the query matches any emergency keyword (case-insensitive)."""
    q = query.lower()
    return any(kw in q for kw in EMERGENCY_KEYWORDS)


def sanitize_input(query: str) -> Optional[str]:
    """Detect prompt-injection or out-of-scope requests.

    Returns:
        REFUSAL string if unsafe, else None (safe to proceed).
    """
    q = query.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, q):
            return REFUSAL
    return None


def is_valid_query(query: str) -> bool:
    """Reject empty, oversized, or non-medical gibberish queries."""
    if not query or not query.strip():
        return False
    if len(query) > 2000:
        return False
    stripped = re.sub(r"[^a-z\s]", "", query.lower())
    # gibberish: too few alphabetic characters relative to length, or no vowels
    if len(stripped) < 3 or not re.search(r"[aeiou]", stripped):
        return False
    return True
