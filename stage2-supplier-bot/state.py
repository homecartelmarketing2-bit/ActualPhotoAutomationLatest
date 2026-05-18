"""
Persistent JSON state for the HC SPEC bot.

A Zoho "Encoding Request" record can carry multiple items in its
`Product_Name1` subform (each item = one product/SKU). We treat each
item as a separate work unit: one Telegram message kay Tony, one
Akeneo upload per SKU. To track them independently we key `pending`
and `processed_records` by `"<record_id>:<item_id>"` (use
`item_pending_key()` to build the key).

Schema:
{
  "telegram_chat_id": -1001234567890,
  "telegram_update_offset": 0,
  "pending": {
    "<record_id>:<item_id>": {
      "record_id":            "<record_id>",
      "item_id":              "<item_id>",
      "product_name":         "Tartarus | Chandelier",
      "akeneo_identifier":    "10227P/11",
      "sales_notes":          "...",
      "request_type":         "Actual Photo",
      "telegram_message_id":  456,
      "sent_time":            "2026-04-13T10:00:00",
      "last_sent_time":       "2026-04-13T10:00:00"
    }
  },
  "message_to_record": {
    "456": "<record_id>:<item_id>"
  },
  "processed_records": {
    "<record_id>:<item_id>": { ... }
  }
}
"""

import json
import os
import threading
from datetime import datetime, timezone

import config

_lock = threading.Lock()


def _path():
    return config.STATE_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> dict:
    return {
        "telegram_chat_id":       None,
        "telegram_update_offset": 0,
        "pending":                {},
        "message_to_record":      {},
        "invalid_records":        {},
        "processed_records":      {},
    }


def _normalize_state(state: dict | None) -> dict:
    merged = _default_state()
    if isinstance(state, dict):
        merged.update(state)

    for key in (
        "pending",
        "message_to_record",
        "invalid_records",
        "processed_records",
    ):
        if not isinstance(merged.get(key), dict):
            merged[key] = {}

    return merged


def load() -> dict:
    if os.path.exists(_path()):
        try:
            with open(_path(), "r", encoding="utf-8") as f:
                return _normalize_state(json.load(f))
        except Exception:
            pass
    return _default_state()


def save(state: dict):
    with _lock:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)


def get_chat_id(state: dict) -> int | None:
    # Prefer env var over saved state
    env_id = config.TELEGRAM_CHAT_ID
    if env_id:
        try:
            return int(env_id)
        except ValueError:
            pass
    with _lock:
        return state.get("telegram_chat_id")


def set_chat_id(state: dict, chat_id: int):
    with _lock:
        state["telegram_chat_id"] = chat_id
    save(state)


def item_pending_key(record_id: str, item_id: str) -> str:
    """
    Build the composite key used to track one item of a Zoho record.

    `record_id` is the parent encoding request; `item_id` is the
    `Product_Name1[*].ID` of the subform row. Legacy callers without
    item_id should pass an empty string — the key still works but
    represents the whole record.
    """
    return f"{record_id}:{item_id}"


def split_pending_key(pending_key: str) -> tuple[str, str]:
    """Inverse of `item_pending_key()`. Returns (record_id, item_id)."""
    if ":" not in pending_key:
        # Legacy: pre-subform keys were just the bare record_id.
        return pending_key, ""
    record_id, _, item_id = pending_key.partition(":")
    return record_id, item_id


def add_pending(state: dict, pending_key: str, product_name: str, sales_notes: str,
                akeneo_identifier: str, message_id: int, sent_time: str,
                request_type: str = "",
                record_id: str = "", item_id: str = ""):
    if not record_id:
        record_id, _maybe_item = split_pending_key(pending_key)
        if not item_id:
            item_id = _maybe_item
    with _lock:
        state["pending"][pending_key] = {
            "record_id":          record_id,
            "item_id":            item_id,
            "product_name":       product_name,
            "sales_notes":        sales_notes,
            "akeneo_identifier":  akeneo_identifier,   # Akeneo product id/code for uploads
            "request_type":       request_type,
            "telegram_message_id": message_id,
            "sent_time":          sent_time,
            "last_sent_time":     sent_time,
        }
        state["message_to_record"][str(message_id)] = pending_key
    save(state)


