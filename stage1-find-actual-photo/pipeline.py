# =====================================================================
#  PIPELINE
#  Picks one unfinished Airtable row and drives it through all phases:
#    Phase 0  →  Generate Prompt (local Vision LLM)
#    Phase 1  →  Styled Photo   (Kie.ai text-to-image)
#    Phase 2  →  Blend          (Styled Photo + Furniture Items)
#    Phase 3  →  Moodboard      (from Blended Image)
#    Phase 4  →  Before Reels   (video from Styled Photo)
#    Phase 5  →  After Reels    (video from Blended Image)
#    Phase 6  →  Combined Reels (Before + After stitched)
# =====================================================================

from config.prompts import MOODBOARD_PROMPT, BEFORE_REELS_PROMPT, AFTER_REELS_PROMPT

from services.airtable import (
    get_next_unfinished_row,
    refetch_record,
    update_status,
    update_field,
    update_attachment,
)
from services.kie import create_image_task, create_blend_task, create_video_task, poll_task_status
from services.zoho import upload_from_url, upload_and_get_public_link
from services.vision_llm import get_random_local_photo, generate_prompt
from services.video import download, combine, cleanup_temp_files


# ── Phase Runners ───────────────────────────────────────────────────
# Each phase returns the new status string so the pipeline can flow.

def _phase0_prompt(table_id: str, record_id: str, fields: dict, ui_callback=None) -> str | None:
    """Phase 0: Generate a styled-photo prompt via the local Vision LLM."""
    prompt = fields.get("Styled Photo Prompt", "")
    photo_path = None
    generated = None

    if not prompt:
        update_status(table_id, record_id, "Processing Adding a Prompt")
        print("[INFO] Generating prompt from local photo...")

        photo_path = get_random_local_photo()
        if not photo_path:
            print("[ERROR] No local photos found. Reverting to Standby.")
            update_status(table_id, record_id, "Standby")
            return None

        # ── Show the photo in UI with "AI scanning" indicator ──
        if ui_callback:
            ui_callback("🔍 Phase 0: AI Scanning Photo...",
                        desc_text="Analyzing reference photo with Vision LLM. Please wait...",
                        image_path=photo_path,
                        scanning=True)

        generated = generate_prompt(photo_path)
        if not generated:
            print("[ERROR] LLM prompt generation failed. Reverting to Standby.")
            update_status(table_id, record_id, "Standby")
            if ui_callback:
                ui_callback("❌ Phase 0: Prompt Generation Failed",
                            desc_text="The Vision LLM could not generate a prompt for this photo.")
            return None

        update_field(table_id, record_id, "Styled Photo Prompt", generated)

        # ── Show generated prompt result in UI ──
        if ui_callback:
            ui_callback("✅ Phase 0: Prompt Generated!",
                        desc_text=generated,
                        image_path=photo_path,
                        scanning=False)
    else:
        print("[INFO] Row already has a prompt. Skipping generation.")
        if ui_callback:
            ui_callback("✅ Phase 0: Prompt Already Exists",
                        desc_text=prompt)

    update_status(table_id, record_id, "Complete Adding a Prompt")
    print("[OK] Phase 0 (Prompt) done.")

    return "Complete Adding a Prompt"


def _phase1_styled_photo(table_id: str, record_id: str, ui_callback=None, stop_event=None) -> str | None:
    """Phase 1: Generate a styled photo from the prompt via Kie.ai."""
    fields = refetch_record(table_id, record_id)
    prompt = fields.get("Styled Photo Prompt", "")

    if not prompt:
        update_status(table_id, record_id, "Error - No Prompt")
        return None

    update_status(table_id, record_id, "Processing")
    print("[INFO] Generating Styled Photo...")
    if ui_callback:
        ui_callback("⏳ Phase 1: Generating Styled Photo...", desc_text=f"Sending prompt to Kie.ai: {prompt[:80]}...")

    task_id = create_image_task(prompt)
    if not task_id:
        update_status(table_id, record_id, "Error - Styled Photo Task Failed")
        return None

    image_url = poll_task_status(task_id, stop_event=stop_event)
    if not image_url:
        if stop_event and stop_event.is_set():
            print("[INFO] Phase 1 cancelled.")
            return None
        update_status(table_id, record_id, "Error - Styled Photo Generation Failed")
        return None

    update_attachment(table_id, record_id, "Styled Photo", image_url)
    saved_path = f"{record_id}_styled_photo.png"
    upload_from_url(image_url, saved_path, "Styled Photo")
    print("[OK] Phase 1 (Styled Photo) done.")
    
    if ui_callback:
        # Assuming upload_from_url saves it locally first or we need to download it for UI.
        # Temp downlaod for UI since upload_from_url handles Zoho, we'll use requests quickly.
        import requests, os
        import tempfile
        tmp_img = os.path.join(tempfile.gettempdir(), saved_path)
        try:
             with open(tmp_img, 'wb') as f:
                 f.write(requests.get(image_url).content)
             ui_callback("Phase 1: Styled Photo", image_path=tmp_img)
        except:
             pass

    return "Processing"


