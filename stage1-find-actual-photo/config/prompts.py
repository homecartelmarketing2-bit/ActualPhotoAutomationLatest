# =====================================================================
#  PROMPTS & CREATIVE VARIATIONS
# =====================================================================

# ── Video Prompts ───────────────────────────────────────────────────
BEFORE_REELS_PROMPT = "Slowly Rotating Around Camera Movement"
AFTER_REELS_PROMPT = "Slowly Rotating Around Camera Movement"

# ── Moodboard Prompt ────────────────────────────────────────────────
MOODBOARD_PROMPT = (
    "Create a vertical professional interior design moodboard collage "
    "in a 3:4 aspect ratio featuring the main furniture and lighting "
    "fixtures visible in the reference image.\n\n"
    "Action: Isolate the items and arrange them in an editorial "
    "flat-lay composition. Remove the original room background.\n\n"
    "Layout Style:\n\n"
    "Background: Solid cream or off-white paper texture.\n"
    "Composition: Vertical layout suitable for a 3:4 frame. Layer the "
    "furniture over abstract organic wavy shapes and 'blobs' in colors "
    "derived from the furniture materials.\n"
    "Elements: Include a vertical color palette strip on the right side "
    "and circular material texture swatches.\n"
    "Vibe: Retro-modern graphic design, 'Hilary Jane Home' aesthetic, "
    "stylish curation.\n"
    "Technical Specs: Vertical 3:4 aspect ratio, 4k resolution, "
    "photorealistic textures, soft lighting"
)

# ── Vision LLM System Prompt ───────────────────────────────────────
VISION_LLM_SYSTEM_PROMPT = (
    "You are a creative interior design prompt engineer. You look at "
    "reference photos ONLY to understand the general aesthetic taste "
    "and style preferences. You DO NOT describe or copy the photo. "
    "Instead, you use it as loose inspiration to invent a completely "
    "NEW, UNIQUE interior design scene. Every prompt you create must "
    "be original, creative, and different from literal descriptions. "
    "Think of yourself as a designer brainstorming fresh ideas "
    "inspired by a mood board."
)

# ── Style & Mood Pools (picked randomly per generation) ────────────
STYLE_VARIATIONS = [
    "modern minimalist", "mid-century modern", "Scandinavian hygge",
    "industrial loft", "bohemian eclectic", "art deco glamour",
    "Japanese wabi-sabi", "coastal Mediterranean", "rustic farmhouse",
    "contemporary luxury", "retro 70s", "neo-classical",
    "tropical resort", "urban chic", "French provincial",
    "desert modern", "biophilic green", "maximalist bold",
    "transitional elegance", "Hollywood regency",
]

MOOD_VARIATIONS = [
    "warm and inviting", "cool and serene", "dramatic and moody",
    "bright and airy", "cozy and intimate", "sleek and sophisticated",
    "earthy and organic", "vibrant and energetic", "soft and romantic",
    "bold and edgy", "peaceful and zen", "opulent and rich",
]


def build_vision_user_prompt(style: str, mood: str) -> str:
    """Builds the user-facing prompt sent alongside the reference image."""
    return (
        f"Look at this interior design reference photo. Use it ONLY as "
        f"style inspiration — do NOT describe what you see literally. "
        f"Instead, invent a completely new and unique interior design "
        f"scene prompt. The style direction should lean towards "
        f"'{style}' with a '{mood}' mood. Include specific furniture "
        f"pieces, lighting fixtures, materials, textures, and color "
        f"palettes. Make the prompt detailed enough for an AI image "
        f"generator to create a stunning, original interior photo. "
        f"Keep it under 400 characters. Output ONLY the prompt text, "
        f"nothing else."
    )
