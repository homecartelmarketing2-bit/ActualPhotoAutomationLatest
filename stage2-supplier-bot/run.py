"""
HC SPEC Bot — main runtime.
Three threads:
  1. Poller   — every 5 min: find Pending records → send Telegram requests
  2. Follow-up — every 60 s tick: resend if no reply after 1 hour
  3. Telegram  — continuous long-poll: handle Tony's replies
"""

import time
import threading
from datetime import datetime, timezone

import akeneo
import bot
import config
import state as state_mod
import zoho
from config import log

# Event to wake the poller immediately (e.g. after /register)
_poll_trigger = threading.Event()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seconds_since(iso_str: str) -> float:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return 0.0


def _format_invalid_reason(missing_fields: list[str]) -> str:
    return f"Missing required fields: {', '.join(missing_fields)}"


def _mark_invalid_record(state: dict, record_id: str, missing_fields: list[str],
                         product_name: str, akeneo_identifier: str, context: str):
    reason = _format_invalid_reason(missing_fields)
    should_alert = state_mod.mark_invalid_record(
        state,
        record_id,
        reason,
        product_name=product_name,
        akeneo_identifier=akeneo_identifier,
    )
    if should_alert:
        bot.send_invalid_record_alert(
            record_id,
            missing_fields,
            product_name,
            akeneo_identifier,
            context,
        )
    log.warning(f"{context}: skipped invalid record {record_id} ({reason})")


def _clear_invalid_pending_entries(state: dict, context: str):
    for record_id, entry in list(state.get("pending", {}).items()):
        product_name = entry.get("product_name", "")
        akeneo_identifier = entry.get("akeneo_identifier", "")
        missing_fields = bot.missing_request_fields(product_name, akeneo_identifier)
        if not missing_fields:
            continue

        state_mod.remove_pending(state, record_id)
        _mark_invalid_record(
            state,
            record_id,
            missing_fields,
            product_name,
            akeneo_identifier,
            context,
        )
        log.info(f"{context}: removed invalid pending entry {record_id}")


# ── Thread 1: Zoho poller ────────────────────────────────────────────────────

def poller_loop(state: dict, stop_event: threading.Event):
    log.info("Poller thread started.")
    while not stop_event.is_set():
        try:
            _poll_once(state)
        except Exception as exc:
            log.error(f"Poller error: {exc}", exc_info=True)
        # Wait for POLL_INTERVAL, but wake early if _poll_trigger is set
        # (e.g. after /register)
        _poll_trigger.wait(timeout=config.POLL_INTERVAL)
        _poll_trigger.clear()
        if stop_event.is_set():
            break
    log.info("Poller thread stopped.")


def _poll_once(state: dict):
    chat_id = state_mod.get_chat_id(state)
    if not chat_id:
        log.warning("No Telegram chat_id configured. Send /register to the bot first.")
        return

    log.info("Polling Zoho for pending records...")
    try:
        records = zoho.get_pending_records()
    except Exception as exc:
        log.error(f"Zoho query failed: {exc}")
        return

    for record in records:
        record_id = str(record.get("ID", ""))
        if not record_id:
            continue
        if state_mod.is_pending(state, record_id):
            continue   # Already waiting for reply

        product_name = _extract_product_name(record)
        # Remarks_Notes2 is the "Sales Notes" field in the Zoho UI
        sales_notes  = str(record.get("Remarks_Notes2") or "").strip()
        request_type = str(record.get("Type_of_Request") or "").strip()

        log.info(f"New pending record: {record_id}  Type={request_type!r}  Product={product_name}")

        # Single Akeneo lookup — gets identifier, actual photo status, and catalog photo
        akeneo_identifier = ""
        photo_bytes = None
        lookup_failed = False
        try:
            if product_name:
                akeneo_identifier, _, has_actual, photo_bytes = akeneo.lookup_product(product_name)
                # The user requested to NOT skip even if Akeneo already has an actual photo.
                if has_actual:
                    log.info(f"  Akeneo already has actual photo for '{product_name}', but we will NOT skip requesting it as per user configuration.")
        except Exception as exc:
            lookup_failed = True
            log.warning(f"  Akeneo lookup failed for '{product_name}': {exc}")

        missing_fields = bot.missing_request_fields(product_name, akeneo_identifier)
        if missing_fields:
            if lookup_failed and missing_fields == ["SKU"]:
                log.warning(f"  Will retry record {record_id} next poll because Akeneo lookup failed before resolving the SKU.")
                continue
            _mark_invalid_record(
                state,
                record_id,
                missing_fields,
                product_name,
                akeneo_identifier,
                "poller",
            )
            continue

        state_mod.clear_invalid_record(state, record_id)

        # Send to Telegram
        message_id = bot.send_photo_request(
            chat_id, product_name, akeneo_identifier, sales_notes, photo_bytes,
            request_type=request_type,
        )
        if message_id is None:
            log.error(f"  Failed to send Telegram message for record {record_id}. Will retry next poll.")
            continue

        sent_time = _now_iso()
        state_mod.add_pending(
            state, record_id, product_name, sales_notes,
            akeneo_identifier, message_id, sent_time
        )

        # Update Zoho status to show we are waiting
        try:
            zoho.update_record(record_id, {
                "Request_Status": "In progress",
            })
        except Exception as exc:
            log.warning(f"  Could not update Zoho record {record_id} status: {exc}")

        log.info(f"  Sent Telegram request for {record_id} (msg_id={message_id})")


