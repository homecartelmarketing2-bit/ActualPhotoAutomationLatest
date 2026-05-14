from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

# Allow running as a standalone script or as a package module
try:
    from .akeneo import AkeneoClient
    from .auth import ZohoAuth
    from .config import configure_logging, load_config
    from .creator import ZohoCreator
    from .helpers import creator_criteria_value, extract_record_id, scalar_to_text
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from akeneo import AkeneoClient
    from auth import ZohoAuth
    from config import configure_logging, load_config
    from creator import ZohoCreator
    from helpers import creator_criteria_value, extract_record_id, scalar_to_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = PROJECT_ROOT / "akeneo_upload_state.json"
REPORT_FILE = PROJECT_ROOT / "akeneo_upload_report.csv"

# Akeneo defaults (overridable via env vars)
AKENEO_HOST = os.getenv("AKENEO_HOST", "http://54.255.135.132")
AKENEO_CLIENT_ID = os.getenv(
    "AKENEO_CLIENT_ID", "5_uedqn47uk2884owwc8wkokkkw0cw4kc84s0kss0gwsgcg8w4s"
)
AKENEO_SECRET = os.getenv(
    "AKENEO_SECRET", "2hzym4lyvxk48kccowokc8ockowgcw0kwg8ksoco88c4kok0og"
)
AKENEO_USERNAME = os.getenv("AKENEO_USERNAME", "api_connection_3885")
AKENEO_PASSWORD = os.getenv("AKENEO_PASSWORD", "376710dfe")
AKENEO_CHANNEL = os.getenv("AKENEO_CHANNEL", "home_cartel")
AKENEO_LOCALE = os.getenv("AKENEO_LOCALE", "en_US")

# Default Akeneo attribute codes for actual photos (3 fields)
# Discover the actual codes by running with --list-attributes first.
AKENEO_PHOTO_ATTRIBUTES = [
    attr.strip()
    for attr in os.getenv(
        "AKENEO_PHOTO_ATTRIBUTES", "Actual_Photo,another_picture_5,another_picture_6"
    ).split(",")
    if attr.strip()
]

POLL_INTERVAL = float(os.getenv("AKENEO_POLL_INTERVAL", "45"))


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"uploaded": {}}


def save_state(state: dict[str, Any], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def append_csv_report(
    report_path: Path,
    product_name: str,
    akeneo_id: str,
    status: str,
    photo_1: str = "",
    photo_2: str = "",
    photo_3: str = "",
    notes: str = "",
) -> None:
    write_header = not report_path.exists()
    try:
        f = report_path.open("a", newline="", encoding="utf-8")
    except PermissionError:
        logger.warning("Cannot write to %s (file open in another program?), skipping CSV row", report_path)
        return
    with f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "Product_Name",
                "Akeneo_Identifier",
                "Status",
                "Actual_Photo_1",
                "Actual_Photo_2",
                "Actual_Photo_3",
                "Notes",
                "Timestamp",
            ])
        writer.writerow([
            product_name,
            akeneo_id,
            status,
            photo_1,
            photo_2,
            photo_3,
            notes,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ])


def extract_file_paths_from_field(raw_value: Any) -> list[str]:
    """Extract downloadable file paths from a Zoho Creator file upload field."""
    if not raw_value:
        return []

    paths: list[str] = []
    if isinstance(raw_value, list):
        for item in raw_value:
            item_str = str(item).strip()
            if not item_str:
                continue
            if "filepath=" in item_str:
                fp = item_str.rsplit("filepath=", 1)[-1]
            else:
                fp = item_str.rsplit("/", 1)[-1]
            if fp:
                paths.append(fp)
    elif isinstance(raw_value, str):
        raw_str = raw_value.strip()
        if raw_str:
            if "filepath=" in raw_str:
                fp = raw_str.rsplit("filepath=", 1)[-1]
            elif "/" in raw_str:
                fp = raw_str.rsplit("/", 1)[-1]
            else:
                fp = raw_str
            if fp:
                paths.append(fp)
    elif isinstance(raw_value, dict):
        text = scalar_to_text(raw_value)
        if text:
            paths.append(text)
    return paths


