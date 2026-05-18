from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SUPPORTED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
SUPPORTED_VIDEO_EXTENSIONS = {"mp4", "mov", "avi"}
SUPPORTED_MEDIA_EXTENSIONS = (
    SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
)

LOW_VALUE_WORDS = {
    "a",
    "an",
    "and",
    "black",
    "blue",
    "brown",
    "chair",
    "chandelier",
    "cm",
    "coffee",
    "dark",
    "gold",
    "gray",
    "grey",
    "ii",
    "iii",
    "iv",
    "lamp",
    "large",
    "led",
    "light",
    "lights",
    "modern",
    "new",
    "of",
    "or",
    "small",
    "sofa",
    "swing",
    "table",
    "the",
    "une",
    "v2",
    "wall",
    "white",
    "with",
    "arm",
    "pendant",
}


@dataclass(frozen=True)
class SearchTerms:
    original: str
    normalized: str
    tokens: tuple[str, ...]
    significant_tokens: tuple[str, ...]
    primary_keyword: str


@dataclass(frozen=True)
class MediaCandidate:
    source: str
    identifier: str
    name: str
    record_id: str | None = None
    field_name: str | None = None


@dataclass(frozen=True)
class MatchResult:
    source: str
    matched_name: str
    media: tuple[MediaCandidate, ...]
    detail: str = ""


def normalize_text(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return " ".join(cleaned.split())


def tokenize_text(value: str) -> tuple[str, ...]:
    normalized = normalize_text(value)
    return tuple(token for token in normalized.split(" ") if token)


def build_search_terms(product_name: str) -> SearchTerms:
    # Use everything before "|" as the primary keyword for searching
    # e.g. "Roger | Pendant Light" → primary = "roger"
    # e.g. "Agatta 3 | Modern Chandelier" → primary = "agatta 3"
    before_pipe = product_name.split("|")[0].strip() if "|" in product_name else product_name
    tokens = tokenize_text(product_name)
    significant = tuple(token for token in tokens if token not in LOW_VALUE_WORDS)
    primary = normalize_text(before_pipe) if before_pipe else (
        significant[0] if significant else (tokens[0] if tokens else "")
    )
    return SearchTerms(
        original=product_name,
        normalized=" ".join(tokens),
        tokens=tokens,
        significant_tokens=significant,
        primary_keyword=primary,
    )


def get_supported_extension(filename: str) -> str | None:
    extension = Path(filename).suffix.lower().lstrip(".")
    return extension if extension in SUPPORTED_MEDIA_EXTENSIONS else None


def is_supported_media(filename: str) -> bool:
    return get_supported_extension(filename) is not None


def media_kind(filename: str) -> str | None:
    extension = get_supported_extension(filename)
    if extension is None:
        return None
    if extension in SUPPORTED_IMAGE_EXTENSIONS:
        return "image"
    return "video"


def candidate_score(candidate_name: str, search_terms: SearchTerms) -> int:
    normalized_candidate = normalize_text(candidate_name)
    if not normalized_candidate:
        return 0

    candidate_tokens = set(tokenize_text(candidate_name))
    significant = set(search_terms.significant_tokens or search_terms.tokens)
    overlap = len(significant & candidate_tokens)
    score = overlap * 10

    if search_terms.primary_keyword and search_terms.primary_keyword in candidate_tokens:
        score += 20
    if search_terms.normalized and search_terms.normalized == normalized_candidate:
        score += 100
    elif search_terms.normalized and search_terms.normalized in normalized_candidate:
        score += 60
    elif significant and significant.issubset(candidate_tokens):
        score += 35

    return score


def unique_media(candidates: Iterable[MediaCandidate]) -> list[MediaCandidate]:
    seen: set[tuple[str, str]] = set()
    result: list[MediaCandidate] = []
    for candidate in candidates:
        key = (candidate.source, candidate.identifier)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def scalar_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(part for part in (scalar_to_text(item) for item in value) if part)
    if isinstance(value, dict):
        for key in (
            "display_value",
            "zc_display_value",
            "name",
            "value",
            "text",
            "label",
            "Link_Name",
        ):
            text = scalar_to_text(value.get(key))
            if text:
                return text
        for nested in value.values():
            text = scalar_to_text(nested)
            if text:
                return text
        return ""
    return str(value).strip()


def extract_subform_items(record: dict[str, Any], subform_field: str) -> list[dict[str, str]]:
    """
    Parse the per-item subform (default `Product_Name1`) from a Zoho encoding
    request record.

    Each subform row represents one product/SKU that sales added to the
    request. We treat each row as a SEPARATE work unit: separate search,
    separate Telegram message, separate Akeneo upload.

    Returns a list of dicts: {item_id, product_name, sku}.
    Returns an empty list if the subform is absent, not an array, or only
    contains empty rows — callers should fall back to the top-level
    `Product_Name` for legacy-shaped records in that case.
    """
    if not subform_field:
        return []
    subform = record.get(subform_field)
    if not isinstance(subform, list):
        return []

    items: list[dict[str, str]] = []
    for row in subform:
        if not isinstance(row, dict):
            continue
        item_id = scalar_to_text(row.get("ID"))
        sku     = scalar_to_text(row.get("SKU"))
        items_obj = row.get("Items")
        product_name = ""
        if isinstance(items_obj, dict):
            product_name = (
                scalar_to_text(items_obj.get("Item_Name"))
                or scalar_to_text(items_obj.get("zc_display_value"))
                or scalar_to_text(items_obj.get("display_value"))
            )
        elif items_obj is not None:
            product_name = scalar_to_text(items_obj)
        if not item_id and not sku and not product_name:
            continue
        items.append({
            "item_id":      item_id,
            "product_name": product_name,
            "sku":          sku,
        })
    return items


def extract_record_id(record: dict[str, Any]) -> str:
    for key in ("ID", "id", "Id"):
        value = scalar_to_text(record.get(key))
        if value:
            return value
    raise KeyError("Record ID not found in Creator response")


def creator_criteria_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
