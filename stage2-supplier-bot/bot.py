"""
Telegram bot — send requests to the group, handle replies.
Uses raw requests (no extra framework needed).
"""

import time
import requests

import akeneo
import config
import llm
import state as state_mod
import zoho
from config import log

TELEGRAM_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# Optional event to wake the poller immediately after /register
_poll_trigger = None

def set_poll_trigger(event):
    """Called by run.py to pass in the threading.Event that wakes the poller."""
    global _poll_trigger
    _poll_trigger = event


# ── Sending ───────────────────────────────────────────────────────────────────

def missing_request_fields(product_name: str, akeneo_identifier: str) -> list[str]:
    missing = []
    if not str(product_name or "").strip():
        missing.append("Name")
    if not str(akeneo_identifier or "").strip():
        missing.append("SKU")
    return missing


def send_admin_message(text: str):
    _send_text(config.TELEGRAM_ADMIN_ID, text)


def send_invalid_record_alert(record_id: str, missing_fields: list[str], product_name: str,
                              akeneo_identifier: str, context: str):
    lines = [
        f"Invalid actual-photo request ({context})",
        f"Zoho Record ID: {record_id}",
        f"Missing: {', '.join(missing_fields)}",
    ]
    if product_name:
        lines.append(f"Name: {product_name}")
    if akeneo_identifier:
        lines.append(f"SKU: {akeneo_identifier}")
    send_admin_message("\n".join(lines))


def send_photo_request(chat_id: int, product_name: str, akeneo_identifier: str,
                       sales_notes: str, photo_bytes: bytes | None,
                       request_type: str = "") -> int | None:
    """
    Send a catalog photo (or text-only if none) to the group.
    Returns the sent message_id on success, or None.

    The Telegram caption shown to the supplier is identical regardless of
    request_type (the underlying ask is the same: provide an actual photo).
    request_type is only used to tag the admin DM so the admin can see at a
    glance whether this row went through Stage 1 first or came in as a direct
    supplier request.
    """
    missing_fields = missing_request_fields(product_name, akeneo_identifier)
    if missing_fields:
        log.warning(f"Telegram send skipped: missing required fields {missing_fields}")
        return None

    caption = _build_caption(product_name, akeneo_identifier, sales_notes)

    if photo_bytes:
        resp = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("catalog.jpg", photo_bytes, "image/jpeg")},
            timeout=60,
        )
    else:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": caption},
            timeout=30,
        )

    if not resp.ok:
        log.error(f"Telegram send failed: {resp.status_code} {resp.text[:300]}")
        return None

    msg = resp.json().get("result", {})
    message_id = msg.get("message_id")
    
    # Send private DM to admin with the actual product name
    if message_id:
        type_label = f" — {request_type}" if request_type else ""
        admin_text = (
            f"📢 *New Request Sent to Group*{type_label}\n"
            f"SKU: {akeneo_identifier}\n"
            f"Name: {product_name}"
        )
        send_admin_message(admin_text)

    return message_id


def resend_request(chat_id: int, record_id: str, entry: dict) -> int | None:
    """Resend the same request as a follow-up."""
    product_name = entry.get("product_name", "")
    sales_notes  = entry.get("sales_notes", "")
    akeneo_identifier = entry.get("akeneo_identifier", "")

    missing_fields = missing_request_fields(product_name, akeneo_identifier)
    if missing_fields:
        log.warning(f"Telegram resend skipped for {record_id}: missing required fields {missing_fields}")
        return None

    photo_bytes = None
    try:
        photo_bytes, _ = akeneo.get_catalog_photo_bytes(product_name)
    except Exception as exc:
        log.warning(f"Could not fetch catalog photo for resend ('{product_name}'): {exc}")

    caption = _build_caption(product_name, akeneo_identifier, sales_notes, is_followup=True)

    if photo_bytes:
        resp = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("catalog.jpg", photo_bytes, "image/jpeg")},
            timeout=60,
        )
    else:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": caption},
            timeout=30,
        )

    if not resp.ok:
        log.error(f"Telegram resend failed: {resp.status_code} {resp.text[:300]}")
        return None

    return resp.json().get("result", {}).get("message_id")


