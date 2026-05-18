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

# Test-mode flags. Set by run() at start-up.
#   _dry_run: log actions but skip Telegram sends and Zoho writes.
#   _run_once: do a single poll cycle, give Telegram a window to receive
#              replies, then exit (no follow-up loop).
_dry_run: bool = False
_run_once: bool = False


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
    for item_key, entry in list(state.get("pending", {}).items()):
        product_name = entry.get("product_name", "")
        akeneo_identifier = entry.get("akeneo_identifier", "")
        missing_fields = bot.missing_request_fields(product_name, akeneo_identifier)
        if not missing_fields:
            continue

        record_id = str(entry.get("record_id") or state_mod.parse_item_key(item_key)[0])
        state_mod.remove_pending(state, item_key)
        _mark_invalid_record(
            state,
            record_id,
            missing_fields,
            product_name,
            akeneo_identifier,
            context,
        )
        log.info(f"{context}: removed invalid pending entry {item_key}")


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
        if _dry_run:
            log.info(
                "[dry-run] No Telegram chat_id configured; continuing so the "
                "dry-run can still show which Zoho rows would be picked up."
            )
            chat_id = 0  # sentinel — we won’t actually send anything
        else:
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

        # Remarks_Notes2 is the "Sales Notes" field in the Zoho UI
        sales_notes  = str(record.get("Remarks_Notes2") or "").strip()
        request_type = str(record.get("Type_of_Request") or "").strip()
        current_status = str(record.get("Request_Status") or "").strip()

        # Skip records we have already processed for this request type, unless
        # they have been re-opened (Request_Status is back to Pending /
        # In progress). This is what stops "Supplier Actual Photo" records
        # from being re-sent on every poll once they're Done / NOT AVAILABLE.
        if state_mod.is_processed(state, record_id, request_type):
            if current_status in config.OPEN_STATUSES:
                log.info(
                    f"Record {record_id} previously processed but is now "
                    f"{current_status!r} again — re-processing."
                )
                state_mod.clear_processed(state, record_id)
            else:
                log.debug(
                    f"Skipping record {record_id} (already processed, "
                    f"status={current_status!r})."
                )
                continue

        subform_items = _extract_subform_items(record)
        if not subform_items:
            missing_fields = ["Product_Name1"]
            _mark_invalid_record(
                state,
                record_id,
                missing_fields,
                "",
                "",
                "poller",
            )
            continue

        log.info(
            f"New pending record: {record_id}  Type={request_type!r}  "
            f"Status={current_status!r}  Items={len(subform_items)}"
        )

        # The previous Remarks/Notes (e.g. stale Stage-1 trigger output,
        # prior supplier responses) must be cleared on first pickup so the
        # new outcome text is the only thing in the field. This applies to
        # both supplier-bound paths ("Supplier Actual Photo" and the
        # "Actual Photo" fall-through after Stage 1 found nothing). Gated
        # on "no items pending yet for this record" so we only wipe once
        # per record, not on every poll while we're waiting for Tony's
        # replies.
        if (
            request_type in (
                config.REQUEST_TYPE_SUPPLIER_ACTUAL_PHOTO,
                config.REQUEST_TYPE_ACTUAL_PHOTO,
            )
            and not state_mod.record_has_pending_items(state, record_id)
        ):
            if _dry_run:
                log.info(
                    f"  [dry-run] Would clear Remarks_Notes on Zoho record "
                    f"{record_id} ({request_type} first pickup)"
                )
            else:
                try:
                    zoho.update_record(record_id, {"Remarks_Notes": ""})
                    log.info(
                        f"  Cleared Remarks_Notes on record {record_id} "
                        f"({request_type} first pickup)"
                    )
                except Exception as exc:
                    log.warning(
                        f"  Could not clear Remarks_Notes on {record_id}: "
                        f"{exc} (continuing)"
                    )

        sent_any = False
        any_remaining = False

        for item in subform_items:
            row_id = item["row_id"]
            product_name = item["product_name"]
            sku_hint = item["sku"]
            item_key = state_mod.make_item_key(record_id, row_id)

            if state_mod.is_pending(state, item_key):
                # Already waiting for a reply on this specific subform item.
                any_remaining = True
                continue

            log.info(
                f"  Item row={row_id} Product={product_name!r} SKU={sku_hint!r}"
            )

            akeneo_identifier = sku_hint
            photo_bytes = None
            lookup_failed = False
            try:
                ident, _, has_actual, photo_bytes = akeneo.lookup_product(
                    product_name, identifier_hint=sku_hint,
                )
                if ident:
                    akeneo_identifier = ident
                # The user requested to NOT skip even if Akeneo already has an actual photo.
                if has_actual:
                    log.info(
                        f"    Akeneo already has actual photo for "
                        f"'{product_name}' (SKU={akeneo_identifier!r}), but "
                        f"we will NOT skip requesting it as per user configuration."
                    )
            except Exception as exc:
                lookup_failed = True
                log.warning(
                    f"    Akeneo lookup failed for '{product_name}' "
                    f"(SKU={sku_hint!r}): {exc}"
                )

            missing_fields = bot.missing_request_fields(product_name, akeneo_identifier)
            if missing_fields:
                if lookup_failed and missing_fields == ["SKU"]:
                    log.warning(
                        f"    Will retry record {record_id} (row {row_id}) next "
                        f"poll because Akeneo lookup failed before resolving the SKU."
                    )
                    any_remaining = True
                    continue
                _mark_invalid_record(
                    state,
                    record_id,
                    missing_fields,
                    product_name,
                    akeneo_identifier,
                    f"poller (row {row_id})",
                )
                continue

            if _dry_run:
                log.info(
                    f"    [dry-run] Would send Telegram to chat_id={chat_id} "
                    f"for record {record_id} row {row_id} "
                    f"(Type={request_type!r}, Product={product_name!r}, "
                    f"SKU={akeneo_identifier!r}, catalog_photo="
                    f"{'yes' if photo_bytes else 'no'})"
                )
                continue

            message_id = bot.send_photo_request(
                chat_id, product_name, akeneo_identifier, sales_notes, photo_bytes,
                request_type=request_type,
            )
            if message_id is None:
                log.error(
                    f"    Failed to send Telegram message for record {record_id} "
                    f"(row {row_id}). Will retry next poll."
                )
                any_remaining = True
                continue

            sent_time = _now_iso()
            state_mod.add_pending(
                state, record_id, product_name, sales_notes,
                akeneo_identifier, message_id, sent_time,
                request_type=request_type,
                row_id=row_id,
            )
            sent_any = True
            any_remaining = True
            log.info(
                f"    Sent Telegram request for {record_id} row {row_id} "
                f"(msg_id={message_id})"
            )

        if sent_any:
            state_mod.clear_invalid_record(state, record_id)

        if _dry_run:
            if sent_any or any_remaining:
                log.info(
                    f"  [dry-run] Would set Request_Status='In progress' on "
                    f"Zoho record {record_id}"
                )
            continue

        if sent_any:
            # Update Zoho status once per record to show we are waiting.
            try:
                zoho.update_record(record_id, {
                    "Request_Status": config.STATUS_IN_PROGRESS,
                })
            except Exception as exc:
                log.warning(
                    f"  Could not update Zoho record {record_id} status: {exc}"
                )


