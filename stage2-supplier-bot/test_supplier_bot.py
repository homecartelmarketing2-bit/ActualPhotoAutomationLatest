"""
Unit tests for the Stage 2 (Telegram supplier bot) behaviors that were
added / changed for the "NOT AVAILABLE" + "Supplier Actual Photo dropdown"
work:

  1. zoho.get_pending_records() issues two queries — one for "Actual Photo"
     with an open-status filter and one for "Supplier Actual Photo" with
     NO status filter — then merges, de-duplicates by ID and applies the
     trigger-text filter to "Actual Photo" rows.

  2. state.mark_processed / is_processed / clear_processed correctly stop
     the poller from re-sending the same "Supplier Actual Photo" record
     on every cycle.

  3. bot._handle_text_reply() sets Request_Status = "NOT AVAILABLE" (not
     "Done") when the supplier indicates the photo is not available.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _reload_modules(state_file: str):
    """
    Reload config + state with STATE_FILE pointed at a tempfile so tests
    don't clobber each other or the real hcspec_state.json.
    """
    os.environ["HCSPEC_TEST_STATE_FILE"] = state_file
    for mod_name in ("state", "config"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import importlib
    config_mod = importlib.import_module("config")
    state_mod = importlib.import_module("state")
    # Force STATE_FILE to point at our temp path.
    config_mod.STATE_FILE = state_file
    state_mod._path = lambda: state_file  # type: ignore[attr-defined]
    return config_mod, state_mod


class _FakeResp:
    def __init__(self, data, ok=True, status=200, text=""):
        self._data = data
        self.ok = ok
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")

    def json(self):
        return self._data


class GetPendingRecordsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config, self.state = _reload_modules(
            str(Path(self.tmpdir.name) / "state.json")
        )
        # zoho imports config / requests, reload it after we've reloaded config
        if "zoho" in sys.modules:
            del sys.modules["zoho"]
        import zoho
        self.zoho = zoho

    def test_two_queries_merge_dedup_and_trigger_filter(self):
        """
        get_pending_records() should:
          - issue one criteria for Actual Photo + open status
          - issue another criteria for Supplier Actual Photo with NO status filter
          - merge, dedup by ID
          - keep Actual Photo rows only if Remarks_Notes contains TRIGGER_TEXT
          - keep all Supplier Actual Photo rows
        """
        actual_photo_records = [
            {
                "ID": "1",
                "Type_of_Request": "Actual Photo",
                "Request_Status": "Pending",
                "Remarks_Notes": f"prefix {self.config.TRIGGER_TEXT} suffix",
                "Product_Name": "Lamp A",
            },
            {
                "ID": "2",
                "Type_of_Request": "Actual Photo",
                "Request_Status": "In progress",
                "Remarks_Notes": "no trigger here",
                "Product_Name": "Lamp B",
            },
        ]
        supplier_records = [
            {
                "ID": "3",
                "Type_of_Request": "Supplier Actual Photo",
                "Request_Status": "Done",
                "Remarks_Notes": "",
                "Product_Name": "Lamp C",
            },
            {
                "ID": "4",
                "Type_of_Request": "Supplier Actual Photo",
                "Request_Status": "Pending",
                "Remarks_Notes": "",
                "Product_Name": "Lamp D",
            },
            # Same ID as an Actual Photo record above (simulates dropdown
            # being flipped to Supplier Actual Photo after Stage-1 wrote
            # the trigger text — should NOT be returned twice).
            {
                "ID": "1",
                "Type_of_Request": "Supplier Actual Photo",
                "Request_Status": "Done",
                "Remarks_Notes": f"prefix {self.config.TRIGGER_TEXT} suffix",
                "Product_Name": "Lamp A",
            },
        ]

        calls: list[dict] = []

        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append({"url": url, "params": dict(params or {})})
            criteria = params["criteria"]
            if "Supplier Actual Photo" in criteria and "Request_Status" not in criteria:
                page = supplier_records
            elif "Actual Photo" in criteria and "Request_Status" in criteria:
                page = actual_photo_records
            else:
                page = []
            # Single-page response (page_size > len(page) -> loop exits)
            return _FakeResp({"data": page})

        with mock.patch.object(self.zoho.requests, "get", side_effect=fake_get):
            with mock.patch.object(
                self.zoho._auth, "headers", return_value={"Authorization": "x"}
            ):
                result = self.zoho.get_pending_records()

        ids = [str(r.get("ID")) for r in result]
        self.assertIn("1", ids, "Actual Photo with trigger text must be kept")
        self.assertNotIn("2", ids, "Actual Photo WITHOUT trigger text must be dropped")
        self.assertIn("3", ids, "Supplier Actual Photo (Done) must be kept")
        self.assertIn("4", ids, "Supplier Actual Photo (Pending) must be kept")
        self.assertEqual(len(ids), len(set(ids)), "IDs must be unique after dedup")

        criterias = [c["params"].get("criteria", "") for c in calls]
        self.assertTrue(
            any(
                'Type_of_Request=="Actual Photo"' in c and "Request_Status" in c
                for c in criterias
            ),
            "Expected one query with Actual Photo + open status filter",
        )
        self.assertTrue(
            any(
                'Type_of_Request=="Supplier Actual Photo"' in c
                and "Request_Status" not in c
                for c in criterias
            ),
            "Expected one query for Supplier Actual Photo with NO status filter",
        )


class ProcessedRecordsStateTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config, self.state = _reload_modules(
            str(Path(self.tmpdir.name) / "state.json")
        )

    def test_mark_and_is_processed_roundtrip(self):
        s = self.state.load()
        self.assertFalse(self.state.is_processed(s, "rec-1", "Supplier Actual Photo"))

        self.state.mark_processed(
            s, "rec-1", "Supplier Actual Photo",
            final_status="Done",
            product_name="Lamp",
            akeneo_identifier="SKU-1",
        )

        # Reload from disk to confirm persistence.
        s2 = self.state.load()
        self.assertTrue(self.state.is_processed(s2, "rec-1", "Supplier Actual Photo"))

    def test_is_processed_returns_false_when_request_type_changed(self):
        s = self.state.load()
        self.state.mark_processed(
            s, "rec-1", "Supplier Actual Photo",
            final_status="Done",
        )
        # Same record, but dropdown is now something else — should NOT skip.
        self.assertFalse(self.state.is_processed(s, "rec-1", "Actual Photo"))

    def test_clear_processed_removes_entry(self):
        s = self.state.load()
        self.state.mark_processed(
            s, "rec-1", "Supplier Actual Photo",
            final_status="Done",
        )
        self.assertTrue(self.state.clear_processed(s, "rec-1"))
        self.assertFalse(self.state.is_processed(s, "rec-1", "Supplier Actual Photo"))
        # Second clear is a no-op.
        self.assertFalse(self.state.clear_processed(s, "rec-1"))


class TextReplyNotAvailableTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config, self.state = _reload_modules(
            str(Path(self.tmpdir.name) / "state.json")
        )
        if "bot" in sys.modules:
            del sys.modules["bot"]
        import bot
        self.bot = bot

    def test_unavailable_reply_sets_not_available_status(self):
        zoho_updates: list[dict] = []
        uploads: list[dict] = []

        def fake_update_record(record_id, fields):
            zoho_updates.append({"record_id": record_id, "fields": dict(fields)})

        def fake_upload_file(record_id, field_name, _bytes, filename=""):
            uploads.append({"record_id": record_id, "field": field_name, "filename": filename})
            return {"code": 3000}

        with mock.patch.object(self.bot.zoho, "update_record", side_effect=fake_update_record), \
             mock.patch.object(self.bot.zoho, "upload_file", side_effect=fake_upload_file), \
             mock.patch.object(self.bot, "_send_text"), \
             mock.patch.object(self.bot, "_load_placeholder_image", return_value=b"png"), \
             mock.patch.object(self.bot.llm, "translate_to_english", return_value="wala po"), \
             mock.patch.object(self.bot.llm, "classify_unavailable", return_value=True):
            s = self.state.load()
            # Seed pending so request_type can be looked up.
            self.state.add_pending(
                s, "rec-1", "Lamp", "", "SKU-1", 999,
                sent_time="2026-01-01T00:00:00",
                request_type="Supplier Actual Photo",
            )
            self.bot._handle_text_reply(
                "wala po", "rec-1", "Lamp", "SKU-1", 1234, s,
            )

        # Status must be set to exactly "Not available" (matching the dropdown),
        # not Done.
        status_updates = [
            u["fields"].get("Request_Status") for u in zoho_updates if "Request_Status" in u["fields"]
        ]
        self.assertIn("Not available", status_updates)
        self.assertIn(self.config.STATUS_NOT_AVAILABLE, status_updates)
        self.assertNotIn(self.config.STATUS_DONE, status_updates)

        # Placeholder image must land in the configured "Internal Actual Photo"
        # field (in this form that's still the legacy Actual_Photo1 API name,
        # because Zoho keeps the link name when a field is renamed in the UI).
        upload_fields = [u["field"] for u in uploads]
        self.assertIn(self.config.FIELD_INTERNAL_ACTUAL_PHOTO, upload_fields)

        # And the record must be marked processed so the poller doesn't re-send.
        s_after = self.state.load()
        self.assertTrue(
            self.state.is_processed(s_after, "rec-1", "Supplier Actual Photo")
        )

    def test_photo_reply_uploads_to_supplier_actual_photo_field(self):
        """Supplier-uploaded photos must land in 'Supplier's Actual Photo'."""
        uploads: list[dict] = []
        zoho_updates: list[dict] = []

        def fake_upload_file(record_id, field_name, _bytes, filename=""):
            uploads.append({"record_id": record_id, "field": field_name, "filename": filename})
            return {"code": 3000}

        def fake_update_record(record_id, fields):
            zoho_updates.append({"record_id": record_id, "fields": dict(fields)})

        with mock.patch.object(self.bot.zoho, "upload_file", side_effect=fake_upload_file), \
             mock.patch.object(self.bot.zoho, "update_record", side_effect=fake_update_record), \
             mock.patch.object(self.bot, "_send_text"), \
             mock.patch.object(self.bot, "_download_file", return_value=b"jpgbytes"), \
             mock.patch.object(self.bot.akeneo, "upload_actual_photo", return_value=True):
            s = self.state.load()
            self.state.add_pending(
                s, "rec-3", "Lamp", "", "SKU-3", 997,
                sent_time="2026-01-01T00:00:00",
                request_type="Supplier Actual Photo",
            )
            message = {"photo": [{"file_id": "abc", "width": 1024, "height": 768}]}
            self.bot._handle_photo_reply(message, "rec-3", "Lamp", "SKU-3", 1234, s)

        upload_fields = [u["field"] for u in uploads]
        self.assertIn(self.config.FIELD_SUPPLIER_ACTUAL_PHOTO, upload_fields)
        self.assertNotIn("Actual_Photo1", upload_fields)

        # Remarks must use the exact "This is automated na uploaded from supplier" string.
        remarks = next(
            (u["fields"].get("Remarks_Notes", "") for u in zoho_updates if "Remarks_Notes" in u["fields"]),
            "",
        )
        self.assertTrue(
            remarks.startswith("This is automated na uploaded from supplier"),
            f"unexpected remarks: {remarks!r}",
        )

    def test_video_reply_uploads_to_supplier_actual_photo_field(self):
        uploads: list[dict] = []

        def fake_upload_file(record_id, field_name, _bytes, filename=""):
            uploads.append({"record_id": record_id, "field": field_name, "filename": filename})
            return {"code": 3000}

        with mock.patch.object(self.bot.zoho, "upload_file", side_effect=fake_upload_file), \
             mock.patch.object(self.bot.zoho, "update_record"), \
             mock.patch.object(self.bot, "_send_text"), \
             mock.patch.object(self.bot, "_download_file", return_value=b"mp4bytes"):
            s = self.state.load()
            self.state.add_pending(
                s, "rec-4", "Lamp", "", "SKU-4", 996,
                sent_time="2026-01-01T00:00:00",
                request_type="Supplier Actual Photo",
            )
            message = {"video": {"file_id": "vid1", "mime_type": "video/mp4"}}
            self.bot._handle_video_reply(message, "rec-4", "Lamp", "SKU-4", 1234, s)

        upload_fields = [u["field"] for u in uploads]
        self.assertIn(self.config.FIELD_SUPPLIER_ACTUAL_PHOTO, upload_fields)
        self.assertNotIn("Video", upload_fields)

    def test_normal_text_reply_still_marks_done(self):
        zoho_updates: list[dict] = []

        def fake_update_record(record_id, fields):
            zoho_updates.append({"record_id": record_id, "fields": dict(fields)})

        with mock.patch.object(self.bot.zoho, "update_record", side_effect=fake_update_record), \
             mock.patch.object(self.bot, "_send_text"), \
             mock.patch.object(self.bot.llm, "translate_to_english", return_value="will check"), \
             mock.patch.object(self.bot.llm, "classify_unavailable", return_value=False):
            s = self.state.load()
            self.state.add_pending(
                s, "rec-2", "Lamp", "", "SKU-2", 998,
                sent_time="2026-01-01T00:00:00",
                request_type="Actual Photo",
            )
            self.bot._handle_text_reply(
                "will check tomorrow", "rec-2", "Lamp", "SKU-2", 1234, s,
            )

        status_updates = [
            u["fields"].get("Request_Status") for u in zoho_updates if "Request_Status" in u["fields"]
        ]
        self.assertEqual(status_updates, [self.config.STATUS_DONE])


