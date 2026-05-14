# =====================================================================
#  ZOHO WORKDRIVE SERVICE
#  Handles OAuth token refresh, file uploads, and public link creation.
# =====================================================================

import os
import time
import tempfile
import requests

from config.settings import (
    ZOHO_CLIENT_ID,
    ZOHO_CLIENT_SECRET,
    ZOHO_REFRESH_TOKEN,
    ZOHO_TOKEN_URL,
    ZOHO_UPLOAD_URL,
    ZOHO_FOLDERS,
)


# ── Token Cache (module-level singleton) ────────────────────────────

_token_cache = {"token": None, "expires_at": 0}


def _get_access_token() -> str | None:
    """Returns a valid access token, refreshing when expired."""
    global _token_cache

    if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]

    try:
        resp = requests.post(ZOHO_TOKEN_URL, data={
            "refresh_token": ZOHO_REFRESH_TOKEN,
            "client_id": ZOHO_CLIENT_ID,
            "client_secret": ZOHO_CLIENT_SECRET,
            "grant_type": "refresh_token",
        })
        data = resp.json()

        if "access_token" not in data:
            print(f"[ERROR] Zoho token refresh failed: {data}")
            return None

        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
        print("[OK] Zoho access token refreshed")
        return data["access_token"]

    except Exception as e:
        print(f"[ERROR] Zoho token refresh error: {e}")
        return None


# ── File Helpers ────────────────────────────────────────────────────

def _download_to_temp(url: str, filename: str) -> str | None:
    """Downloads a remote file to a temp path."""
    filepath = os.path.join(tempfile.gettempdir(), filename)
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return filepath
    except Exception as e:
        print(f"[ERROR] Downloading file: {e}")
        return None


def _upload_file(token: str, filepath: str, filename: str, folder_id: str) -> requests.Response | None:
    """Uploads a local file to a Zoho folder and returns the response."""
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    try:
        with open(filepath, "rb") as f:
            return requests.post(
                ZOHO_UPLOAD_URL,
                headers=headers,
                files={"content": (filename, f)},
                data={"parent_id": folder_id, "override-name-exist": "true"},
            )
    except Exception as e:
        print(f"[ERROR] Zoho upload: {e}")
        return None


# ── Public API ──────────────────────────────────────────────────────

def upload_from_url(file_url: str, filename: str, folder_key: str) -> bool:
    """Downloads a remote file and uploads it to the named Zoho folder."""
    folder_id = ZOHO_FOLDERS.get(folder_key)
    if not folder_id:
        print(f"[ERROR] Unknown Zoho folder: {folder_key}")
        return False

    token = _get_access_token()
    if not token:
        print("[WARN] Skipping Zoho upload — no access token.")
        return False

    filepath = _download_to_temp(file_url, filename)
    if not filepath:
        return False

    try:
        print(f"[INFO] Uploading '{filename}' to Zoho/{folder_key}...")
        resp = _upload_file(token, filepath, filename, folder_id)

        if resp and resp.status_code in (200, 201):
            print(f"[OK] Uploaded to Zoho: {folder_key}/{filename}")
            return True

        status = resp.status_code if resp else "N/A"
        text = resp.text[:200] if resp else ""
        print(f"[WARN] Zoho upload response {status}: {text}")
        return False
    finally:
        _cleanup(filepath)


def upload_local_file(local_path: str, filename: str, folder_key: str) -> bool:
    """Uploads a local file directly to the named Zoho folder."""
    folder_id = ZOHO_FOLDERS.get(folder_key)
    if not folder_id:
        print(f"[ERROR] Unknown Zoho folder: {folder_key}")
        return False

    token = _get_access_token()
    if not token:
        print("[WARN] Skipping Zoho upload — no access token.")
        return False

    print(f"[INFO] Uploading '{filename}' to Zoho/{folder_key}...")
    resp = _upload_file(token, local_path, filename, folder_id)

    if resp and resp.status_code in (200, 201):
        print(f"[OK] Uploaded to Zoho: {folder_key}/{filename}")
        return True

    status = resp.status_code if resp else "N/A"
    text = resp.text[:200] if resp else ""
    print(f"[WARN] Zoho upload response {status}: {text}")
    return False


def upload_and_get_public_link(filepath: str, folder_key: str = "Before and After Reels") -> str | None:
    """
    Uploads a local file to Zoho, creates a public download link,
    and returns the URL (used for combined videos → Airtable).
    """
    folder_id = ZOHO_FOLDERS.get(folder_key)
    token = _get_access_token()
    if not token:
        print("[WARN] No Zoho access token for combined video upload.")
        return None

    filename = os.path.basename(filepath)

    try:
        print("[INFO] Uploading combined video to Zoho Drive...")
        resp = _upload_file(token, filepath, filename, folder_id)

        if not resp or resp.status_code not in (200, 201):
            status = resp.status_code if resp else "N/A"
            text = resp.text[:200] if resp else ""
            print(f"[ERROR] Zoho upload failed: {status} {text}")
            return None

        data = resp.json()
        resource_id = data["data"][0]["attributes"].get("resource_id", "")
        if not resource_id:
            print("[ERROR] No resource_id in Zoho upload response")
            return None

        print(f"[OK] Uploaded to Zoho. Resource ID: {resource_id}")

        # Create a public share link
        download_url = _create_share_link(token, resource_id, filename)
        if download_url:
            return download_url

        # Fallback: direct download API URL (needs auth)
        fallback = f"https://workdrive.zoho.com/api/v1/download/{resource_id}"
        print("[INFO] Using fallback download URL")
        return fallback

    except Exception as e:
        print(f"[ERROR] Combined video upload: {e}")
        return None


# ── Internal Helpers ────────────────────────────────────────────────

def _create_share_link(token: str, resource_id: str, filename: str) -> str | None:
    """Creates a public download link for a Zoho resource."""
    url = f"https://workdrive.zoho.com/api/v1/files/{resource_id}/links"
    payload = {
        "data": {
            "attributes": {
                "link_name": filename,
                "request_user_data": False,
                "allow_download": True,
                "role_id": "34",
            },
            "type": "links",
        }
    }
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            link = resp.json().get("data", {}).get("attributes", {}).get("link", "")
            if link:
                download_url = link.rstrip("/") + "?download=true"
                print(f"[OK] Public download link: {download_url}")
                return download_url
            print(f"[WARN] No link in share response: {resp.text[:200]}")
        else:
            print(f"[WARN] Share link creation failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[ERROR] Creating share link: {e}")

    return None


def _cleanup(*filepaths: str):
    """Removes temporary files silently."""
    for fp in filepaths:
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