def _phase2_blend(table_id: str, record_id: str, blend_prompt: str, ui_callback=None, stop_event=None) -> None:
    """Phase 2: Blend the styled photo with furniture items."""
    fields = refetch_record(table_id, record_id)
    styled = fields.get("Styled Photo")
    furn1 = fields.get("Furniture Item")
    furn2 = fields.get("Furniture Item2")
    blended = fields.get("Blended Image")

    if blended:
        print("[INFO] Blended Image already exists. Skipping.")
        return
    if not (styled and furn1 and furn2):
        print("[WARN] Missing Styled Photo or Furniture Items. Skipping blend.")
        return

    print("[INFO] Blending images...")
    image_urls = [styled[0]["url"], furn1[0]["url"], furn2[0]["url"]]
    if ui_callback:
        ui_callback("⏳ Phase 2: Blending Images...", desc_text="Merging Styled Photo + 2 Furniture Items via Kie.ai...")

    task_id = create_blend_task(image_urls, blend_prompt)
    if not task_id:
        update_status(table_id, record_id, "Error - Blend Task Failed")
        return

    blended_url = poll_task_status(task_id, stop_event=stop_event)
    if not blended_url:
        if stop_event and stop_event.is_set():
            print("[INFO] Phase 2 cancelled.")
            return
        update_status(table_id, record_id, "Error - Blend Generation Failed")
        return

    update_attachment(table_id, record_id, "Blended Image", blended_url)
    saved_path = f"{record_id}_blended.png"
    upload_from_url(blended_url, saved_path, "Blended Image")
    print("[OK] Phase 2 (Blend) done.")
    
    if ui_callback:
        import requests, os
        import tempfile
        tmp_img = os.path.join(tempfile.gettempdir(), saved_path)
        try:
             with open(tmp_img, 'wb') as f:
                 f.write(requests.get(blended_url).content)
             ui_callback("Phase 2: Blended Image", image_path=tmp_img)
        except:
             pass


def _phase3_moodboard(table_id: str, record_id: str, ui_callback=None, stop_event=None) -> None:
    """Phase 3: Generate a moodboard from the blended image."""
    fields = refetch_record(table_id, record_id)
    blended = fields.get("Blended Image")
    moodboard = fields.get("Moodboard Image")

    if moodboard:
        print("[INFO] Moodboard already exists. Skipping.")
        return
    if not blended:
        print("[WARN] No Blended Image found. Cannot generate Moodboard.")
        return

    print("[INFO] Generating Moodboard...")
    blended_url = blended[0]["url"]
    if ui_callback:
        ui_callback("⏳ Phase 3: Generating Moodboard...", desc_text="Creating moodboard layout from blended image...")

    task_id = create_blend_task([blended_url], MOODBOARD_PROMPT)
    if not task_id:
        update_status(table_id, record_id, "Error - Moodboard Task Failed")
        return

    moodboard_url = poll_task_status(task_id, stop_event=stop_event)
    if not moodboard_url:
        if stop_event and stop_event.is_set():
            print("[INFO] Phase 3 cancelled.")
            return
        update_status(table_id, record_id, "Error - Moodboard Generation Failed")
        return

    update_attachment(table_id, record_id, "Moodboard Image", moodboard_url)
    saved_path = f"{record_id}_moodboard.png"
    upload_from_url(moodboard_url, saved_path, "Moodboard")
    print("[OK] Phase 3 (Moodboard) done.")
    
    if ui_callback:
        import requests, os
        import tempfile
        tmp_img = os.path.join(tempfile.gettempdir(), saved_path)
        try:
             with open(tmp_img, 'wb') as f:
                 f.write(requests.get(moodboard_url).content)
             ui_callback("Phase 3: Moodboard", image_path=tmp_img)
        except:
             pass


def _phase_video(table_id: str, record_id: str,
                 source_field: str, target_field: str,
                 prompt: str, zoho_folder: str, label: str, ui_callback=None, stop_event=None) -> None:
    """Generic video-from-image phase (used for Before & After Reels)."""
    fields = refetch_record(table_id, record_id)
    source = fields.get(source_field)
    existing = fields.get(target_field)

    if existing:
        print(f"[INFO] {label} already exists. Skipping.")
        return
    if not source:
        return

    print(f"[INFO] Generating {label} video...")
    source_url = source[0]["url"]
    if ui_callback:
        ui_callback(f"⏳ Phase Video: Generating {label}...", desc_text=f"Creating video from {source_field}...")

    task_id = create_video_task(source_url, prompt)
    if not task_id:
        update_status(table_id, record_id, f"Error - {label} Task Failed")
        return

    video_url = poll_task_status(task_id, stop_event=stop_event)
    if not video_url:
        if stop_event and stop_event.is_set():
            print(f"[INFO] {label} cancelled.")
            return
        update_status(table_id, record_id, f"Error - {label} Generation Failed")
        return

    update_attachment(table_id, record_id, target_field, video_url)
    upload_from_url(video_url, f"{record_id}_{label.lower().replace(' ', '_')}.mp4", zoho_folder)
    print(f"[OK] {label} done.")
    
    if ui_callback:
        ui_callback(f"Phase Video: {label} Built", desc_text=f"Video generated successfully for {label}.")