def _build_caption(product_name: str, akeneo_identifier: str, sales_notes: str,
                   is_followup: bool = False) -> str:
    lines = []
    if is_followup:
        lines.extend(["Follow-up reminder:", ""])

    lines.append(f"Name: {product_name}")
    lines.append(f"SKU: {akeneo_identifier}")
    
    # Only include sales notes if it's an actual manual request, 
    # not the auto-generated "No matching photo/video found" text.
    if sales_notes and "No matching photo/video found" not in sales_notes:
        lines.append("")  # blank line
        lines.append(sales_notes)
        
    lines.append("")  # blank line separator
    lines.append("Can you please provide an actual photo for this item")
    return "\n".join(lines)


# ── Receiving (long-poll loop) ────────────────────────────────────────────────

def poll_updates(state: dict):
    """
    Single call to getUpdates. Processes all available updates.
    Updates state["telegram_update_offset"] in place (and saves).
    """
    offset = state.get("telegram_update_offset", 0)
    try:
        resp = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=40,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning(f"Telegram getUpdates error: {exc}")
        time.sleep(5)
        return

    updates = resp.json().get("result", [])
    for update in updates:
        update_id = update.get("update_id", 0)
        state["telegram_update_offset"] = update_id + 1
        state_mod.save(state)
        _handle_update(update, state)


def _handle_update(update: dict, state: dict):
    """Route a single Telegram update to the right handler."""
    message = update.get("message") or update.get("channel_post")
    if not message:
        return

    chat_id    = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    sender_id  = message.get("from", {}).get("id")
    text       = message.get("text", "")

    # /register command — anyone in the group can register the chat
    if text.strip().startswith("/register"):
        state_mod.set_chat_id(state, chat_id)
        _send_text(chat_id, f"Chat registered! ID: {chat_id}")
        log.info(f"Registered chat_id: {chat_id}")
        # Wake the poller immediately so it sends requests right away
        if _poll_trigger:
            _poll_trigger.set()
        return

    # Only process replies from allowed users
    if sender_id not in config.TELEGRAM_ALLOWED_IDS:
        log.info(f"Ignored reply from sender_id {sender_id}. Bot only processes user IDs {config.TELEGRAM_ALLOWED_IDS}.")
        return

    caption = message.get("caption", "").strip()
    msg_text = text.strip() or caption
    lower_text = msg_text.lower()
    
    if lower_text.startswith("done ") or lower_text.startswith("#done ") or lower_text.startswith("upload ") or lower_text.startswith("/upload "):
        sku_keyword = " ".join(msg_text.split(" ")[1:]).strip()
        if not sku_keyword:
            _send_text(chat_id, "Please provide an SKU after the command (e.g. 'done 10227P/11')")
            return
            
        if not message.get("photo") and not message.get("video") and not (
            message.get("document", {}).get("mime_type", "").startswith("video/")
        ):
            _send_text(chat_id, "Please attach a photo or video when using the manual upload command.")
            return

        _handle_manual_upload(message, sku_keyword, chat_id, state)
        return

    # Determine which pending item this is a reply to
    item_key = _find_record_for_reply(message, state)
    if not item_key:
        return   # Not a reply to a bot request — ignore

    entry = state["pending"].get(item_key)
    if not entry:
        return

    record_id = str(
        entry.get("record_id") or state_mod.parse_item_key(item_key)[0]
    )
    product_name      = entry.get("product_name", "")
    akeneo_identifier = entry.get("akeneo_identifier", "")
    log.info(
        f"Handling reply for record {record_id} item_key={item_key} "
        f"(Product: {product_name})"
    )

    # Photo reply
    if message.get("photo"):
        _handle_photo_reply(message, item_key, product_name, akeneo_identifier, chat_id, state)
        return

    # Video / document that is a video
    if message.get("video") or (
        message.get("document", {}).get("mime_type", "").startswith("video/")
    ):
        _handle_video_reply(message, item_key, product_name, akeneo_identifier, chat_id, state)
        return

    # Text reply
    if text:
        _handle_text_reply(text, item_key, product_name, akeneo_identifier, chat_id, state)
        return


