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

    run.run()


if __name__ == "__main__":
    main()