def _extract_subform_items(record: dict) -> list[dict]:
    """Extract product items from the `Product_Name1` subform.

    Each subform row looks like:
        {
            "ID": "<row_id>",
            "Items": {"Item_Name": "<product name>", "ID": "..."},
            "SKU": "<sku>",
            "zc_display_value": "...",
        }

    Returns a list of {"row_id", "product_name", "sku"} dicts — always
    sourced from the subform. The top-level `Product_Name` field is
    intentionally ignored because in the current Zoho form it is empty
    by design.
    """
    rows = record.get("Product_Name1") or []
    if not isinstance(rows, list):
        return []

    items: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("ID") or "").strip()

        items_obj = row.get("Items") or {}
        product_name = ""
        if isinstance(items_obj, dict):
            for key in ("Item_Name", "zc_display_value", "display_value", "Name"):
                val = items_obj.get(key)
                if val and str(val).strip():
                    product_name = str(val).strip()
                    break
        if not product_name:
            display_val = row.get("zc_display_value") or row.get("display_value")
            if display_val:
                product_name = str(display_val).strip()

        sku = str(row.get("SKU") or "").strip()

        items.append({
            "row_id":       row_id,
            "product_name": product_name,
            "sku":          sku,
        })
    return items


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
        (key, entry)
        for key, entry in list(state["pending"].items())
        if _seconds_since(entry.get("last_sent_time", entry.get("sent_time", now_iso)))
           >= config.FOLLOWUP_INTERVAL
    ]

    for item_key, entry in to_resend:
        record_id = str(entry.get("record_id") or state_mod.parse_item_key(item_key)[0])
        log.info(
            f"Follow-up: resending request for record {record_id} "
            f"item_key={item_key} (SKU: {entry.get('akeneo_identifier', 'N/A')})"
        )
        new_msg_id = bot.resend_request(chat_id, record_id, entry)
        if new_msg_id:
            # Register the new message_id so replies to it are still matched
            state_mod.register_message(state, item_key, new_msg_id, _now_iso())
            log.info(f"  Follow-up sent (new msg_id={new_msg_id})")
        else:
            log.warning(f"  Follow-up send failed for {record_id} item_key={item_key}")


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

