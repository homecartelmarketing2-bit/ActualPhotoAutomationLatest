from __future__ import annotations

import argparse
import json
import logging
import sys

from .automation import ActualPhotoAutomation
from .config import configure_logging, load_config, validate_config
from .video_browser import VideoBrowserApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m actual_photo_automation",
        description="Automate Actual Photo encoding requests in Zoho Creator.",
    )
    parser.add_argument(
        "--debug-fields",
        action="store_true",
        help="Print Creator field metadata and sample record keys, then exit.",
    )
    parser.add_argument(
        "--test-one",
        action="store_true",
        help="Process only one eligible record, then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and log actions without uploading files or updating records.",
    )
    parser.add_argument(
        "--product",
        type=str,
        default="",
        help="Filter by product name (e.g. --product \"Svea\").",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument(
        "--video-browser",
        action="store_true",
        help="Start the internal WorkDrive video browser instead of the polling automation.",
    )
    parser.add_argument(
        "--video-browser-host",
        type=str,
        default="",
        help="Override the bind host for the internal video browser.",
    )
    parser.add_argument(
        "--video-browser-port",
        type=int,
        default=0,
        help="Override the bind port for the internal video browser.",
    )
    parser.add_argument(
        "--video-browser-depth",
        type=int,
        default=0,
        help="Override the recursive folder depth used by the internal video browser.",
    )
    parser.add_argument(
        "--video-browser-folder",
        action="append",
        default=[],
        help="Add a WorkDrive folder ID for the internal video browser. Repeat to include more folders.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(debug=args.debug)
    config = load_config()
    if args.video_browser_host:
        config["video_browser_host"] = args.video_browser_host.strip()
    if args.video_browser_port:
        config["video_browser_port"] = int(args.video_browser_port)
    if args.video_browser_depth:
        config["video_browser_depth"] = int(args.video_browser_depth)
    if args.video_browser_folder:
        config["video_browser_folder_ids"] = [folder.strip() for folder in args.video_browser_folder if folder.strip()]
    missing = validate_config(config)
    if missing:
        logging.error(
            "Missing required configuration values: %s. Update environment variables or config.py defaults.",
            ", ".join(missing),
        )
        return 2

    if args.video_browser:
        browser = VideoBrowserApp(config)
        browser.serve(
            host=str(config.get("video_browser_host", "")).strip() or None,
            port=int(config.get("video_browser_port", 0)) or None,
        )
        return 0

    automation = ActualPhotoAutomation(config)

    if args.debug_fields:
        payload = automation.debug_fields()
        print(json.dumps(payload, indent=2, default=str))
        return 0

    product_filter = args.product.strip() if args.product else ""

    if args.test_one:
        outcomes = automation.process_pending_records(dry_run=args.dry_run, limit=1, product_filter=product_filter)
        print(json.dumps([outcome.__dict__ for outcome in outcomes], indent=2))
        return 0

    if args.dry_run or product_filter:
        outcomes = automation.process_pending_records(dry_run=args.dry_run, product_filter=product_filter)
        print(json.dumps([outcome.__dict__ for outcome in outcomes], indent=2))
        return 0

    try:
        automation.run_forever()
    except KeyboardInterrupt:
        logging.info("Polling loop stopped by user")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