def process_records(
    creator: ZohoCreator,
    akeneo: AkeneoClient,
    config: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    report_path: Path,
    *,
    product_filter: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    photo_attributes: list[str],
) -> dict[str, int]:
    """Fetch and process eligible records. Returns summary counts."""
    counts = {"processed": 0, "uploaded": 0, "skipped": 0, "not_found": 0, "error": 0}

    crm_app = config["crm_app"]
    encoding_report = config["encoding_report"]
    field_status = config["field_request_status"]
    field_request_type = config["field_request_type"]
    done_value = config["request_done_value"]
    request_type_value = config["request_type_value"]

    # Only fetch completed Actual Photo requests.
    criteria = (
        f'({field_status} == "{creator_criteria_value(done_value)}"'
        f' && {field_request_type} == "{creator_criteria_value(request_type_value)}")'
    )
    max_fetch = int(config.get("max_pending_fetch", 200))

    logger.info("Fetching records with criteria: %s", criteria)
    records = creator.get_records(crm_app, encoding_report, criteria=criteria, max_records=max_fetch)
    logger.info("Fetched %d records from Zoho Creator", len(records))

    for record in records:
        if limit and counts["processed"] >= limit:
            break

        try:
            record_id = extract_record_id(record)
        except KeyError:
            logger.warning("Skipping record without ID: %s", record)
            continue

        # Skip already processed
        if record_id in state.get("uploaded", {}):
            continue

        product_name = scalar_to_text(record.get(config["field_product_name"]))
        if not product_name:
            logger.debug("Skipping record %s: no product name", record_id)
            continue

        request_type = scalar_to_text(record.get(field_request_type))
        if request_type != request_type_value:
            logger.debug(
                "Skipping record %s (%s): request type '%s' is not eligible",
                record_id,
                product_name,
                request_type,
            )
            continue

        # Apply product filter
        if product_filter and product_filter.lower() not in product_name.lower():
            continue

        # Check Actual_Photo field has content
        actual_photo_raw = record.get("Actual_Photo1")
        file_paths = extract_file_paths_from_field(actual_photo_raw)
        if not file_paths:
            logger.debug("Skipping record %s (%s): no actual photos", record_id, product_name)
            continue

        counts["processed"] += 1
        logger.info("Processing: %s (%d photos)", product_name, len(file_paths))

        if dry_run:
            logger.info("[DRY RUN] Would upload %d photos for %s", len(file_paths), product_name)
            photo_names = file_paths[:3]
            append_csv_report(
                report_path,
                product_name,
                "(dry run)",
                "dry_run",
                photo_names[0] if len(photo_names) > 0 else "",
                photo_names[1] if len(photo_names) > 1 else "",
                photo_names[2] if len(photo_names) > 2 else "",
                f"{len(file_paths)} photos would be uploaded",
            )
            continue

        # Find product in Akeneo by name
        product_data, akeneo_id, product_type = akeneo.find_by_name(product_name)
        if not product_data:
            logger.warning("Product not found in Akeneo by name: %s", product_name)
            counts["not_found"] += 1
            append_csv_report(
                report_path, product_name, "", "not_found_in_akeneo",
                notes=f"No product or product-model found with name '{product_name}'",
            )
            state["uploaded"][record_id] = {
                "product_name": product_name,
                "akeneo_id": "",
                "status": "not_found_in_akeneo",
                "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_state(state, state_path)
            continue

        logger.info("Found in Akeneo: %s (type=%s, code=%s)", product_name, product_type, akeneo_id)

        # Check if product already has actual photos
        if akeneo.product_has_actual_photos(product_data, photo_attributes):
            logger.info("Skipping %s: already has actual photos in Akeneo", akeneo_id)
            counts["skipped"] += 1
            append_csv_report(
                report_path, product_name, akeneo_id, "already_has_photos",
                photo_1="existing", photo_2="existing", photo_3="existing",
                notes="Product already has actual photos",
            )
            state["uploaded"][record_id] = {
                "product_name": product_name,
                "akeneo_id": akeneo_id,
                "status": "already_has_photos",
                "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_state(state, state_path)
            continue

        # Download and upload photos
        uploaded_files = ["", "", ""]
        upload_success = True

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            for idx, fp in enumerate(file_paths[:3]):
                attr_code = photo_attributes[idx] if idx < len(photo_attributes) else None
                if not attr_code:
                    logger.warning("No attribute code for photo index %d, skipping", idx)
                    break

                try:
                    dest = tmp_path / fp
                    downloaded = creator.download_file_field_to_path(
                        crm_app,
                        encoding_report,
                        record_id,
                        "Actual_Photo1",
                        dest,
                        filepath=fp,
                    )
                    logger.info("Downloaded: %s", downloaded.name)

                    response = akeneo.upload_media_file(
                        downloaded,
                        akeneo_id,
                        attr_code,
                        product_type=product_type,
                        scope=None,
                        locale=None,
                    )
                    if response.status_code == 201:
                        uploaded_files[idx] = downloaded.name
                    else:
                        upload_success = False
                        uploaded_files[idx] = f"FAILED:{response.status_code}"
                        logger.error(
                            "Upload failed for %s -> %s.%s: %s",
                            downloaded.name, akeneo_id, attr_code, response.text[:300],
                        )
                except Exception as exc:
                    upload_success = False
                    uploaded_files[idx] = f"ERROR:{exc}"
                    logger.exception(
                        "Error processing photo %d for %s", idx + 1, product_name
                    )

        if upload_success and any(f and not f.startswith(("FAILED:", "ERROR:")) for f in uploaded_files):
            counts["uploaded"] += 1
            status = "uploaded"
        else:
            counts["error"] += 1
            status = "error"

        append_csv_report(
            report_path, product_name, akeneo_id, status,
            uploaded_files[0], uploaded_files[1], uploaded_files[2],
        )
        state["uploaded"][record_id] = {
            "product_name": product_name,
            "akeneo_id": akeneo_id,
            "status": status,
            "files_uploaded": sum(1 for f in uploaded_files if f and not f.startswith(("FAILED:", "ERROR:"))),
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_state(state, state_path)

    return counts


def run_list_attributes(akeneo: AkeneoClient) -> None:
    """List all image-type attributes in Akeneo and print them."""
    logger.info("Fetching image attributes from Akeneo...")
    attributes = akeneo.list_attributes(type_filter="pim_catalog_image")
    if not attributes:
        print("No image attributes found in Akeneo.")
        return

    print(f"\n{'Code':<35} {'Type':<25} {'Labels'}")
    print("-" * 90)
    for attr in attributes:
        code = attr.get("code", "")
        attr_type = attr.get("type", "")
        labels = attr.get("labels", {})
        label_str = labels.get("en_US", "") or next(iter(labels.values()), "") if labels else ""
        print(f"{code:<35} {attr_type:<25} {label_str}")
    print(f"\nTotal: {len(attributes)} image attributes")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload actual photos from Zoho Creator to Akeneo PIM"
    )
    parser.add_argument(
        "--list-attributes",
        action="store_true",
        help="List all image-type attributes in Akeneo and exit",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log actions without uploading")
    parser.add_argument("--product", type=str, default=None, help="Filter by product name")
    parser.add_argument("--limit", type=int, default=None, help="Max records to process per cycle")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--once", action="store_true", help="Run once instead of continuous loop"
    )
    args = parser.parse_args()

    configure_logging(debug=args.debug)

    # Load Zoho config
    config = load_config()

    # Init Akeneo client
    akeneo = AkeneoClient(
        host=AKENEO_HOST,
        client_id=AKENEO_CLIENT_ID,
        secret=AKENEO_SECRET,
        username=AKENEO_USERNAME,
        password=AKENEO_PASSWORD,
        channel=AKENEO_CHANNEL,
    )

    if args.list_attributes:
        run_list_attributes(akeneo)
        return

    # Validate photo attributes
    if not AKENEO_PHOTO_ATTRIBUTES:
        print(
            "ERROR: No Akeneo photo attributes configured.\n"
            "Run with --list-attributes first to discover attribute codes,\n"
            "then set AKENEO_PHOTO_ATTRIBUTES env var (comma-separated)."
        )
        sys.exit(2)

    logger.info("Akeneo photo attributes: %s", AKENEO_PHOTO_ATTRIBUTES)
    logger.info("Akeneo channel: %s, locale: %s", AKENEO_CHANNEL, AKENEO_LOCALE)

    # Init Zoho
    auth = ZohoAuth(config)
    creator = ZohoCreator(auth, config)

    state_path = STATE_FILE
    report_path = REPORT_FILE
    state = load_state(state_path)

    logger.info("Starting Akeneo upload automation")
    logger.info("State file: %s", state_path)
    logger.info("Report file: %s", report_path)

    while True:
        try:
            counts = process_records(
                creator,
                akeneo,
                config,
                state,
                state_path,
                report_path,
                product_filter=args.product,
                limit=args.limit,
                dry_run=args.dry_run,
                photo_attributes=AKENEO_PHOTO_ATTRIBUTES,
            )
            logger.info(
                "Cycle complete: %d processed, %d uploaded, %d skipped, %d not_found, %d errors",
                counts["processed"],
                counts["uploaded"],
                counts["skipped"],
                counts["not_found"],
                counts["error"],
            )
        except Exception:
            logger.exception("Error during processing cycle")

        if args.once or args.dry_run:
            break

        logger.info("Sleeping %s seconds before next cycle...", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