def _find_record_for_reply(message: dict, state: dict) -> str | None:
    """
    Map an incoming message to the composite item-key of a pending entry.
    Priority: reply_to_message.message_id → caption/text SKU match.
    """
    reply_to = message.get("reply_to_message", {})
    if reply_to:
        replied_id = reply_to.get("message_id")
        key = state_mod.record_id_for_message(state, replied_id)
        if key:
            return key

    # Fallback: scan text/caption for a matching product name / SKU fragment
    msg_text = message.get("text") or message.get("caption") or ""
    msg_lower = msg_text.lower()
    for item_key, entry in state["pending"].items():
        name = entry.get("product_name", "")
        sku = entry.get("akeneo_identifier", "")
        # SKU match wins outright — it disambiguates between items in the
        # same subform that share a product-name prefix.
        if sku and sku.lower() in msg_lower:
            return item_key
        # Match on first word of product name (e.g. "Boden" from "Boden | Brass Marble Table Lamp")
        if name:
            short = name.split("|")[0].strip()
            if short and short.lower() in msg_lower:
                return item_key
            # Also check the first two words, and then the first word just in case
            if " " in short:
                parts = short.split()
                if len(parts) >= 2:
                    first_two = f"{parts[0]} {parts[1]}"
                    if first_two.lower() in msg_lower:
                        return item_key

                first_word = parts[0].strip()
                if first_word and first_word.lower() in msg_lower:
                    return item_key

    return None


def _handle_photo_reply(message: dict, item_key: str, product_name: str,
                        akeneo_identifier: str, chat_id: int, state: dict):
    """Download photo → upload to Zoho Supplier's Actual Photo + Akeneo → update status."""
    photos  = message["photo"]
    best    = max(photos, key=lambda p: p.get("width", 0) * p.get("height", 0))
    file_id = best["file_id"]

    record_id, _ = state_mod.parse_item_key(item_key)

    photo_bytes = _download_file(file_id)
    if not photo_bytes:
        log.error(f"Could not download photo for record {record_id}")
        return

    entry = state.get("pending", {}).get(item_key) or {}
    request_type = str(entry.get("request_type", "")).strip()
    if entry.get("record_id"):
        record_id = str(entry["record_id"])

    success_zoho   = False
    success_akeneo = False

    try:
        zoho.upload_file(
            record_id, config.FIELD_SUPPLIER_ACTUAL_PHOTO,
            photo_bytes, filename="actual_photo.jpg",
        )
        success_zoho = True
        log.info(
            f"Zoho: uploaded actual photo to {config.FIELD_SUPPLIER_ACTUAL_PHOTO} "
            f"for record {record_id}"
        )
    except Exception as exc:
        log.error(f"Zoho upload failed for {record_id}: {exc}")

    if akeneo_identifier:
        try:
            success_akeneo = akeneo.upload_actual_photo(akeneo_identifier, photo_bytes)
        except Exception as exc:
            log.error(f"Akeneo upload failed for {akeneo_identifier}: {exc}")

    notes = config.REMARKS_AUTOMATED_FROM_SUPPLIER
    if not success_zoho:
        notes += " (Zoho upload failed — manual check needed)"
    if akeneo_identifier and not success_akeneo:
        notes += " (Akeneo upload failed — manual check needed)"

    state_mod.remove_pending(state, item_key)
    _finalize_record_if_last_item(
        state, record_id, request_type,
        product_name=product_name,
        akeneo_identifier=akeneo_identifier,
        remarks=notes,
        final_status=config.STATUS_DONE,
    )
    _send_text(chat_id, "Thankyou tony! <3")
    _send_text(config.TELEGRAM_ADMIN_ID, f"✅ *Uploaded to CRM (Photo)*\nSKU: {akeneo_identifier}\nName: {product_name}")
    log.info(f"Photo reply handled for record {record_id} (item_key={item_key})")


