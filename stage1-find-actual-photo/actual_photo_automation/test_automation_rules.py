from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actual_photo_automation.automation import ActualPhotoAutomation
from actual_photo_automation.helpers import MatchResult, MediaCandidate
from actual_photo_automation.upload_to_akeneo import process_records


BASE_CONFIG = {
    "archive_media_fields": ["Image_Upload", "Video_Upload"],
    "archive_report": "Archive_Report",
    "crm_app": "crm",
    "encoding_report": "All_Encoding_Requests",
    "field_actual_media": "",
    "field_image_media": "Actual_Photo1",
    "field_product_name": "Product_Name",
    "field_remarks": "Remarks_Notes",
    "field_request_status": "Request_Status",
    "field_request_type": "Type_of_Request",
    "field_video_media": "Video",
    "max_pending_fetch": 50,
    "max_upload_files_per_record": 15,
    "request_done_value": "Done",
    "request_open_values": ["Pending", "In progress"],
    "request_type_value": "Actual Photo",
    "success_remarks": "This is uploaded by Automated",
    "workdrive_parent_folder_id": "folder-1",
    "workdrive_parent_folder_ids": [],
    "workdrive_search_depth": 2,
    "workflow_app": "workflow-module-creation",
}


class CreatorSpy:
    def __init__(self, *, records: list[dict] | None = None, upload_error: Exception | None = None):
        self.records = list(records or [])
        self.upload_error = upload_error
        self.get_records_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.upload_calls: list[dict] = []

    def get_records(self, app_link: str, report_link: str, **kwargs):
        self.get_records_calls.append(
            {"app_link": app_link, "report_link": report_link, **kwargs}
        )
        return list(self.records)

    def update_record(self, app_link: str, report_link: str, record_id: str, data: dict):
        self.update_calls.append(
            {
                "app_link": app_link,
                "report_link": report_link,
                "record_id": record_id,
                "data": dict(data),
            }
        )
        return {"code": 3000, "message": "OK"}

    def upload_file_to_record(
        self,
        app_link: str,
        report_link: str,
        record_id: str,
        field_name: str,
        file_path: Path,
        *,
        upload_name: str | None = None,
    ):
        self.upload_calls.append(
            {
                "app_link": app_link,
                "report_link": report_link,
                "record_id": record_id,
                "field_name": field_name,
                "file_path": Path(file_path),
                "upload_name": upload_name,
            }
        )
        if self.upload_error is not None:
            raise self.upload_error
        return {"code": 3000, "message": "OK"}