def remove_pending(state: dict, pending_key: str):
    with _lock:
        state["pending"].pop(pending_key, None)
        stale_message_ids = [
            message_id
            for message_id, mapped_key in state["message_to_record"].items()
            if mapped_key == pending_key
        ]
        for message_id in stale_message_ids:
            state["message_to_record"].pop(message_id, None)
    save(state)


def register_message(state: dict, pending_key: str, message_id: int, sent_time: str | None = None):
    with _lock:
        if pending_key in state["pending"]:
            state["pending"][pending_key]["telegram_message_id"] = message_id
            if sent_time:
                state["pending"][pending_key]["last_sent_time"] = sent_time
        state["message_to_record"][str(message_id)] = pending_key
    save(state)


def update_last_sent(state: dict, pending_key: str, last_sent_time: str):
    with _lock:
        if pending_key in state["pending"]:
            state["pending"][pending_key]["last_sent_time"] = last_sent_time
    save(state)


def record_id_for_message(state: dict, message_id: int) -> str | None:
    """
    Map a Telegram message_id back to the pending key it belongs to.

    The function is named `record_id_for_message` for backward
    compatibility, but with the subform refactor it now returns the
    composite pending_key (`<record_id>:<item_id>`).
    """
    with _lock:
        return state["message_to_record"].get(str(message_id))


def is_pending(state: dict, pending_key: str) -> bool:
    with _lock:
        return pending_key in state["pending"]


def pending_keys_for_record(state: dict, record_id: str) -> list[str]:
    """Return all pending_keys currently waiting on a reply for `record_id`."""
    prefix = f"{record_id}:"
    with _lock:
        return [
            key for key in state["pending"]
            if key == record_id or key.startswith(prefix)
        ]


def mark_invalid_record(
    state: dict,
    record_id: str,
    reason: str,
    product_name: str = "",
    akeneo_identifier: str = "",
) -> bool:
    now_iso = _now_iso()
    with _lock:
        current = state["invalid_records"].get(record_id, {})
        should_alert = not current or current.get("reason") != reason
        state["invalid_records"][record_id] = {
            "reason":             reason,
            "product_name":       product_name,
            "akeneo_identifier":  akeneo_identifier,
            "last_seen_time":     now_iso,
            "last_alert_time":    now_iso if should_alert else current.get("last_alert_time", ""),
        }
    save(state)
    return should_alert


def clear_invalid_record(state: dict, record_id: str) -> bool:
    removed = False
    with _lock:
        removed = state["invalid_records"].pop(record_id, None) is not None
    if removed:
        save(state)
    return removed


def mark_processed(
    state: dict,
    pending_key: str,
    request_type: str,
    *,
    final_status: str = "",
    product_name: str = "",
    akeneo_identifier: str = "",
) -> None:
    """
    Record that we have finished processing an item so the poller does not
    immediately pick it up again on the next cycle.

    `pending_key` is the composite `<record_id>:<item_id>` produced by
    `item_pending_key()`.

    Used mainly for "Supplier Actual Photo" items, which are now polled
    regardless of Request_Status — once the supplier has replied and the
    item has been resolved Done / Not available, we don't want to re-send the
    Telegram request on every poll.
    """
    record_id, item_id = split_pending_key(pending_key)
    with _lock:
        state["processed_records"][pending_key] = {
            "record_id":         record_id,
            "item_id":           item_id,
            "request_type":      request_type,
            "final_status":      final_status,
            "product_name":      product_name,
            "akeneo_identifier": akeneo_identifier,
            "processed_at":      _now_iso(),
        }
    save(state)


def is_processed(state: dict, pending_key: str, request_type: str) -> bool:
    """Return True if we previously processed this item for this request type."""
    with _lock:
        entry = state["processed_records"].get(pending_key)
        if not entry:
            return False
        # If the dropdown has been changed to a different request type since we
        # last processed, don't skip — treat it as a fresh request.
        if entry.get("request_type") and entry["request_type"] != request_type:
            return False
        return True


def clear_processed(state: dict, pending_key: str) -> bool:
    """Forget a previously processed item so it can be re-sent if needed."""
    removed = False
    with _lock:
        removed = state["processed_records"].pop(pending_key, None) is not None
    if removed:
        save(state)
    return removed


def processed_items_for_record(state: dict, record_id: str) -> list[dict]:
    """Return all processed entries belonging to a single record_id."""
    with _lock:
        return [
            entry for entry in state["processed_records"].values()
            if entry.get("record_id") == record_id
        ]
