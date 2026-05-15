"""
HC SPEC Bot — Configuration
"""

import logging
import os
import sys
from pathlib import Path


def _source_dir() -> Path:
    return Path(__file__).resolve().parent


def _bundle_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return _source_dir()


def _runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _source_dir()

# ── Zoho OAuth2 ─────────────────────────────────────────────────────────────
ZOHO_CLIENT_ID     = os.environ.get("ZOHO_CLIENT_ID",     "1000.1553ED62RZUMKKXBU11108F3R60XRM")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "765799a817efa11d1acfb8a199df0df7361a4d6e9b")
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN", "1000.9b25e59d1b1206c4dd3f3d2b0a9a2e65.42acb5cd9955dee26bfe0e8d99074753")
ZOHO_TOKEN_URL     = "https://accounts.zoho.com/oauth/v2/token"

# ── Zoho Creator ─────────────────────────────────────────────────────────────
ZOHO_CREATOR_BASE  = "https://creator.zoho.com/api/v2.1"
ZOHO_ACCOUNT       = "homecartel"
ZOHO_APP           = "crm"
ZOHO_REPORT        = "All_Encoding_Requests"

# ── Akeneo PIM ───────────────────────────────────────────────────────────────
AKENEO_HOST        = os.environ.get("AKENEO_HOST",     "http://54.255.135.132")
AKENEO_CLIENT_ID   = os.environ.get("AKENEO_CLIENT_ID","5_uedqn47uk2884owwc8wkokkkw0cw4kc84s0kss0gwsgcg8w4s")
AKENEO_SECRET      = os.environ.get("AKENEO_SECRET",   "2hzym4lyvxk48kccowokc8ockowgcw0kwg8ksoco88c4kok0og")
AKENEO_USERNAME    = os.environ.get("AKENEO_USERNAME", "api_connection_3885")
AKENEO_PASSWORD    = os.environ.get("AKENEO_PASSWORD", "376710dfe")

# Akeneo attribute that holds the "actual" supplier photo we are waiting for
AKENEO_ACTUAL_ATTR = "Actual_Photo"
# Akeneo attributes to use as catalog/reference photos (in priority order)
# These are tried in order until one has data
AKENEO_CATALOG_ATTRS = [
    "image",
    "another_picture",
    "another_picture_2",
    "another_picture_3",
    "another_picture_4",
    "another_picture_5",
    "another_picture_6",
]

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8766106835:AAHKF7uoEevrZBVhlla6cv9C_m_9_0L9uh0")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")   # set via env or /register
_allowed_ids_str   = os.environ.get("TELEGRAM_TONY_ID", "8595658832, 8624679677") # can be comma separated list of IDs
TELEGRAM_ALLOWED_IDS = [int(i.strip()) for i in _allowed_ids_str.split(",") if i.strip()]
TELEGRAM_ADMIN_ID  = int(os.environ.get("TELEGRAM_ADMIN_ID", "7787491984")) # admin for private DMs

# ── LM Studio (local LLM for translation) ────────────────────────────────────
LM_STUDIO_URL      = os.environ.get("LM_STUDIO_URL",   "http://10.5.0.2:1234")
LM_STUDIO_MODEL    = os.environ.get("LM_STUDIO_MODEL", "zai-org/glm-4.6v-flash")

# ── Timing ───────────────────────────────────────────────────────────────────
POLL_INTERVAL      = int(os.environ.get("POLL_INTERVAL",     "60"))    # 1 minute
FOLLOWUP_INTERVAL  = int(os.environ.get("FOLLOWUP_INTERVAL", "600"))   # 10 minutes

# ── State file ───────────────────────────────────────────────────────────────
BASE_DIR = os.fspath(_source_dir())
BUNDLE_DIR = os.fspath(_bundle_dir())
RUNTIME_DIR = os.fspath(_runtime_dir())
STATE_FILE = os.fspath(_runtime_dir() / "hcspec_state.json")
LOG_FILE = os.fspath(_runtime_dir() / "hcspec_bot.log")
PLACEHOLDER_IMAGE = os.fspath(_bundle_dir() / "actual_photo_not_available.png")

