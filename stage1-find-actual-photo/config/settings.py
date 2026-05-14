# =====================================================================
#  API KEYS & ENDPOINTS
#  All secrets are loaded from the .env file in the project root.
#  Copy .env.example → .env and fill in your values.
# =====================================================================

import os
import sys
from dotenv import load_dotenv

# Find .env relative to the exe (PyInstaller) or the project root (dev)
if getattr(sys, 'frozen', False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(_base_dir, '.env'))

# ── Kie.ai ──────────────────────────────────────────────────────────
KIE_API_KEY = os.getenv("KIE_API_KEY", "")
KIE_CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
KIE_QUERY_TASK_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"

# ── Airtable ────────────────────────────────────────────────────────
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "")

# ── Zoho WorkDrive ──────────────────────────────────────────────────
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "")
ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_UPLOAD_URL = "https://workdrive.zoho.com/api/v1/upload"

ZOHO_FOLDERS = {
    "Styled Photo": os.getenv("ZOHO_FOLDER_STYLED_PHOTO", ""),
    "Blended Image": os.getenv("ZOHO_FOLDER_BLENDED_IMAGE", ""),
    "Moodboard": os.getenv("ZOHO_FOLDER_MOODBOARD", ""),
    "Styled Reels": os.getenv("ZOHO_FOLDER_STYLED_REELS", ""),
    "Before and After Reels": os.getenv("ZOHO_FOLDER_BEFORE_AND_AFTER_REELS", ""),
    "Before and After Feeds": os.getenv("ZOHO_FOLDER_BEFORE_AND_AFTER_FEEDS", ""),
    "Before Reels": os.getenv("ZOHO_FOLDER_BEFORE_REELS", ""),
    "After Reels": os.getenv("ZOHO_FOLDER_AFTER_REELS", ""),
}

# ── Vision LLM (local LM Studio) ───────────────────────────────────
VISION_LLM_MODEL = os.getenv("VISION_LLM_MODEL", "zai-org/glm-4.6v-flash")
VISION_LLM_ENDPOINTS = os.getenv("VISION_LLM_ENDPOINTS", "").split(",")
VISION_LLM_TIMEOUT = int(os.getenv("VISION_LLM_TIMEOUT", "120"))

# ── Local Photo Directory ───────────────────────────────────────────
LOCAL_PHOTO_DIR = os.getenv("LOCAL_PHOTO_DIR", r"C:\Users\User\Downloads\photos")

# =====================================================================
#  MODEL & GENERATION SETTINGS
# =====================================================================

# ── Image Generation (Kie.ai) ──────────────────────────────────────
IMAGE_MODEL = "nano-banana-pro"
IMAGE_ASPECT_RATIO = "3:4"
IMAGE_RESOLUTION = "2K"
IMAGE_FORMAT = "png"

# ── Video Generation (Kling v2.5 Turbo via Kie.ai) ─────────────────
VIDEO_MODEL = "kling/v2-5-turbo-image-to-video-pro"
VIDEO_DURATION = "5"
VIDEO_NEGATIVE_PROMPT = "blur, distort, and low quality"
VIDEO_CFG_SCALE = 0.5

# ── Polling ─────────────────────────────────────────────────────────
POLL_INTERVAL = 5  # seconds between status checks (no limit — polls until done)

# ── Main Loop ───────────────────────────────────────────────────────
IDLE_WAIT_SECONDS = 30   # wait time when all tables are clear