def _handle_video_reply(message: dict, item_key: str, product_name: str,
                        akeneo_identifier: str, chat_id: int, state: dict):
    """Download video → upload to Zoho Video + Akeneo → update status."""
    video   = message.get("video") or message.get("document", {})
    file_id = video.get("file_id")
    if not file_id:
        return

    mime     = video.get("mime_type", "video/mp4")
    ext      = "." + mime.split("/")[-1] if "/" in mime else ".mp4"
    filename = f"video{ext}"

    record_id, _ = state_mod.parse_item_key(item_key)

    video_bytes = _download_file(file_id)
    if not video_bytes:
        log.error(f"Could not download video for record {record_id}")
        return

    entry = state.get("pending", {}).get(item_key) or {}
    request_type = str(entry.get("request_type", "")).strip()
    if entry.get("record_id"):
        record_id = str(entry["record_id"])

    success_zoho   = False

    try:
        zoho.upload_file(
            record_id, config.FIELD_SUPPLIER_ACTUAL_PHOTO,
            video_bytes, filename=filename,
        )
        success_zoho = True
        log.info(
            f"Zoho: uploaded supplier video to {config.FIELD_SUPPLIER_ACTUAL_PHOTO} "
            f"for record {record_id}"
        )
    except Exception as exc:
        log.error(f"Zoho video upload failed for {record_id}: {exc}")

    # Per user request, do not upload videos to Akeneo

    notes = config.REMARKS_AUTOMATED_FROM_SUPPLIER
    if not success_zoho:
        notes += " (Zoho upload failed)"

    state_mod.remove_pending(state, item_key)
    _finalize_record_if_last_item(
        state, record_id, request_type,
        product_name=product_name,
        akeneo_identifier=akeneo_identifier,
        remarks=notes,
        final_status=config.STATUS_DONE,
    )
    _send_text(chat_id, "Thankyou tony! <3")
    _send_text(config.TELEGRAM_ADMIN_ID, f"✅ *Uploaded to CRM (Video)*\nSKU: {akeneo_identifier}\nName: {product_name}")
    log.info(f"Video reply handled for record {record_id} (item_key={item_key})")


def _handle_text_reply(text: str, item_key: str, product_name: str,
                       akeneo_identifier: str, chat_id: int, state: dict):
    """Translate Tagalog text → check if it means 'not available' → act accordingly."""
    translated = llm.translate_to_english(text)

    # Check if the translated text means "actual photo not available"
    is_unavailable = llm.classify_unavailable(translated)

    record_id, _ = state_mod.parse_item_key(item_key)
    entry = state.get("pending", {}).get(item_key) or {}
    request_type = str(entry.get("request_type", "")).strip()
    if entry.get("record_id"):
        record_id = str(entry["record_id"])

    if is_unavailable:
        log.info(f"Supplier says NOT AVAILABLE for record {record_id}: {translated}")

        # Upload the "ACTUAL PHOTO NOT AVAILABLE" placeholder image into the
        # "Internal Actual Photo" field. This is what fills the row when the
        # supplier confirms they have no actual photo.
        placeholder = _load_placeholder_image()
        if placeholder:
            try:
                zoho.upload_file(
                    record_id, config.FIELD_INTERNAL_ACTUAL_PHOTO, placeholder,
                    filename="actual_photo_not_available.png",
                )
                log.info(
                    f"Zoho: uploaded NOT AVAILABLE placeholder to "
                    f"{config.FIELD_INTERNAL_ACTUAL_PHOTO} for record {record_id}"
                )
            except Exception as exc:
                log.error(f"Zoho placeholder upload failed for {record_id}: {exc}")

        state_mod.remove_pending(state, item_key)
        _finalize_record_if_last_item(
            state, record_id, request_type,
            product_name=product_name,
            akeneo_identifier=akeneo_identifier,
            remarks=f"Supplier confirmed: Actual photo not available. ({translated})",
            final_status=config.STATUS_NOT_AVAILABLE,
        )
        _send_text(chat_id,
                   f"Thank you Tony! Noted that '{product_name}' has NO ACTUAL PHOTO available. Placeholder uploaded.")
        _send_text(config.TELEGRAM_ADMIN_ID, f"❌ *No Actual Photo Available*\nSKU: {akeneo_identifier}\nName: {product_name}")
        return

    # Normal text reply — just record the translated response
    state_mod.remove_pending(state, item_key)
    _finalize_record_if_last_item(
        state, record_id, request_type,
        product_name=product_name,
        akeneo_identifier=akeneo_identifier,
        remarks=f"Supplier response: {translated}",
        final_status=config.STATUS_DONE,
    )
    _send_text(chat_id, f"Thank you Tony! Your response for '{product_name}' has been recorded:\n{translated}")
    _send_text(config.TELEGRAM_ADMIN_ID, f"📝 *Response Recorded*\nSKU: {akeneo_identifier}\nName: {product_name}\nResponse: {translated}")