# ── Remarks text that signals this bot should act ────────────────────────────
# Matches the text written by the Stage-1 automation (Crm-Actual-Photo) when
# no photo is found in Zoho WorkDrive / Archive / Akeneo.
TRIGGER_TEXT = "Not available from Kanban Notes / Zoho Drive / Akeneo"

# ── Request types handled by this bot ────────────────────────────────────────
# "Actual Photo": handled only after Stage 1 (Crm-Actual-Photo) has tried
#                 and failed, i.e. it has written TRIGGER_TEXT into
#                 Remarks_Notes.
# "Supplier Actual Photo": handled immediately for any Pending / In progress
#                         row. Used when the requestor already knows the
#                         photo must come straight from the supplier, so
#                         Stage 1's WorkDrive/Archive search is skipped.
REQUEST_TYPE_ACTUAL_PHOTO          = "Actual Photo"
REQUEST_TYPE_SUPPLIER_ACTUAL_PHOTO = "Supplier Actual Photo"

# All request types this bot will poll for in Zoho.
SUPPORTED_REQUEST_TYPES = (
    REQUEST_TYPE_ACTUAL_PHOTO,
    REQUEST_TYPE_SUPPLIER_ACTUAL_PHOTO,
)

# Request types that bypass the Stage-1 Remarks_Notes trigger-text filter and
# go straight to the supplier.
DIRECT_TO_SUPPLIER_REQUEST_TYPES = (
    REQUEST_TYPE_SUPPLIER_ACTUAL_PHOTO,
)

# Request types where the bot ignores the Pending / In progress status filter
# and acts on any Request_Status. Used so sales can flip the dropdown to
# "Supplier Actual Photo" on an already-Done row and have the bot pick it up.
ANY_STATUS_REQUEST_TYPES = (
    REQUEST_TYPE_SUPPLIER_ACTUAL_PHOTO,
)

# ── Request_Status values used by this bot ───────────────────────────────────
# These match the dropdown options on the Zoho Creator "Encoding Request" form
# exactly (case-sensitive): Pending / In progress / Done / Not available / Other.
STATUS_DONE          = os.environ.get("STATUS_DONE",          "Done")
STATUS_IN_PROGRESS   = os.environ.get("STATUS_IN_PROGRESS",   "In progress")
STATUS_NOT_AVAILABLE = os.environ.get("STATUS_NOT_AVAILABLE", "Not available")
STATUS_PENDING       = os.environ.get("STATUS_PENDING",       "Pending")

# Open statuses — the bot picks these up for normal "Actual Photo" requests.
OPEN_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS)

# Remarks the bot writes when the supplier uploads a photo/video via Telegram.
REMARKS_AUTOMATED_FROM_SUPPLIER = os.environ.get(
    "REMARKS_AUTOMATED_FROM_SUPPLIER",
    "This is automated na uploaded from supplier",
)

# ── Zoho field names for upload destinations ─────────────────────────────────
# These are the API names (not the UI display names). They follow Zoho
# Creator's apostrophe convention (e.g. UI "Checker's Note" → API
# "Checker_s_Note"). They can be overridden via env vars if Zoho's API names
# differ from the defaults below.
#
#   Supplier_s_Actual_Photo  ← UI "Supplier's Actual Photo"
#   Internal_Actual_Photo    ← UI "Internal Actual Photo"
#   Internal_Actual_Video    ← UI "Internal Actual Video"
FIELD_SUPPLIER_ACTUAL_PHOTO = os.environ.get(
    "FIELD_SUPPLIER_ACTUAL_PHOTO", "Supplier_s_Actual_Photo"
)
FIELD_INTERNAL_ACTUAL_PHOTO = os.environ.get(
    "FIELD_INTERNAL_ACTUAL_PHOTO", "Internal_Actual_Photo"
)
FIELD_INTERNAL_ACTUAL_VIDEO = os.environ.get(
    "FIELD_INTERNAL_ACTUAL_VIDEO", "Internal_Actual_Video"
)
# Legacy fields that the bot used before "Internal & Supplier's Actual Photo"
# was added to the form. Kept as fallbacks so existing records still work.
FIELD_LEGACY_ACTUAL_PHOTO = os.environ.get("FIELD_LEGACY_ACTUAL_PHOTO", "Actual_Photo1")
FIELD_LEGACY_VIDEO        = os.environ.get("FIELD_LEGACY_VIDEO",        "Video")

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("hcspec_bot")
