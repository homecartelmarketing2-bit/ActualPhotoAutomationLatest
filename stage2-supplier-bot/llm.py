"""
LM Studio helper — translate Tagalog/Filipino text to English.
Uses the same local endpoint as the existing zoho_bill_automation.
"""

import requests
import config
from config import log

SYSTEM_PROMPT = (
    "You are a translator. Translate the following Filipino/Tagalog message to English. "
    "Return ONLY the English translation, nothing else. "
    "If the message is already in English, return it unchanged."
)


def translate_to_english(text: str) -> str:
    """
    Translate text from Filipino/Tagalog to English via LM Studio.
    Falls back to the original text if LM Studio is unavailable.
    """
    try:
        resp = requests.post(
            f"{config.LM_STUDIO_URL}/v1/chat/completions",
            json={
                "model": config.LM_STUDIO_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": text},
                ],
                "max_tokens":  200,
                "temperature": 0.1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        translated = result["choices"][0]["message"]["content"].strip()
        log.info(f"LLM translation: '{text}' → '{translated}'")
        return translated
    except Exception as exc:
        log.warning(f"LM Studio unavailable, using original text: {exc}")
        return text


CLASSIFY_PROMPT = (
    "You are a classifier. Given a supplier's response about a product photo request, "
    "determine if the response means the actual photo is NOT AVAILABLE / doesn't exist / "
    "they don't have it / out of stock / discontinued / wala / hindi available.\n"
    "Reply with ONLY 'YES' if the photo is not available, or 'NO' if it is available or "
    "if the message is about something else (like providing info, asking questions, etc)."
)


def classify_unavailable(text: str) -> bool:
    """
    Use LLM to classify whether text means 'actual photo not available'.
    Falls back to keyword matching if LM Studio is down.
    """
    # Quick keyword check first (both English and Tagalog)
    lower = text.lower()
    unavailable_keywords = [
        "not available", "no actual photo", "no actual", "no photo",
        "don't have", "dont have", "doesn't have", "doesnt have",
        "out of stock", "discontinued", "unavailable", "not yet available",
        "wala", "walang actual", "walang photo", "hindi available",
        "wala po", "wala pong", "no stock", "not in stock",
    ]
    if any(kw in lower for kw in unavailable_keywords):
        log.info(f"Keyword match: text classified as UNAVAILABLE: '{text}'")
        return True

    # Use LLM for more nuanced detection
    try:
        resp = requests.post(
            f"{config.LM_STUDIO_URL}/v1/chat/completions",
            json={
                "model": config.LM_STUDIO_MODEL,
                "messages": [
                    {"role": "system", "content": CLASSIFY_PROMPT},
                    {"role": "user",   "content": text},
                ],
                "max_tokens":  10,
                "temperature": 0.0,
            },
            timeout=15,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip().upper()
        is_unavailable = answer.startswith("YES")
        log.info(f"LLM classify: '{text}' → {answer} (unavailable={is_unavailable})")
        return is_unavailable
    except Exception as exc:
        log.warning(f"LLM classify failed, defaulting to available: {exc}")
        return False