class WorkDriveStub:
    def __init__(self, match: MatchResult | None = None):
        self.match = match
        self.calls: list[dict] = []

    def find_best_media_match(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.match


class AutomationRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    def _make_automation(
        self,
        *,
        creator: CreatorSpy | None = None,
        workdrive: WorkDriveStub | None = None,
    ) -> ActualPhotoAutomation:
        automation = object.__new__(ActualPhotoAutomation)
        automation.config = dict(BASE_CONFIG)
        automation.creator = creator or CreatorSpy()
        automation.workdrive = workdrive or WorkDriveStub()
        automation.state = {"processed": {}}
        automation.state_path = Path(self.tempdir.name) / "processed.json"
        automation.find_archive_match = lambda product_name: None
        automation._save_state = lambda: None

        def download_candidate(candidate: MediaCandidate, target_dir: Path) -> Path:
            destination = target_dir / candidate.name
            destination.write_bytes(b"ok")
            return destination

        automation._download_candidate = download_candidate
        return automation

    def test_fetch_pending_requests_uses_actual_photo_open_status_criteria(self) -> None:
        creator = CreatorSpy(records=[])
        automation = self._make_automation(creator=creator)

        automation.fetch_pending_requests(limit=5)

        self.assertEqual(len(creator.get_records_calls), 1)
        call = creator.get_records_calls[0]
        self.assertEqual(
            call["criteria"],
            '((Type_of_Request == "Actual Photo") && (Request_Status == "Pending" || Request_Status == "In progress"))',
        )
        self.assertEqual(call["max_records"], 5)

    def test_process_record_skips_non_actual_photo_requests(self) -> None:
        creator = CreatorSpy()
        workdrive = WorkDriveStub()
        automation = self._make_automation(creator=creator, workdrive=workdrive)
        record = {
            "ID": "1",
            "Product_Name": "Sample Lamp",
            "Type_of_Request": "Pricing",
            "Request_Status": "Pending",
            "Actual_Photo1": [],
            "Video": "",
        }

        outcome = automation.process_record(record)

        self.assertEqual(outcome.source, "skip")
        self.assertIn("not eligible", outcome.note)
        self.assertEqual(workdrive.calls, [])
        self.assertEqual(creator.upload_calls, [])
        self.assertEqual(creator.update_calls, [])

    def test_process_record_marks_successful_actual_photo_as_done(self) -> None:
        creator = CreatorSpy()
        match = MatchResult(
            source="workdrive",
            matched_name="Sample Lamp",
            media=(MediaCandidate(source="workdrive", identifier="file-1", name="sample.jpg"),),
        )
        automation = self._make_automation(
            creator=creator,
            workdrive=WorkDriveStub(match=match),
        )
        record = {
            "ID": "2",
            "Product_Name": "Sample Lamp",
            "Type_of_Request": "Actual Photo",
            "Request_Status": "Pending",
            "Actual_Photo1": [],
            "Video": "",
        }

        outcome = automation.process_record(record)

        self.assertEqual(outcome.uploaded_count, 1)
        self.assertEqual(len(creator.upload_calls), 1)
        self.assertEqual(len(creator.update_calls), 1)
        self.assertEqual(
            creator.update_calls[0]["data"]["Request_Status"],
            "Done",
        )
        self.assertIn("Automated retrieval of actual photos/videos", creator.update_calls[0]["data"]["Remarks_Notes"])

    def test_process_record_failed_upload_does_not_mark_done(self) -> None:
        creator = CreatorSpy(upload_error=RuntimeError("upload failed"))
        match = MatchResult(
            source="workdrive",
            matched_name="Sample Lamp",
            media=(MediaCandidate(source="workdrive", identifier="file-1", name="sample.jpg"),),
        )
        automation = self._make_automation(
            creator=creator,
            workdrive=WorkDriveStub(match=match),
        )
        record = {
            "ID": "3",
            "Product_Name": "Sample Lamp",
            "Type_of_Request": "Actual Photo",
            "Request_Status": "Pending",
            "Actual_Photo1": [],
            "Video": "",
        }

        outcome = automation.process_record(record)

        self.assertEqual(outcome.uploaded_count, 0)
        self.assertEqual(len(creator.upload_calls), 1)
        self.assertEqual(len(creator.update_calls), 1)
        self.assertNotIn("Request_Status", creator.update_calls[0]["data"])
        self.assertIn("No files were successfully uploaded", creator.update_calls[0]["data"]["Remarks_Notes"])


class UploadToAkeneoRulesTests(unittest.TestCase):
    def test_process_records_uses_done_and_actual_photo_criteria(self) -> None:
        creator = CreatorSpy(records=[])
        counts = process_records(
            creator=creator,
            akeneo=object(),
            config=dict(BASE_CONFIG),
            state={"uploaded": {}},
            state_path=Path(tempfile.gettempdir()) / "akeneo-state.json",
            report_path=Path(tempfile.gettempdir()) / "akeneo-report.csv",
            dry_run=True,
            photo_attributes=["Actual_Photo"],
        )

        self.assertEqual(counts["processed"], 0)
        self.assertEqual(len(creator.get_records_calls), 1)
        self.assertEqual(
            creator.get_records_calls[0]["criteria"],
            '(Request_Status == "Done" && Type_of_Request == "Actual Photo")',
        )

    def test_process_records_skips_non_actual_photo_records(self) -> None:
        creator = CreatorSpy(
            records=[
                {
                    "ID": "10",
                    "Product_Name": "Sample Lamp",
                    "Type_of_Request": "Pricing",
                    "Request_Status": "Done",
                    "Actual_Photo1": ["sample.jpg"],
                }
            ]
        )

        counts = process_records(
            creator=creator,
            akeneo=object(),
            config=dict(BASE_CONFIG),
            state={"uploaded": {}},
            state_path=Path(tempfile.gettempdir()) / "akeneo-state.json",
            report_path=Path(tempfile.gettempdir()) / "akeneo-report.csv",
            dry_run=True,
            photo_attributes=["Actual_Photo"],
        )

        self.assertEqual(counts["processed"], 0)
        self.assertEqual(counts["uploaded"], 0)
        self.assertEqual(counts["error"], 0)


if __name__ == "__main__":
    unittest.main()
