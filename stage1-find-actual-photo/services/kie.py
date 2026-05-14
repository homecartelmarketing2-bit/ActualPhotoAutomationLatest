# =====================================================================
#  KIE.AI SERVICE
#  Handles image generation, blending, video creation, and task polling.
# =====================================================================

import json
import time
import requests

from config.settings import (
    KIE_API_KEY,
    KIE_CREATE_TASK_URL,
    KIE_QUERY_TASK_URL,
    IMAGE_MODEL,
    IMAGE_ASPECT_RATIO,
    IMAGE_RESOLUTION,
    IMAGE_FORMAT,
    VIDEO_MODEL,
    VIDEO_DURATION,
    VIDEO_NEGATIVE_PROMPT,
    VIDEO_CFG_SCALE,
    POLL_INTERVAL,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {KIE_API_KEY}",
    }


def _create_task(payload: dict) -> str | None:
    """Posts a task to Kie.ai and returns the taskId or None."""
    try:
        resp = requests.post(KIE_CREATE_TASK_URL, headers=_headers(), json=payload)
        data = resp.json()
        if data.get("code") == 200:
            return data["data"]["taskId"]
        print(f"[ERROR] Kie.ai: {data.get('message')}")
    except requests.RequestException as e:
        print(f"[ERROR] Creating Kie.ai task: {e}")
    return None


# ── Task Creators ───────────────────────────────────────────────────

def create_image_task(prompt: str) -> str | None:
    """Creates a text-to-image task and returns the taskId."""
    return _create_task({
        "model": IMAGE_MODEL,
        "input": {
            "prompt": prompt,
            "aspect_ratio": IMAGE_ASPECT_RATIO,
            "resolution": IMAGE_RESOLUTION,
            "output_format": IMAGE_FORMAT,
        },
    })


def create_blend_task(image_urls: list[str], prompt: str) -> str | None:
    """Creates an image-blend task and returns the taskId."""
    return _create_task({
        "model": IMAGE_MODEL,
        "input": {
            "prompt": prompt,
            "aspect_ratio": IMAGE_ASPECT_RATIO,
            "resolution": IMAGE_RESOLUTION,
            "output_format": IMAGE_FORMAT,
            "image_input": image_urls,
        },
    })


def create_video_task(image_url: str, prompt: str) -> str | None:
    """Creates an image-to-video task (Kling v2.5 Turbo) and returns the taskId."""
    return _create_task({
        "model": VIDEO_MODEL,
        "input": {
            "prompt": prompt,
            "image_url": image_url,
            "duration": VIDEO_DURATION,
            "negative_prompt": VIDEO_NEGATIVE_PROMPT,
            "cfg_scale": VIDEO_CFG_SCALE,
        },
    })


# ── Polling ─────────────────────────────────────────────────────────

def poll_task_status(task_id: str, stop_event=None) -> str | None:
    """
    Polls until the task succeeds, fails, or stop_event is set.
    Returns the first result URL on success, or None on failure/cancellation.
    """
    print(f"[WAIT] Polling task {task_id[:12]}... (no time limit)")

    while not (stop_event and stop_event.is_set()):
        try:
            resp = requests.get(
                KIE_QUERY_TASK_URL,
                headers=_headers(),
                params={"taskId": task_id},
            )
            data = resp.json()

            if data.get("code") != 200:
                return None

            state = data["data"].get("state", "")

            if state == "success":
                result = data["data"].get("resultJson", {})
                if isinstance(result, str):
                    result = json.loads(result)
                urls = result.get("resultUrls", [])
                return urls[0] if urls else None

            if state == "failed":
                print(f"[ERROR] Task failed: {data['data'].get('failMsg', 'Unknown')}")
                return None

        except requests.RequestException:
            pass

        if stop_event:
            stop_event.wait(POLL_INTERVAL)
        else:
            time.sleep(POLL_INTERVAL)

    print("[INFO] Polling cancelled by stop request.")
    return None