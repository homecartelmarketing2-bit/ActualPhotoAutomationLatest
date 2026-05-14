# =====================================================================
#  AIRTABLE SERVICE
#  Handles all reads/writes to Airtable records.
# =====================================================================

import requests

from config.settings import AIRTABLE_TOKEN, AIRTABLE_BASE_ID


# ── Helpers ─────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json",
    }


def _api_url(table_id: str) -> str:
    return f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{table_id}"


# ── Read ────────────────────────────────────────────────────────────

def get_next_unfinished_row(table_id: str):
    """
    Fetches ONE row whose Status is actionable.
    Returns (record_id, fields_dict) or (None, None).
    """
    params = {
        "filterByFormula": (
            "OR("
            "{Status} = 'Standby', "
            "{Status} = 'Processing Adding a Prompt', "
            "{Status} = 'Complete Adding a Prompt', "
            "{Status} = 'Processing'"
            ")"
        ),
        "maxRecords": 1,
    }
    try:
        resp = requests.get(_api_url(table_id), headers=_headers(), params=params)
        resp.raise_for_status()
        records = resp.json().get("records", [])
        if records:
            rec = records[0]
            return rec["id"], rec.get("fields", {})
        return None, None
    except requests.RequestException as e:
        print(f"[ERROR] Fetching unfinished row: {e}")
        return None, None


def refetch_record(table_id: str, record_id: str) -> dict:
    """Re-fetches a single record and returns its fields."""
    try:
        resp = requests.get(f"{_api_url(table_id)}/{record_id}", headers=_headers())
        resp.raise_for_status()
        return resp.json().get("fields", {})
    except requests.RequestException as e:
        print(f"[ERROR] Re-fetching record: {e}")
        return {}


# ── Write ───────────────────────────────────────────────────────────

def _patch(table_id: str, record_id: str, fields: dict) -> bool:
    """Generic PATCH wrapper for a single record."""
    url = f"{_api_url(table_id)}/{record_id}"
    try:
        resp = requests.patch(url, headers=_headers(), json={"fields": fields})
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[ERROR] Patching record ({fields}): {e}")
        return False


def update_status(table_id: str, record_id: str, status: str) -> bool:
    ok = _patch(table_id, record_id, {"Status": status})
    if ok:
        print(f"[OK] Status → '{status}'")
    return ok


def update_field(table_id: str, record_id: str, field_name: str, value) -> bool:
    ok = _patch(table_id, record_id, {field_name: value})
    if ok:
        print(f"[OK] '{field_name}' updated")
    return ok


def update_attachment(table_id: str, record_id: str, field_name: str, url: str) -> bool:
    ok = _patch(table_id, record_id, {field_name: [{"url": url}]})
    if ok:
        print(f"[OK] '{field_name}' attachment uploaded")
    return ok