def _finalize_record_if_last_item(
    state: dict,
    record_id: str,
    request_type: str,
    *,
    product_name: str,
    akeneo_identifier: str,
    remarks: str,
    final_status: str,
) -> None:
    """Mark a record Done / Not available only when no other subform items are still pending.

    For records with multiple `Product_Name1` subform items, the supplier
    will reply once per item; we don't want to flip `Request_Status` to
    Done until every item has been resolved.
    """
    if state_mod.record_has_pending_items(state, record_id):
        log.info(
            f"Record {record_id}: still has pending subform items — "
            f"leaving Request_Status as is (this item resolved)."
        )
        return

    try:
        zoho.update_record(record_id, {
            "Request_Status": final_status,
            "Remarks_Notes":  remarks,
        })
    except Exception as exc:
        log.error(f"Zoho update_record failed for {record_id}: {exc}")

    state_mod.mark_processed(
        state, record_id, request_type,
        final_status=final_status,
        product_name=product_name,
        akeneo_identifier=akeneo_identifier,
    )


def _load_placeholder_image() -> bytes | None:
    """Load the 'ACTUAL PHOTO NOT AVAILABLE' placeholder from disk."""
    placeholder_path = config.PLACEHOLDER_IMAGE
    try:
        with open(placeholder_path, "rb") as f:
            return f.read()
    except Exception as exc:
        log.error(f"Could not load placeholder image: {exc}")
        return None