class SubformPollerTests(unittest.TestCase):
    """Tests for the new subform-based Stage-2 poller behaviour.

    Each `Product_Name1` row in a Zoho record is treated as an
    independent pending item — Stage 2 must send one Telegram per row
    and track each one under a composite `record_id::row_id` key so
    multi-item records aren't collapsed back into a single request.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config, self.state = _reload_modules(
            str(Path(self.tmpdir.name) / "state.json")
        )
        for mod_name in ("run", "bot", "zoho", "akeneo"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        import run
        self.run = run

    def _two_item_record(self) -> dict:
        return {
            "ID": "4662244000012128236",
            "Type_of_Request": "Actual Photo",
            "Request_Status": "Pending",
            "Remarks_Notes": self.config.TRIGGER_TEXT,
            "Product_Name": "",
            "Product_Name1": [
                {
                    "ID": "row-A",
                    "Items": {"Item_Name": "Rhosyn | Alabaster Wall Light"},
                    "SKU": "XR-B1029-1",
                },
                {
                    "ID": "row-B",
                    "Items": {"Item_Name": "Rhosyn | Alabaster Wall Light"},
                    "SKU": "XR-B1029-2",
                },
            ],
        }

    def test_extract_subform_items_reads_rows_from_subform(self):
        items = self.run._extract_subform_items(self._two_item_record())
        self.assertEqual(
            items,
            [
                {"row_id": "row-A", "product_name": "Rhosyn | Alabaster Wall Light", "sku": "XR-B1029-1"},
                {"row_id": "row-B", "product_name": "Rhosyn | Alabaster Wall Light", "sku": "XR-B1029-2"},
            ],
        )

    def test_extract_subform_items_ignores_top_level_product_name(self):
        record = {
            "ID": "rec-1",
            "Product_Name": "Top Level Should Be Ignored",
            # No Product_Name1 at all.
        }
        self.assertEqual(self.run._extract_subform_items(record), [])

    def test_poll_once_sends_one_telegram_per_subform_item(self):
        """Two subform items => two send_photo_request calls => two pending entries."""
        record = self._two_item_record()

        # Force chat_id so _poll_once doesn't early-return.
        s = self.state.load()
        self.state.set_chat_id(s, 12345)
        self.state.save(s)
        # Reload state so _poll_once sees the chat_id.
        state = self.state.load()

        sent: list[dict] = []

        def fake_send(chat_id, product_name, akeneo_identifier, sales_notes,
                      photo_bytes, request_type=""):
            mid = 1000 + len(sent)
            sent.append({
                "chat_id": chat_id,
                "product_name": product_name,
                "akeneo_identifier": akeneo_identifier,
                "request_type": request_type,
                "message_id": mid,
            })
            return mid

        zoho_updates: list[dict] = []

        def fake_update_record(record_id, fields):
            zoho_updates.append({"record_id": record_id, "fields": dict(fields)})

        with mock.patch.object(self.run.zoho, "get_pending_records", return_value=[record]), \
             mock.patch.object(self.run.zoho, "update_record", side_effect=fake_update_record), \
             mock.patch.object(self.run.akeneo, "lookup_product",
                               side_effect=lambda name, identifier_hint="":
                               (identifier_hint, {"identifier": identifier_hint}, False, None)), \
             mock.patch.object(self.run.bot, "send_photo_request", side_effect=fake_send):
            self.run._poll_once(state)

        # 2 subform items => exactly 2 Telegram sends.
        self.assertEqual(len(sent), 2, f"expected 2 sends, got: {sent}")
        skus = {s["akeneo_identifier"] for s in sent}
        self.assertEqual(skus, {"XR-B1029-1", "XR-B1029-2"})

        # 2 pending entries (one per row), keyed by composite key.
        pending_keys = list(state["pending"].keys())
        self.assertEqual(
            sorted(pending_keys),
            sorted(["4662244000012128236::row-A", "4662244000012128236::row-B"]),
        )
        for key, entry in state["pending"].items():
            self.assertEqual(entry["record_id"], "4662244000012128236")
            self.assertIn(entry["row_id"], {"row-A", "row-B"})

        # Zoho status was updated exactly once for the record (not once per item).
        status_updates = [
            u for u in zoho_updates
            if u["fields"].get("Request_Status") == self.config.STATUS_IN_PROGRESS
        ]
        self.assertEqual(len(status_updates), 1)

    def test_poll_once_skips_record_with_no_subform_items(self):
        """The original failing record symptom: no subform => marked invalid, no Telegram."""
        bad_record = {
            "ID": "rec-empty",
            "Type_of_Request": "Actual Photo",
            "Request_Status": "Pending",
            "Remarks_Notes": self.config.TRIGGER_TEXT,
            "Product_Name": "",
            # No Product_Name1 at all.
        }

        s = self.state.load()
        self.state.set_chat_id(s, 12345)
        self.state.save(s)
        state = self.state.load()

        sent: list = []

        with mock.patch.object(self.run.zoho, "get_pending_records", return_value=[bad_record]), \
             mock.patch.object(self.run.zoho, "update_record"), \
             mock.patch.object(self.run.akeneo, "lookup_product",
                               return_value=("", None, False, None)), \
             mock.patch.object(self.run.bot, "send_photo_request",
                               side_effect=lambda *a, **kw: sent.append(a) or 1):
            self.run._poll_once(state)

        self.assertEqual(sent, [])
        self.assertEqual(state["pending"], {})
        self.assertIn("rec-empty", state.get("invalid_records", {}))

    def test_finalize_only_marks_done_after_last_subform_item(self):
        """Reply for one item must NOT flip Request_Status to Done if siblings remain."""
        if "bot" in sys.modules:
            del sys.modules["bot"]
        import bot
        self.bot = bot

        s = self.state.load()
        # Two pending items for the same record.
        self.state.add_pending(
            s, "rec-multi", "Lamp A", "", "SKU-A", 1001,
            sent_time="2026-01-01T00:00:00",
            request_type="Actual Photo", row_id="row-A",
        )
        self.state.add_pending(
            s, "rec-multi", "Lamp B", "", "SKU-B", 1002,
            sent_time="2026-01-01T00:00:00",
            request_type="Actual Photo", row_id="row-B",
        )

        zoho_updates: list[dict] = []

        def fake_update_record(record_id, fields):
            zoho_updates.append({"record_id": record_id, "fields": dict(fields)})

        with mock.patch.object(self.bot.zoho, "update_record", side_effect=fake_update_record), \
             mock.patch.object(self.bot, "_send_text"), \
             mock.patch.object(self.bot.llm, "translate_to_english", return_value="ok"), \
             mock.patch.object(self.bot.llm, "classify_unavailable", return_value=False):
            # First reply for row-A — should NOT mark Done (row-B still pending).
            self.bot._handle_text_reply(
                "ok thanks", "rec-multi::row-A", "Lamp A", "SKU-A", 1234, s,
            )

        status_updates_after_first = [
            u["fields"].get("Request_Status")
            for u in zoho_updates if "Request_Status" in u["fields"]
        ]
        self.assertEqual(
            status_updates_after_first, [],
            "Done must NOT be sent to Zoho while another subform item is pending",
        )
        # row-A removed, row-B still pending.
        self.assertNotIn("rec-multi::row-A", s["pending"])
        self.assertIn("rec-multi::row-B", s["pending"])

        zoho_updates.clear()

        # Second reply for row-B — last item, NOW it should mark Done.
        with mock.patch.object(self.bot.zoho, "update_record", side_effect=fake_update_record), \
             mock.patch.object(self.bot, "_send_text"), \
             mock.patch.object(self.bot.llm, "translate_to_english", return_value="ok"), \
             mock.patch.object(self.bot.llm, "classify_unavailable", return_value=False):
            self.bot._handle_text_reply(
                "ok thanks", "rec-multi::row-B", "Lamp B", "SKU-B", 1234, s,
            )

        status_updates_after_last = [
            u["fields"].get("Request_Status")
            for u in zoho_updates if "Request_Status" in u["fields"]
        ]
        self.assertEqual(status_updates_after_last, [self.config.STATUS_DONE])
        self.assertNotIn("rec-multi::row-B", s["pending"])


class StateCompositeKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config, self.state = _reload_modules(
            str(Path(self.tmpdir.name) / "state.json")
        )

    def test_make_and_parse_item_key_roundtrip(self):
        key = self.state.make_item_key("rec-1", "row-A")
        self.assertEqual(key, "rec-1::row-A")
        self.assertEqual(self.state.parse_item_key(key), ("rec-1", "row-A"))

    def test_make_item_key_without_row_id_returns_bare_record_id(self):
        self.assertEqual(self.state.make_item_key("rec-1"), "rec-1")
        self.assertEqual(self.state.make_item_key("rec-1", ""), "rec-1")
        self.assertEqual(self.state.parse_item_key("rec-1"), ("rec-1", ""))

    def test_pending_items_for_record_finds_all_rows(self):
        s = self.state.load()
        self.state.add_pending(
            s, "rec-1", "A", "", "SKU-A", 100,
            sent_time="t", request_type="Actual Photo", row_id="row-A",
        )
        self.state.add_pending(
            s, "rec-1", "B", "", "SKU-B", 101,
            sent_time="t", request_type="Actual Photo", row_id="row-B",
        )
        self.state.add_pending(
            s, "rec-2", "C", "", "SKU-C", 102,
            sent_time="t", request_type="Actual Photo", row_id="row-C",
        )

        keys_for_rec1 = [k for k, _ in self.state.pending_items_for_record(s, "rec-1")]
        self.assertEqual(sorted(keys_for_rec1), ["rec-1::row-A", "rec-1::row-B"])

        self.assertTrue(self.state.record_has_pending_items(s, "rec-1"))
        self.assertTrue(self.state.record_has_pending_items(s, "rec-2"))

        # Remove rec-1's items — should no longer be pending.
        self.state.remove_pending(s, "rec-1::row-A")
        self.state.remove_pending(s, "rec-1::row-B")
        self.assertFalse(self.state.record_has_pending_items(s, "rec-1"))
        self.assertTrue(self.state.record_has_pending_items(s, "rec-2"))


if __name__ == "__main__":
    unittest.main()
