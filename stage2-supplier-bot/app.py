"""
Top-level application entrypoint for local runs and frozen EXE builds.
"""

import argparse
import sys

import akeneo
import run
import zoho


def main():
    parser = argparse.ArgumentParser(description="HC SPEC Telegram Bot")
    parser.add_argument(
        "--debug-fields",
        action="store_true",
        help="Print all field names from the first record in All_Encoding_Requests and exit.",
    )
    parser.add_argument(
        "--test-akeneo",
        metavar="SKU",
        help="Check Akeneo for actual + catalog photo for the given SKU and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Log which Zoho records would be processed without sending "
            "Telegram messages or updating Zoho. Useful for verifying the "
            "query and Akeneo lookups against live data."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Do one poll cycle, then keep the Telegram listener alive long "
            "enough to receive replies (default: 5 minutes), then exit. "
            "Useful for end-to-end testing of the supplier flow in a "
            "single run."
        ),
    )
    parser.add_argument(
        "--once-reply-window-seconds",
        type=int,
        default=run.ONCE_REPLY_WINDOW_SECONDS,
        help=(
            "How long --once waits for Telegram replies before exiting "
            f"(default: {run.ONCE_REPLY_WINDOW_SECONDS}s)."
        ),
    )
    args = parser.parse_args()

    if args.debug_fields:
        zoho.debug_fields()
        sys.exit(0)

    if args.test_akeneo:
        product_name = args.test_akeneo
        print(f"\n=== Akeneo test for: {product_name} ===")
        has = akeneo.has_actual_photo(product_name)
        print(f"  Has actual photo (Actual_Photo): {has}")
        photo_bytes, identifier = akeneo.get_catalog_photo_bytes(product_name)
        if photo_bytes:
            print(f"  Catalog photo found: {len(photo_bytes)} bytes (Akeneo ID: {identifier})")
        else:
            print(f"  No catalog photo found in Akeneo. (Akeneo ID: {identifier or 'not found'})")
        sys.exit(0)

    run.run(
        dry_run=args.dry_run,
        once=args.once,
        once_reply_window_seconds=args.once_reply_window_seconds,
    )


if __name__ == "__main__":
    main()
