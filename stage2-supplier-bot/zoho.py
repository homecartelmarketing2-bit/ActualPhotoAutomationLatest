"""
Zoho Creator client for All_Encoding_Requests report.
Handles: query pending records, update fields, upload photo/video.
"""

import time
import requests
import config
from config import log


class ZohoAuth:
    def __init__(self):
        self._token = None
        self._expiry = 0

    def get_token(self):
        if self._token and time.time() < self._expiry:
            return self._token
        log.info("Refreshing Zoho access token...")
        resp = requests.post(config.ZOHO_TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "client_id":     config.ZOHO_CLIENT_ID,
            "client_secret": config.ZOHO_CLIENT_SECRET,
            "refresh_token": config.ZOHO_REFRESH_TOKEN,
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise RuntimeError(f"Zoho token refresh failed: {data}")
        self._token  = data["access_token"]
        self._expiry = time.time() + data.get("expires_in", 3600) - 300
        log.info("Zoho token refreshed OK.")
        return self._token

    def headers(self):
        return {"Authorization": f"Zoho-oauthtoken {self.get_token()}"}


# Module-level singleton so all callers share one token cache
_auth = ZohoAuth()


def _report_url():
    return (
        f"{config.ZOHO_CREATOR_BASE}"
        f"/{config.ZOHO_ACCOUNT}"
        f"/{config.ZOHO_APP}"
        f"/report/{config.ZOHO_REPORT}"
    )


def get_pending_records():
    """
    Return all records in All_Encoding_Requests where:
      - Request_Status == "Pending"
      - Remarks_Notes contains the trigger text set in config
    Uses server-side criteria for status; filters remarks in Python.
    """
    all_records = []
    start = 0
    page_size = 200

    while True:
        resp = requests.get(
            _report_url(),
            headers=_auth.headers(),
            params={
                # Only "Actual Photo" requests that are still Pending
                "criteria": '(Request_Status=="Pending"||Request_Status=="In progress")&&(Type_of_Request=="Actual Photo")',
                "from":     start,
                "limit":    page_size,
            },
            timeout=60,
        )
        resp.raise_for_status()
        page = resp.json().get("data", [])
        if not page:
            break
        all_records.extend(page)
        if len(page) < page_size:
            break
        start += page_size

    # Further filter: only those where Remarks_Notes contains the trigger text
    # (i.e. the original automation already tried and found nothing)
    filtered = [
        r for r in all_records
        if config.TRIGGER_TEXT.lower() in str(r.get("Remarks_Notes", "")).lower()
    ]
    log.info(
        f"Zoho: {len(all_records)} Pending/In-progress Actual-Photo records, (filtered {len(filtered)})"
    )
    return filtered


def search_record_by_product_name(product_name: str) -> dict | None:
    """
    Search for a pending or in-progress record by Product_Name match.
    """
    all_records = []
    start = 0
    page_size = 200

    while True:
        resp = requests.get(
            _report_url(),
            headers=_auth.headers(),
            params={
                "criteria": '(Request_Status=="Pending"||Request_Status=="In progress")&&(Type_of_Request=="Actual Photo")',
                "from":     start,
                "limit":    page_size,
            },
            timeout=60,
        )
        resp.raise_for_status()
        page = resp.json().get("data", [])
        if not page:
            break
        all_records.extend(page)
        if len(page) < page_size:
            break
        start += page_size

    target = product_name.lower().strip()
    for record in all_records:
        # Check standard fields representing Product Name
        rec_name = ""
        for key in ("Product_Name", "Product_name", "product_name", "Name", "name"):
            val = record.get(key)
            if val and str(val).strip():
                rec_name = str(val).lower().strip()
                break
        
        if rec_name and (target == rec_name or target in rec_name or rec_name in target):
            return record
            
    return None


def debug_fields():
    """Print all field names from the first record (for verification)."""
    resp = requests.get(
        _report_url(),
        headers=_auth.headers(),
        params={"from": 0, "limit": 2},
        timeout=60,
    )
    resp.raise_for_status()
    records = resp.json().get("data", [])
    if not records:
        print("No records found.")
        return
    print(f"\n=== Fields in {config.ZOHO_REPORT} (first record) ===")
    for key, val in sorted(records[0].items()):
        print(f"  {key:35s} = {str(val)[:80]}")


def update_record(record_id, fields: dict):
    """
    PATCH a record to update text/select fields.
    fields: dict of { field_name: value }
    """
    url = f"{_report_url()}/{record_id}"
    resp = requests.patch(
        url,
        headers={**_auth.headers(), "Content-Type": "application/json"},
        json={"data": fields},
        timeout=30,
    )
    if not resp.ok:
        log.error(f"Zoho update_record {record_id} failed: {resp.status_code} {resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()


def upload_file(record_id, field_name, file_bytes, filename="file.jpg"):
    """
    Upload a photo or video to a Creator file-upload field.
    POST /report/{report}/{record_id}/{field_name}/upload
    """
    url = f"{_report_url()}/{record_id}/{field_name}/upload"
    resp = requests.post(
        url,
        headers=_auth.headers(),
        files={"file": (filename, file_bytes)},
        timeout=120,
    )
    if not resp.ok:
        log.error(f"Zoho upload_file {record_id}/{field_name} failed: {resp.status_code} {resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()