def _extract_product_name(record: dict) -> str:
    for key in ("Product_Name", "Product_name", "product_name", "Name", "name"):
        val = record.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return ""


# ── Thread 2: Follow-up ──────────────────────────────────────────────────────

def followup_loop(state: dict, stop_event: threading.Event):
    log.info("Follow-up thread started.")
    while not stop_event.is_set():
        try:
            _followup_once(state)
        except Exception as exc:
            log.error(f"Follow-up error: {exc}", exc_info=True)
        stop_event.wait(60)   # check every minute
    log.info("Follow-up thread stopped.")


def _followup_once(state: dict):
    chat_id = state_mod.get_chat_id(state)
    if not chat_id:
        return

    _clear_invalid_pending_entries(state, "follow-up cleanup")

    now_iso  = _now_iso()
    to_resend = [
        (rid, entry)
        for rid, entry in list(state["pending"].items())
        if _seconds_since(entry.get("last_sent_time", entry.get("sent_time", now_iso)))
           >= config.FOLLOWUP_INTERVAL
    ]

    for record_id, entry in to_resend:
        log.info(f"Follow-up: resending request for record {record_id} (SKU: {entry.get('akeneo_identifier', 'N/A')})")
        new_msg_id = bot.resend_request(chat_id, record_id, entry)
        if new_msg_id:
            # Register the new message_id so replies to it are still matched
            state_mod.register_message(state, record_id, new_msg_id, _now_iso())
            log.info(f"  Follow-up sent (new msg_id={new_msg_id})")
        else:
            log.warning(f"  Follow-up send failed for {record_id}")


# ── Thread 3: Telegram long-poll ─────────────────────────────────────────────

def telegram_loop(state: dict, stop_event: threading.Event):
    log.info("Telegram polling thread started.")    
    while not stop_event.is_set():
        try:
            bot.poll_updates(state)
        except Exception as exc:
            log.error(f"Telegram poll error: {exc}", exc_info=True)
            time.sleep(5)
    log.info("Telegram polling thread stopped.")


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    state = state_mod.load()
    _clear_invalid_pending_entries(state, "startup cleanup")
    state_mod.save(state)
    log.info("=" * 60)
    log.info("  HC SPEC BOT STARTED")
    log.info(f"  Poll interval:    {config.POLL_INTERVAL}s")
    log.info(f"  Follow-up after:  {config.FOLLOWUP_INTERVAL}s")
    log.info(f"  Pending records:  {len(state.get('pending', {}))}")
    chat_id = state_mod.get_chat_id(state)
    if chat_id:
        log.info(f"  Telegram chat:    {chat_id}")
    else:
        log.warning("  Telegram chat_id NOT set — send /register to the bot!")
    log.info("=" * 60)

    stop_event = threading.Event()

    # Tell the bot module how to wake the poller (for /register)
    bot.set_poll_trigger(_poll_trigger)

    threads = [
        threading.Thread(target=poller_loop,   args=(state, stop_event), daemon=True, name="Poller"),
        threading.Thread(target=followup_loop, args=(state, stop_event), daemon=True, name="FollowUp"),
        threading.Thread(target=telegram_loop, args=(state, stop_event), daemon=True, name="Telegram"),
    ]

    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping (Ctrl+C)...")
        stop_event.set()
        for t in threads:
            t.join(timeout=5)
        log.info("HC SPEC Bot stopped.")
