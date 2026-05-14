# =====================================================================
#  MAIN — Round-Robin Table Automation
#  Entry point: cycles through all Airtable tables, processing one
#  row per table per cycle.
# =====================================================================

import sys
import os
import time

# Ensure the project root (where this file lives) is on the Python path
# so that `config` and `services` packages are always importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import IDLE_WAIT_SECONDS
from config.tables import AIRTABLE_TABLES
from pipeline import process_one_row


def main():
    print("\n" + "=" * 60)
    print("  MOODBOARD V3 — ROUND-ROBIN TABLE AUTOMATION")
    print("  (Prompt → Styled Photo → Blend → Moodboard → Videos)")
    print("=" * 60)

    while True:
        total_actions = 0

        for table in AIRTABLE_TABLES:
            table_name = table["name"]
            table_id = table["id"]
            blend_prompt = table["blend_prompt"]

            print(f"\n{'─' * 50}")
            print(f"  TABLE: {table_name}")
            print(f"{'─' * 50}")

            if process_one_row(table_id, blend_prompt):
                total_actions += 1
                print(f"\n[✓] Done with 1 row in '{table_name}'. Moving to next table...")
            else:
                print(f"[INFO] No pending rows in '{table_name}'.")

        if total_actions == 0:
            print(f"\n[INFO] All tables clear. Waiting {IDLE_WAIT_SECONDS}s before next cycle...")
            time.sleep(IDLE_WAIT_SECONDS)
        else:
            print(f"\n[INFO] Completed {total_actions} rows this cycle. Starting next round...")


if __name__ == "__main__":
    main()