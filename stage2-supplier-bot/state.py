"""
Persistent JSON state for the HC SPEC bot.

Schema:
{
  "telegram_chat_id": -1001234567890,
  "telegram_update_offset": 0,
  "pending": {
    "<record_id>": {
      "sku": "10227P/11",
      "product_name": "Tartarus | Chandelier",
      "sales_notes": "...",
      "telegram_message_id": 456,
      "sent_time": "2026-04-13T10:00:00",
      "last_sent_time": "2026-04-13T10:00:00"
    }
  },
  "message_to_record": {
    "456": "<record_id>"
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


def add_pending(state: dict, record_id: str, product_name: str, sales_notes: str,
                akeneo_identifier: str, message_id: int, sent_time: str,
                request_type: str = ""):
    with _lock:
        state["pending"][record_id] = {
            "product_name":       product_name,
            "sales_notes":        sales_notes,
            "akeneo_identifier":  akeneo_identifier,   # Akeneo product id/code for uploads
            "request_type":       request_type,
            "telegram_message_id": message_id,
            "sent_time":          sent_time,
            "last_sent_time":     sent_time,
        }
        state["message_to_record"][str(message_id)] = record_id
    save(state)


def remove_pending(state: dict, record_id: str):
    with _lock:
        state["pending"].pop(record_id, None)
        stale_message_ids = [
            message_id
            for message_id, mapped_record_id in state["message_to_record"].items()
            if mapped_record_id == record_id
        ]
        for message_id in stale_message_ids:
            state["message_to_record"].pop(message_id, None)
    save(state)


def register_message(state: dict, record_id: str, message_id: int, sent_time: str | None = None):
    with _lock:
        if record_id in state["pending"]:
            state["pending"][record_id]["telegram_message_id"] = message_id
            if sent_time:
                state["pending"][record_id]["last_sent_time"] = sent_time
        state["message_to_record"][str(message_id)] = record_id
    save(state)


def update_last_sent(state: dict, record_id: str, last_sent_time: str):
    with _lock:
        if record_id in state["pending"]:
            state["pending"][record_id]["last_sent_time"] = last_sent_time
    save(state)


def record_id_for_message(state: dict, message_id: int) -> str | None:
    with _lock:
        return state["message_to_record"].get(str(message_id))


def is_pending(state: dict, record_id: str) -> bool:
    with _lock:
        return record_id in state["pending"]


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
    record_id: str,
    request_type: str,
    *,
    final_status: str = "",
    product_name: str = "",
    akeneo_identifier: str = "",
) -> None:
    """
    Record that we have finished processing a record so the poller does not
    immediately pick it up again on the next cycle.

    Used mainly for "Supplier Actual Photo" records, which are now polled
    regardless of Request_Status — once the supplier has replied and the
    record has been marked Done / NOT AVAILABLE, we don't want to re-send the
    Telegram request on every poll.
    """
    with _lock:
        state["processed_records"][record_id] = {
            "request_type":      request_type,
            "final_status":      final_status,
            "product_name":      product_name,
            "akeneo_identifier": akeneo_identifier,
            "processed_at":      _now_iso(),
        }
    save(state)


def is_processed(state: dict, record_id: str, request_type: str) -> bool:
    """Return True if we previously processed this record for this request type."""
    with _lock:
        entry = state["processed_records"].get(record_id)
        if not entry:
            return False
        # If the dropdown has been changed to a different request type since we
        # last processed, don't skip — treat it as a fresh request.
        if entry.get("request_type") and entry["request_type"] != request_type:
            return False
        return True


def clear_processed(state: dict, record_id: str) -> bool:
    """Forget a previously processed record so it can be re-sent if needed."""
    removed = False
    with _lock:
        removed = state["processed_records"].pop(record_id, None) is not None
    if removed:
        save(state)
    return removed