def _phase6_combine_reels(table_id: str, record_id: str, ui_callback=None) -> None:
    """Phase 6: Stitch Before + After Reels into one video."""
    fields = refetch_record(table_id, record_id)
    before = fields.get("Before Reels")
    after = fields.get("After Reels")
    combined = fields.get("Combine Before and After Reels")

    if combined:
        print("[INFO] Combined Reels already exists. Skipping.")
        return
    if not (before and after):
        return

    print("[INFO] Combining Before + After Reels...")
    if ui_callback:
        ui_callback("⏳ Phase 6: Combining Reels...", desc_text="Stitching Before + After videos together...")
    before_path = download(before[0]["url"], f"{record_id}_before.mp4")
    after_path = download(after[0]["url"], f"{record_id}_after.mp4")

    if not (before_path and after_path):
        print("[WARN] Could not download one or both videos. Skipping.")
        cleanup_temp_files(before_path, after_path)
        return

    combined_path = combine(before_path, after_path, f"{record_id}_combined.mp4")
    if not combined_path:
        print("[WARN] Video combination failed. Skipping.")
        cleanup_temp_files(before_path, after_path)
        return

    combined_url = upload_and_get_public_link(combined_path)
    if combined_url:
        update_attachment(table_id, record_id, "Combine Before and After Reels", combined_url)
        print("[OK] Phase 6 (Combined Reels) done.")
        if ui_callback:
            ui_callback("Phase 6: Combined Reels Compiled", desc_text="Final stitched reels video has been uploaded.")
    else:
        print("[WARN] Could not upload combined video. Skipping.")

    cleanup_temp_files(before_path, after_path, combined_path)


# ── Main Orchestrator ───────────────────────────────────────────────

def process_one_row(table_id: str, blend_prompt: str, ui_callback=None, stop_event=None) -> bool:
    """
    Picks the next unfinished row and runs all phases sequentially.
    Returns True if a row was processed, False if no work was found.
    """
    record_id, fields = get_next_unfinished_row(table_id)
    if not record_id:
        return False

    status = fields.get("Status", "")
    print(f"\n[ROW] Processing {record_id} (status: {status})")
    if ui_callback:
        ui_callback(f"📌 Record Found: {record_id}", desc_text=f"Current status: {status}. Starting pipeline...")

    # Phase 0: Prompt Generation
    if status == "Standby":
        status = _phase0_prompt(table_id, record_id, fields, ui_callback)
        if not status:
            return True  # row was touched but errored

    if stop_event and stop_event.is_set():
        print("[INFO] Stop requested. Halting pipeline.")
        return True

    # Phase 1: Styled Photo
    if status == "Complete Adding a Prompt":
        status = _phase1_styled_photo(table_id, record_id, ui_callback, stop_event=stop_event)
        if not status:
            return True

    if stop_event and stop_event.is_set():
        print("[INFO] Stop requested. Halting pipeline.")
        return True

    # Phases 2–6 only run when status is "Processing"
    if status == "Processing":
        if ui_callback:
            ui_callback("Processing Video & Blends", desc_text="Starting the blend & video rendering phases...")

        _phase2_blend(table_id, record_id, blend_prompt, ui_callback, stop_event=stop_event)
        if stop_event and stop_event.is_set():
            print("[INFO] Stop requested. Halting pipeline.")
            return True

        _phase3_moodboard(table_id, record_id, ui_callback, stop_event=stop_event)
        if stop_event and stop_event.is_set():
            print("[INFO] Stop requested. Halting pipeline.")
            return True

        _phase_video(
            table_id, record_id,
            source_field="Styled Photo", target_field="Before Reels",
            prompt=BEFORE_REELS_PROMPT, zoho_folder="Before Reels",
            label="Before Reels",
            ui_callback=ui_callback, stop_event=stop_event
        )
        if stop_event and stop_event.is_set():
            print("[INFO] Stop requested. Halting pipeline.")
            return True

        _phase_video(
            table_id, record_id,
            source_field="Blended Image", target_field="After Reels",
            prompt=AFTER_REELS_PROMPT, zoho_folder="After Reels",
            label="After Reels",
            ui_callback=ui_callback, stop_event=stop_event
        )
        if stop_event and stop_event.is_set():
            print("[INFO] Stop requested. Halting pipeline.")
            return True

        _phase6_combine_reels(table_id, record_id, ui_callback)

    if stop_event and stop_event.is_set():
        print("[INFO] Stop requested. Skipping completion mark.")
        return True

    # Mark complete
    update_status(table_id, record_id, "Complete")
    print(f"[DONE] Record {record_id} fully completed!")
    return True
