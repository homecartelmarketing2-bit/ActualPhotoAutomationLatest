"""
Akeneo PIM client.
Handles: auth, get product by SKU, check/download actual photo,
         find catalog/reference photo, upload actual photo.
"""

import base64
import json
import time
import requests
import config
from config import log

_session = requests.Session()
_access_token  = None
_refresh_token = None
_expires_at    = 0.0


def _authenticate():
    global _access_token, _refresh_token, _expires_at

    if _access_token and time.time() < _expires_at - 60:
        return _access_token

    credentials = base64.b64encode(
        f"{config.AKENEO_CLIENT_ID}:{config.AKENEO_SECRET}".encode()
    ).decode()

    if _refresh_token:
        payload = {"grant_type": "refresh_token", "refresh_token": _refresh_token}
    else:
        payload = {
            "grant_type": "password",
            "username":   config.AKENEO_USERNAME,
            "password":   config.AKENEO_PASSWORD,
        }

    resp = _session.post(
        f"{config.AKENEO_HOST}/api/oauth/v1/token",
        json=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    _access_token  = data["access_token"]
    _refresh_token = data.get("refresh_token")
    _expires_at    = time.time() + int(data.get("expires_in", 3600))
    log.info("Akeneo: authenticated OK.")
    return _access_token


def _auth_header():
    return {"Authorization": f"Bearer {_authenticate()}"}


def _get(path, retries=3, **kwargs):
    global _access_token
    for attempt in range(retries):
        try:
            resp = _session.get(
                f"{config.AKENEO_HOST}{path}",
                headers=_auth_header(),
                timeout=60,
                **kwargs,
            )
            if resp.status_code == 401:
                _access_token = None
                resp = _session.get(
                    f"{config.AKENEO_HOST}{path}",
                    headers=_auth_header(),
                    timeout=60,
                    **kwargs,
                )
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                log.warning(f"Akeneo connection error (attempt {attempt+1}), retry in {wait}s: {exc}")
                time.sleep(wait)
            else:
                raise


def _get_name(item: dict) -> str:
    """Extract the display name from an Akeneo product or product-model."""
    name_vals = item.get("values", {}).get("name", [])
    if name_vals:
        return name_vals[0].get("data", "")
    return item.get("code", item.get("identifier", ""))


def get_product_by_identifier(identifier: str) -> dict | None:
    """Fetch a simple product by its exact identifier."""
    resp = _get(f"/api/rest/v1/products/{identifier}")
    if resp.status_code == 404:
        return None
    if not resp.ok:
        log.warning(f"Akeneo get_product({identifier}): HTTP {resp.status_code}")
        return None
    return resp.json()


def get_product_model_by_code(code: str) -> dict | None:
    """Fetch a product-model by its exact code."""
    resp = _get(
        "/api/rest/v1/product-models",
        params={"search": json.dumps({"code": [{"operator": "=", "value": code}]})},
    )
    if not resp.ok:
        return None
    items = resp.json().get("_embedded", {}).get("items", [])
    return items[0] if items else None


def find_product_by_name(product_name: str) -> tuple[str, dict] | tuple[None, None]:
    """
    Search Akeneo for a product/product-model whose name matches product_name.
    Returns (identifier_or_code, product_dict) or (None, None).

    Strategy:
    1. Try the part before " | " as exact identifier/code (fast path)
    2. Search products by name attribute (contains search)
    3. Search product-models by name attribute
    """
    # Fast path: use the short name before " | " as identifier
    short_name = product_name.split("|")[0].strip() if "|" in product_name else product_name

    # Try short name as product identifier
    product = get_product_by_identifier(short_name)
    if product:
        return short_name, product

    # Try short name as product-model code
    model = get_product_model_by_code(short_name)
    if model:
        return short_name, model

    # Try full name as identifier
    product = get_product_by_identifier(product_name)
    if product:
        return product_name, product

    # Search products by name attribute (contains)
    search_vals = [short_name]
    if "|" not in product_name and " " in product_name:
        parts = product_name.split()
        if len(parts) >= 2:
            first_two = f"{parts[0]} {parts[1]}"
            if first_two != short_name:
                search_vals.append(first_two)
        
        first_word = parts[0]
        if first_word and first_word != short_name:
            search_vals.append(first_word)

    for search_val in search_vals:
        try:
            resp = _get(
                "/api/rest/v1/products",
                params={
                    "search": json.dumps({
                        "name": [{"attribute": "name", "operator": "CONTAINS", "value": search_val}]
                    }),
                    "limit": 5,
                    "pagination_type": "search_after",
                },
            )
            if resp.ok:
                items = resp.json().get("_embedded", {}).get("items", [])
                if items:
                    best = items[0]
                    return best.get("identifier", ""), best
        except Exception as exc:
            log.warning(f"Akeneo name search failed for '{search_val}': {exc}")

        # Search product-models by name
        try:
            resp = _get(
                "/api/rest/v1/product-models",
                params={
                    "search": json.dumps({
                        "name": [{"attribute": "name", "operator": "CONTAINS", "value": search_val}]
                    }),
                    "limit": 5,
                },
            )
            if resp.ok:
                items = resp.json().get("_embedded", {}).get("items", [])
                if items:
                    best = items[0]
                    return best.get("code", ""), best
        except Exception as exc:
            log.warning(f"Akeneo product-model name search failed for '{search_val}': {exc}")

    log.info(f"Akeneo: no product found for '{product_name}'")
    return None, None


def lookup_product(product_name: str) -> tuple[str, dict | None, bool, bytes | None]:
    """
    Single Akeneo lookup per product — called once in run.py per record.
    Returns: (akeneo_identifier, product_dict, has_actual_photo, catalog_photo_bytes)
    """
    identifier, product = find_product_by_name(product_name)
    if not product:
        log.info(f"Akeneo: product not found for '{product_name}'")
        return "", None, False, None

    values = product.get("values", {})

    # Check actual photo
    actual_entries = values.get(config.AKENEO_ACTUAL_ATTR, [])
    has_actual = any(entry.get("data") for entry in actual_entries)

    # Get catalog photo
    catalog_bytes = None
    for attr in config.AKENEO_CATALOG_ATTRS:
        entries = values.get(attr, [])
        for entry in entries:
            media_code = entry.get("data")
            if not media_code:
                continue
            log.info(f"Akeneo: downloading catalog photo from attr '{attr}' for '{product_name}'...")
            resp = _get(f"/api/rest/v1/media-files/{media_code}/download")
            if resp.status_code == 200 and len(resp.content) > 100:
                catalog_bytes = resp.content
                break
        if catalog_bytes:
            break

    if not catalog_bytes:
        log.info(f"Akeneo: no catalog photo found for '{product_name}'.")

    return identifier or "", product, has_actual, catalog_bytes


# Keep these for backward compatibility with __main__.py --test-akeneo
def has_actual_photo(product_name: str) -> bool:
    _, _, has_actual, _ = lookup_product(product_name)
    return has_actual


def get_catalog_photo_bytes(product_name: str) -> tuple[bytes | None, str]:
    identifier, _, _, catalog_bytes = lookup_product(product_name)
    return catalog_bytes, identifier


def upload_actual_photo(identifier: str, file_bytes: bytes, filename: str = "actual_photo.jpg") -> bool:
    """
    Upload file_bytes as the Actual_Photo for the product in Akeneo.
    identifier: the Akeneo product identifier or product-model code.
    Step 1: POST /media-files to get the new media code.
    Step 2: PATCH /products/{identifier} to link the media file.
    Returns True on success.
    """
    if not identifier:
        log.warning("Akeneo upload_actual_photo: no identifier provided, skipping.")
        return False

    token = _authenticate()

    # Step 1: upload the media file
    product_data = json.dumps({
        "identifier": identifier,
        "attribute":  config.AKENEO_ACTUAL_ATTR,
        "locale":     None,
        "scope":      None,
    })
    upload_resp = _session.post(
        f"{config.AKENEO_HOST}/api/rest/v1/media-files",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "product": (None, product_data, "application/json"),
            "file":    (filename, file_bytes),
        },
        timeout=120,
    )
    if not upload_resp.ok:
        if upload_resp.status_code == 422 and "not in the attribute set" in upload_resp.text:
            log.warning(f"Akeneo upload_actual_photo skipped: {config.AKENEO_ACTUAL_ATTR} is not in the attribute set for '{identifier}'.")
            return False
        log.error(f"Akeneo upload_actual_photo failed: {upload_resp.status_code} {upload_resp.text[:300]}")
        return False

    # The media code is returned in the Location header or response body
    media_code = None
    location = upload_resp.headers.get("Location", "")
    if location:
        media_code = location.rstrip("/").split("/")[-1]
    else:
        # Some versions return it in the body
        try:
            media_code = upload_resp.json().get("code")
        except Exception:
            pass

    log.info(f"Akeneo: uploaded actual photo for {identifier} OK (media code: {media_code}).")
    return True


def upload_video(identifier: str, file_bytes: bytes, filename: str = "video.mp4") -> bool:
    """
    Upload a video to Akeneo linked to the product.
    identifier: the Akeneo product identifier or product-model code.
    """
    if not identifier:
        log.warning("Akeneo upload_video: no identifier provided, skipping.")
        return False

    token = _authenticate()

    product_data = json.dumps({
        "identifier": identifier,
        "attribute":  "Video",   # adjust if Akeneo uses a different attribute name
        "locale":     None,
        "scope":      None,
    })
    upload_resp = _session.post(
        f"{config.AKENEO_HOST}/api/rest/v1/media-files",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "product": (None, product_data, "application/json"),
            "file":    (filename, file_bytes),
        },
        timeout=180,
    )
    if not upload_resp.ok:
        log.error(f"Akeneo upload_video failed: {upload_resp.status_code} {upload_resp.text[:300]}")
        return False

    log.info(f"Akeneo: uploaded video for {identifier} OK.")
    return True
