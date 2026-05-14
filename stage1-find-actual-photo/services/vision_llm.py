# =====================================================================
#  VISION LLM SERVICE
#  Generates creative interior-design prompts from reference photos
#  using a local LM Studio vision model.
# =====================================================================

import os
import io
import random
import base64

import requests
from PIL import Image

from config.settings import (
    VISION_LLM_MODEL,
    VISION_LLM_ENDPOINTS,
    VISION_LLM_TIMEOUT,
    LOCAL_PHOTO_DIR,
)
from config.prompts import (
    VISION_LLM_SYSTEM_PROMPT,
    STYLE_VARIATIONS,
    MOOD_VARIATIONS,
    build_vision_user_prompt,
)


# ── Image Encoding ──────────────────────────────────────────────────

def _encode_image(image_path: str, max_size: int = 800) -> str:
    """Resizes and base64-encodes an image for the vision model."""
    img = Image.open(image_path)
    img.thumbnail((max_size, max_size))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


# ── Photo Selection ─────────────────────────────────────────────────

def get_random_local_photo() -> str | None:
    """Picks a random photo from the local reference directory."""
    if not os.path.exists(LOCAL_PHOTO_DIR):
        print(f"[ERROR] Directory not found: {LOCAL_PHOTO_DIR}")
        return None

    valid_ext = (".jpg", ".jpeg", ".png")
    photos = [f for f in os.listdir(LOCAL_PHOTO_DIR) if f.lower().endswith(valid_ext)]

    if not photos:
        print(f"[ERROR] No photos found in {LOCAL_PHOTO_DIR}")
        return None

    return os.path.join(LOCAL_PHOTO_DIR, random.choice(photos))


# ── Prompt Generation ───────────────────────────────────────────────

def generate_prompt(image_path: str) -> str | None:
    """
    Sends a reference photo to the local vision LLM and returns a
    creative interior-design prompt. Tries each configured endpoint
    in order (fallback chain).
    """
    base64_image = _encode_image(image_path)
    chosen_style = random.choice(STYLE_VARIATIONS)
    chosen_mood = random.choice(MOOD_VARIATIONS)

    payload = {
        "model": VISION_LLM_MODEL,
        "messages": [
            {"role": "system", "content": VISION_LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_vision_user_prompt(chosen_style, chosen_mood)},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            },
        ],
        "max_tokens": 500,
        "temperature": 1.0,
    }

    headers = {"Content-Type": "application/json"}

    for endpoint in VISION_LLM_ENDPOINTS:
        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=VISION_LLM_TIMEOUT)
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0]["message"]["content"]
        except requests.RequestException:
            continue  # try next endpoint

    print("[ERROR] Vision LLM generation failed on all endpoints")
    return None
