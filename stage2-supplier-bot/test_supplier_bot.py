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
    def __init__(self, data, ok=True, status=200, text="", headers=None):
        self._data = data
        self.ok = ok
        self.status_code = status
        self.text = text
        self.headers = headers or {}
        self.content = b""

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


class AkeneoSlotSelectionTests(unittest.TestCase):
    """upload_actual_photo must pick the first empty actual-photo slot."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        for mod_name in ("akeneo", "config"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        import importlib
        self.config = importlib.import_module("config")
        self.akeneo = importlib.import_module("akeneo")

    def _make_product(self, filled_slots: list[str]) -> dict:
        return {
            "values": {
                slot: [{"data": "media-code-xxx", "scope": None, "locale": None}]
                for slot in filled_slots
            },
        }

    def test_picks_first_slot_when_all_empty(self):
        slots = ["Actual_Photo", "another_picture_5", "another_picture_6"]
        with mock.patch.object(self.akeneo, "get_product_by_identifier",
                               return_value=self._make_product([])):
            self.assertEqual(
                self.akeneo._first_empty_actual_photo_slot("sku-1", slots),
                "Actual_Photo",
            )

    def test_picks_next_empty_slot(self):
        slots = ["Actual_Photo", "another_picture_5", "another_picture_6"]
        with mock.patch.object(self.akeneo, "get_product_by_identifier",
                               return_value=self._make_product(["Actual_Photo"])):
            self.assertEqual(
                self.akeneo._first_empty_actual_photo_slot("sku-1", slots),
                "another_picture_5",
            )

    def test_returns_none_when_all_filled(self):
        slots = ["Actual_Photo", "another_picture_5", "another_picture_6"]
        with mock.patch.object(self.akeneo, "get_product_by_identifier",
                               return_value=self._make_product(slots)):
            self.assertIsNone(
                self.akeneo._first_empty_actual_photo_slot("sku-1", slots),
            )

    def test_upload_actual_photo_sends_to_first_empty_slot(self):
        """Verify upload_actual_photo posts to Akeneo with the right slot name."""
        captured: dict = {}

        def fake_post(url, headers=None, files=None, timeout=None):
            # Pull out the JSON product blob that names the attribute.
            blob = files["product"][1]
            captured["url"] = url
            captured["attribute"] = __import__("json").loads(blob)["attribute"]
            return _FakeResp({"code": "media-abc"}, ok=True, status=201)

        slots = ["Actual_Photo", "another_picture_5", "another_picture_6"]
        with mock.patch.object(self.akeneo, "_authenticate", return_value="tok"), \
             mock.patch.object(self.akeneo, "get_product_by_identifier",
                               return_value=self._make_product(["Actual_Photo"])), \
             mock.patch.object(self.akeneo._session, "post", side_effect=fake_post), \
             mock.patch.object(self.config, "AKENEO_PHOTO_ATTRIBUTES", slots):
            ok = self.akeneo.upload_actual_photo("sku-1", b"jpgbytes")

        self.assertTrue(ok)
        self.assertEqual(captured.get("attribute"), "another_picture_5")


if __name__ == "__main__":
    unittest.main()
