from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = PROJECT_ROOT / "processed_records.json"

CONFIG: dict[str, Any] = {
    "zoho_client_id": os.getenv(
        "ZOHO_CLIENT_ID", "1000.1553ED62RZUMKKXBU11108F3R60XRM"
    ),
    "zoho_client_secret": os.getenv(
        "ZOHO_CLIENT_SECRET", "765799a817efa11d1acfb8a199df0df7361a4d6e9b"
    ),
    "zoho_refresh_token": os.getenv(
        "ZOHO_REFRESH_TOKEN",
        "1000.bf82f4c64bc0df59e00e1fc98c4136aa.866000179c497d0b201c53f9c94672a6",
    ),
    "zoho_token_url": os.getenv(
        "ZOHO_TOKEN_URL", "https://accounts.zoho.com/oauth/v2/token"
    ),
    "zoho_creator_base_url": os.getenv(
        "ZOHO_CREATOR_BASE_URL", "https://www.zohoapis.com/creator/v2.1"
    ),
    "zoho_creator_account": os.getenv("ZOHO_CREATOR_ACCOUNT", "homecartel"),
    "crm_app": os.getenv("ZOHO_CRM_APP", "crm"),
    "encoding_report": os.getenv("ZOHO_ENCODING_REPORT", "All_Encoding_Requests"),
    "encoding_form": os.getenv("ZOHO_ENCODING_FORM", ""),
    "workflow_app": os.getenv("ZOHO_WORKFLOW_APP", "workflow-module-creation"),
    "archive_report": os.getenv("ZOHO_ARCHIVE_REPORT", "Archive_Report"),
    "workdrive_base_url": os.getenv(
        "ZOHO_WORKDRIVE_BASE_URL", "https://www.zohoapis.com/workdrive/api/v1"
    ),
    "workdrive_download_base_url": os.getenv(
        "ZOHO_WORKDRIVE_DOWNLOAD_BASE_URL",
        "https://workdrive.zoho.com/api/v1/download",
    ),
    "workdrive_parent_folder_id": os.getenv(
        "ZOHO_WORKDRIVE_PARENT_FOLDER_ID", "7jwlhc85fd4a73fd2499ea20df0b1bddcafea"
    ),
    "workdrive_parent_folder_ids": [
        folder_id.strip()
        for folder_id in os.getenv(
            "ZOHO_WORKDRIVE_PARENT_FOLDER_IDS",
            # HC Merchandising > HC Photo Bank > NEW LIGHT DESIGNS ACTUAL PHOTO
            # HC Merchandising > HC Photo Bank > homecartel Actual Photo (Lighting)
            # HC Merchandising > HC Photo Bank > ACTUAL PHOTOS
            # HC Purchasing > Actual Photos of Items
            # HC Encoding > Lighting Fixtures > Actual Product Photos
            # HC Encoding > Videos
            "sx97x278a598def89429d9227a80e77b392ff,bbr0j24dfbd268264489fac3d60aae1ae13a5,1qe2mc95b6d2242cb497b840d5c2be91bfc7b,uh2ugf230944dd2dd4d82b03a6ea786c79bd1,7jwlhc85fd4a73fd2499ea20df0b1bddcafea,7jwlhf523e6e83c48431883e7dad2078d6150",
        ).split(",")
        if folder_id.strip()
    ],
    "workdrive_global_search_enabled": os.getenv(
        "ZOHO_WORKDRIVE_GLOBAL_SEARCH_ENABLED", "false"
    ).strip().lower()
    in {"1", "true", "yes", "on"},
    "workdrive_team_id": os.getenv("ZOHO_WORKDRIVE_TEAM_ID", ""),
    "workdrive_search_result_limit": int(
        os.getenv("ZOHO_WORKDRIVE_SEARCH_RESULT_LIMIT", "25")
    ),
    "field_product_name": os.getenv("FIELD_PRODUCT_NAME", "Product_Name"),
    "field_request_type": os.getenv("FIELD_REQUEST_TYPE", "Type_of_Request"),
    "field_request_status": os.getenv("FIELD_REQUEST_STATUS", "Request_Status"),
    "field_actual_media": os.getenv("FIELD_ACTUAL_MEDIA", ""),
    "field_image_media": os.getenv("FIELD_IMAGE_MEDIA", "Actual_Photo1"),
    "field_video_media": os.getenv("FIELD_VIDEO_MEDIA", "Video"),
    # Note: "Image_Attachment" is a product reference image, NOT actual photo/video.
    # Do not use it for skip-checking — the automation uploads to "Image" and "Video" fields.
    "field_remarks": os.getenv("FIELD_REMARKS", "Remarks_Notes"),
    "request_type_value": os.getenv("REQUEST_TYPE_VALUE", "Actual Photo"),
    "request_done_value": os.getenv("REQUEST_DONE_VALUE", "Done"),
    "request_open_values": [
        value.strip()
        for value in os.getenv("REQUEST_OPEN_VALUES", "Pending,In progress").split(",")
        if value.strip()
    ],
    "success_remarks": os.getenv(
        "SUCCESS_REMARKS", "This is uploaded by Automated"
    ),
    "archive_item_name_field": os.getenv("ARCHIVE_ITEM_NAME_FIELD", "Item_Name"),
    "archive_status_field": os.getenv("ARCHIVE_STATUS_FIELD", "Pasado_ba_sa_Quality_Check"),
    "archive_approved_value": os.getenv("ARCHIVE_APPROVED_VALUE", "Approve"),
    "archive_media_fields": [
        value.strip()
        for value in os.getenv("ARCHIVE_MEDIA_FIELDS", "Image_Upload,Video_Upload").split(",")
        if value.strip()
    ],
    "poll_interval_seconds": float(os.getenv("POLL_INTERVAL_SECONDS", "45")),
    "api_throttle_seconds": float(os.getenv("API_THROTTLE_SECONDS", "0.5")),
    "request_timeout_seconds": float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")),
    "workdrive_list_page_size": int(os.getenv("WORKDRIVE_LIST_PAGE_SIZE", "50")),
    "workdrive_max_pages": int(os.getenv("WORKDRIVE_MAX_PAGES", "10")),
    "workdrive_search_depth": int(os.getenv("WORKDRIVE_SEARCH_DEPTH", "2")),
    "workdrive_recursive_search_depth": int(
        os.getenv("WORKDRIVE_RECURSIVE_SEARCH_DEPTH", "2")
    ),
    "video_browser_host": os.getenv("VIDEO_BROWSER_HOST", "127.0.0.1"),
    "video_browser_port": int(os.getenv("VIDEO_BROWSER_PORT", "8787")),
    "video_browser_depth": int(os.getenv("VIDEO_BROWSER_DEPTH", "4")),
    "video_browser_cache_seconds": float(
        os.getenv("VIDEO_BROWSER_CACHE_SECONDS", "20")
    ),
    "video_browser_title": os.getenv(
        "VIDEO_BROWSER_TITLE", "HomeCartel WorkDrive Reels"
    ),
    "video_browser_folder_ids": [
        folder_id.strip()
        for folder_id in os.getenv(
            "VIDEO_BROWSER_FOLDER_IDS",
            # HC Encoding > Videos
            "7jwlhf523e6e83c48431883e7dad2078d6150",
        ).split(",")
        if folder_id.strip()
    ],
    "max_upload_files_per_record": int(os.getenv("MAX_UPLOAD_FILES_PER_RECORD", "15")),
    "not_found_remarks": os.getenv(
        "NOT_FOUND_REMARKS", "No matching photo/video found for {product_name}"
    ),
    "not_found_retry_hours": float(os.getenv("NOT_FOUND_RETRY_HOURS", "24")),
    "max_pending_fetch": int(os.getenv("MAX_PENDING_FETCH", "50")),
    "processed_state_file": os.getenv("PROCESSED_STATE_FILE", str(STATE_FILE)),
}


def load_config() -> dict[str, Any]:
    config = dict(CONFIG)
    config["archive_media_fields"] = list(CONFIG["archive_media_fields"])
    config["request_open_values"] = list(CONFIG["request_open_values"])
    config["workdrive_parent_folder_ids"] = list(CONFIG["workdrive_parent_folder_ids"])
    config["video_browser_folder_ids"] = list(CONFIG["video_browser_folder_ids"])
    config["processed_state_file"] = Path(config["processed_state_file"]).expanduser()
    return config


def configure_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def validate_config(config: dict[str, Any]) -> list[str]:
    required_keys = [
        "zoho_client_id",
        "zoho_client_secret",
        "zoho_refresh_token",
        "workdrive_parent_folder_id",
    ]
    missing: list[str] = []
    for key in required_keys:
        value = str(config.get(key, "")).strip()
        if not value or value == "REPLACE_ME":
            missing.append(key)
    return missing