# How long the --once mode keeps the Telegram listener alive after a single
# poll so the supplier has time to reply. Override per-call via run().
ONCE_REPLY_WINDOW_SECONDS = 300


def run(dry_run: bool = False, once: bool = False,
        once_reply_window_seconds: int = ONCE_REPLY_WINDOW_SECONDS):
    """
    Start the bot.

    dry_run
        If True, log what would happen but skip Telegram sends and Zoho writes.
        Still hits Zoho (read-only) and Akeneo (read-only) to exercise the
        full query path.
    once
        If True, do a single poll cycle and then keep the Telegram listener
        alive for `once_reply_window_seconds` so the supplier has time to
        reply, then exit. No follow-up loop runs. Useful for end-to-end tests.
    """
    global _dry_run, _run_once
    _dry_run = dry_run
    _run_once = once

    state = state_mod.load()
    _clear_invalid_pending_entries(state, "startup cleanup")
    state_mod.save(state)
    log.info("=" * 60)
    log.info("  HC SPEC BOT STARTED")
    if dry_run:
        log.info("  Mode:             DRY-RUN (no Telegram sends, no Zoho writes)")
    if once:
        log.info(f"  Mode:             ONCE (single poll, exit after {once_reply_window_seconds}s)")
    log.info(f"  Poll interval:    {config.POLL_INTERVAL}s")
    log.info(f"  Follow-up after:  {config.FOLLOWUP_INTERVAL}s")
    log.info(f"  Pending records:  {len(state.get('pending', {}))}")
    chat_id = state_mod.get_chat_id(state)
    if chat_id:
        log.info(f"  Telegram chat:    {chat_id}")
    else:
        log.warning("  Telegram chat_id NOT set — send /register to the bot!")
    log.info("=" * 60)

    if once:
        _run_once_cycle(state, once_reply_window_seconds)
        return

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


def _run_once_cycle(state: dict, reply_window_seconds: int):
    """Run one poll, then briefly listen for Telegram replies, then exit."""
    _poll_once(state)

    if _dry_run:
        log.info("[dry-run] --once: skipping Telegram reply window.")
        return

    stop_event = threading.Event()
    bot.set_poll_trigger(_poll_trigger)

    telegram_thread = threading.Thread(
        target=telegram_loop, args=(state, stop_event), daemon=True, name="Telegram",
    )
    telegram_thread.start()

    log.info(
        f"--once: poll cycle complete. Listening for Telegram replies for "
        f"{reply_window_seconds}s before exiting (Ctrl+C to stop early)."
    )
    try:
        deadline = time.monotonic() + reply_window_seconds
        while time.monotonic() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping early (Ctrl+C)...")
    finally:
        stop_event.set()
        telegram_thread.join(timeout=5)
        log.info("--once: exiting.")