def _download_file(file_id: str) -> bytes | None:
    """Download a file from Telegram by file_id."""
    try:
        info_resp = requests.get(
            f"{TELEGRAM_API}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        info_resp.raise_for_status()
        file_path = info_resp.json()["result"]["file_path"]
        dl_resp = requests.get(
            f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{file_path}",
            timeout=120,
        )
        dl_resp.raise_for_status()
        return dl_resp.content
    except Exception as exc:
        log.error(f"_download_file({file_id}) failed: {exc}")
        return None


def _send_text(chat_id: int, text: str):
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except Exception as exc:
        log.warning(f"_send_text failed: {exc}")


def _handle_manual_upload(message: dict, sku_keyword: str, chat_id: int, state: dict):
    """Handle standalone photo/video uploaded to Telegram mapped to an SKU."""
    _send_text(chat_id, f"🔍 Looking up product matching '{sku_keyword}' in Akeneo...")
    
    akeneo_identifier, product, _, _ = akeneo.lookup_product(sku_keyword)
    
    if not product:
        _send_text(chat_id, f"❌ Could not find product matching '{sku_keyword}' in Akeneo.")
        return
        
    product_name = akeneo_identifier
    name_vals = product.get("values", {}).get("name", [])
    if name_vals:
        product_name = name_vals[0].get("data", "")
        
    _send_text(chat_id, f"🔍 Found product: {product_name}. Searching Zoho CRM...")
    
    record = zoho.search_record_by_product_name(product_name)
    if not record:
        # Fallback to searching by original keyword
        record = zoho.search_record_by_product_name(sku_keyword)
    
    if not record:
        _send_text(chat_id, f"❌ Could not find a pending/in-progress Zoho record matching '{product_name}' or '{sku_keyword}'.")
        return
        
    record_id = str(record.get("ID"))
    _send_text(chat_id, f"✅ Found Zoho Record {record_id}. Uploading files...")
    
    success_zoho = False
    success_akeneo = False
    
    if message.get("photo"):
        photos  = message["photo"]
        best    = max(photos, key=lambda p: p.get("width", 0) * p.get("height", 0))
        file_id = best["file_id"]
        
        photo_bytes = _download_file(file_id)
        if not photo_bytes:
            _send_text(chat_id, "❌ Failed to download photo from Telegram.")
            return
            
        try:
            zoho.upload_file(
                record_id, config.FIELD_SUPPLIER_ACTUAL_PHOTO,
                photo_bytes, filename="actual_photo.jpg",
            )
            success_zoho = True
        except Exception as exc:
            log.error(f"Zoho manual upload failed for {record_id}: {exc}")
            
        if akeneo_identifier:
            try:
                success_akeneo = akeneo.upload_actual_photo(akeneo_identifier, photo_bytes)
            except Exception as exc:
                log.error(f"Akeneo manual upload failed for {akeneo_identifier}: {exc}")
                
    elif message.get("video") or message.get("document", {}).get("mime_type", "").startswith("video/"):
        video   = message.get("video") or message.get("document", {})
        file_id = video.get("file_id")
        mime     = video.get("mime_type", "video/mp4")
        ext      = "." + mime.split("/")[-1] if "/" in mime else ".mp4"
        filename = f"video{ext}"
        
        video_bytes = _download_file(file_id)
        if not video_bytes:
            _send_text(chat_id, "❌ Failed to download video from Telegram.")
            return
            
        try:
            zoho.upload_file(
                record_id, config.FIELD_SUPPLIER_ACTUAL_PHOTO,
                video_bytes, filename=filename,
            )
            success_zoho = True
        except Exception as exc:
            log.error(f"Zoho video manual upload failed for {record_id}: {exc}")
            
        if akeneo_identifier:
            try:
                success_akeneo = akeneo.upload_video(akeneo_identifier, video_bytes, filename=filename)
            except Exception as exc:
                log.error(f"Akeneo video manual upload failed for {akeneo_identifier}: {exc}")
                
    notes = "Uploaded manually via Telegram bot command."
    if not success_zoho: notes += " (Zoho Upload Failed)"
    if akeneo_identifier and not success_akeneo: notes += " (Akeneo Upload Failed)"

    try:
        zoho.update_record(record_id, {
            "Request_Status": config.STATUS_DONE,
            "Remarks_Notes": notes,
        })
    except Exception as exc:
        log.error(f"Zoho update_record manual failed for {record_id}: {exc}")

    request_type = str(record.get("Type_of_Request", "")).strip()
    matching_keys = [
        key
        for key, entry in state.get("pending", {}).items()
        if str(entry.get("record_id") or state_mod.parse_item_key(key)[0]) == record_id
        and (
            not akeneo_identifier
            or str(entry.get("akeneo_identifier", "")).lower() == akeneo_identifier.lower()
        )
    ]
    if not matching_keys and record_id in state.get("pending", {}):
        # Fallback — legacy non-subform pending entry keyed by bare record_id.
        matching_keys = [record_id]
    for key in matching_keys:
        state_mod.remove_pending(state, key)
    state_mod.mark_processed(
        state, record_id, request_type,
        final_status=config.STATUS_DONE,
        product_name=product_name,
        akeneo_identifier=akeneo_identifier,
    )

    _send_text(chat_id, f"🎉 Done uploading actual photo/video for '{product_name}' and marked as Done in Zoho.")
