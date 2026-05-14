# ActualPhotoAutomationLatest

Consolidated home for **HomeCartel's Actual Photo automation pipeline**.

## Pipeline overview

The pipeline polls Zoho Creator's `All_Encoding_Requests` report and processes
records based on the `Type_of_Request` field. It runs in **two cooperating
stages** + a fast-path for direct supplier requests:

```
Sales encodes request in Zoho CRM
            │
            ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  Type_of_Request == "Actual Photo"                             │
   │     → Stage 1 (stage1-find-actual-photo) tries to find it      │
   │       in Zoho WorkDrive + Archive (Kanban Notes).              │
   │       • found     → upload + mark Done.                        │
   │       • not found → writes trigger text in Remarks_Notes,      │
   │                     leaves Pending.                            │
   │                                                                │
   │     → Stage 2 (stage2-supplier-bot) sees the trigger text,     │
   │       sends a Telegram request to the supplier (Tony) with     │
   │       the catalog photo. Handles his photo/video/text reply,   │
   │       uploads to Zoho + Akeneo, marks Done. Follows up every   │
   │       10 minutes until reply.                                  │
   │                                                                │
   │  Type_of_Request == "Supplier Actual Photo"                    │
   │     → Stage 1 is SKIPPED. Stage 2 picks it up immediately      │
   │       (no trigger text required) and sends the Telegram        │
   │       request straight to the supplier.                        │
   └────────────────────────────────────────────────────────────────┘
```

The "connection" between the two stages is the `Remarks_Notes` field that
Stage 1 writes when it cannot find a photo. Stage 2 reads that text as its
trigger for `Actual Photo` requests. For `Supplier Actual Photo` requests
there is no trigger text — Stage 2 picks them up directly.

## Layout

```
ActualPhotoAutomationLatest/
├── README.md                    (this file)
├── stage1-find-actual-photo/    Stage 1 — search WorkDrive + Archive
│                                (from Crm-Actual-Photo)
└── stage2-supplier-bot/         Stage 2 — Telegram supplier bot
                                 (from Automated-Tony-Send-No-Actual)
                                 + Supplier Actual Photo request type
```

## Stage 1 — `stage1-find-actual-photo`

First-pass automation. Polls Zoho for `Type_of_Request == "Actual Photo"` rows
that are still `Pending` / `In progress`, then for each row:

1. Searches Zoho WorkDrive folders (HC Photo Bank, HC Purchasing > Actual Photos,
   HC Encoding > Lighting Fixtures > Actual Product Photos, HC Encoding > Videos,
   etc.).
2. Searches the Zoho Archive (Kanban Notes) module for Approved records that
   match the product name.
3. If matches are found, downloads and uploads them to the request's
   `Actual_Photo1` / `Video` field and marks the request `Done`.
4. If no matches are found, writes the trigger text
   `Not available from Kanban Notes / Zoho Drive / Akeneo. Waiting for actual photos and videos from the Supplier`
   to `Remarks_Notes` so Stage 2 picks it up.

Entrypoint: `stage1-find-actual-photo/main.py`
Dependencies: `pip install -r stage1-find-actual-photo/requirements.txt`

## Stage 2 — `stage2-supplier-bot`

Telegram bot that talks to the supplier (Tony). Three threads:

1. **Poller** — every 60 s: pulls all Zoho rows of the two supported request
   types, sends a Telegram message for each new row, tracks them in
   `hcspec_state.json`.
2. **Follow-up** — every 60 s tick: if a row hasn't been replied to in
   10 minutes, resend the request.
3. **Telegram long-poll** — continuously listens for replies and handles
   them: photo → upload to Zoho `Actual_Photo1` + Akeneo `Actual_Photo`;
   video → upload to Zoho `Video`; text → run through the local LLM, if it
   means "not available" upload the placeholder PNG, otherwise just store
   the translated text in `Remarks_Notes`. Marks the row `Done` once handled.

Manual upload command (works inside Telegram): `done <SKU>` or `upload <SKU>`
attached to a photo or video — looks up the SKU in Akeneo, finds the matching
Zoho row, uploads the file, and marks the row `Done`.

### Supported request types

| `Type_of_Request`      | When Stage 2 acts                                     |
|------------------------|-------------------------------------------------------|
| `Actual Photo`         | After Stage 1 has written the trigger text into `Remarks_Notes` (i.e. WorkDrive/Archive search produced nothing). |
| `Supplier Actual Photo`| Immediately, for any `Pending` / `In progress` row. Used when the requestor already knows the photo must come straight from the supplier. |

Entrypoint: `stage2-supplier-bot/app.py` (or `python -m stage2-supplier-bot`)
Dependencies: `pip install -r stage2-supplier-bot/requirements.txt`

### One-time setup
- Add the bot to the supplier's Telegram group, send `/register` from inside
  the group so the bot stores the `chat_id`.
- Configure Zoho / Akeneo / Telegram credentials via environment variables
  (see `stage2-supplier-bot/config.py` for the full list).

## Running both stages

The two stages share a Zoho refresh token and a few environment variables but
are otherwise independent — run each in its own process / Windows Task / VM
service. Stage 1 is a single-process loop; Stage 2 is a multi-threaded bot.

```bash
# In one shell:
cd stage1-find-actual-photo && python main.py

# In another shell:
cd stage2-supplier-bot && python app.py
```
