from __future__ import annotations

import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .auth import ZohoAuth
from .helpers import creator_criteria_value


class ZohoCreator:
    def __init__(self, auth: ZohoAuth, config: dict[str, Any]):
        self.auth = auth
        self.config = config
        self.base_url = config["zoho_creator_base_url"].rstrip("/")
        self.account = config["zoho_creator_account"]
        self.throttle_seconds = float(config.get("api_throttle_seconds", 0.5))

    def _sleep(self) -> None:
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)

    def _data_report_url(self, app_link: str, report_link: str) -> str:
        return f"{self.base_url}/data/{self.account}/{app_link}/report/{report_link}"

    def _record_url(self, app_link: str, report_link: str, record_id: str) -> str:
        return f"{self._data_report_url(app_link, report_link)}/{record_id}"

    def _upload_url(
        self, app_link: str, report_link: str, record_id: str, field_name: str
    ) -> str:
        return f"{self._record_url(app_link, report_link, record_id)}/{field_name}/upload"

    def _download_url(
        self, app_link: str, report_link: str, record_id: str, field_name: str
    ) -> str:
        return f"{self._record_url(app_link, report_link, record_id)}/{field_name}/download"

    def _form_fields_url(self, app_link: str, form_link: str) -> str:
        return f"{self.base_url}/meta/{self.account}/{app_link}/form/{form_link}/fields"

    def _request(self, method: str, url: str, **kwargs: Any):
        self._sleep()
        return self.auth.request(method, url, **kwargs)

    @staticmethod
    def _creator_batch_size(requested: int) -> int:
        if requested <= 200:
            return 200
        if requested <= 500:
            return 500
        return 1000

    @staticmethod
    def _filename_from_response(response) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
        if not match:
            return ""
        return match.group(1).strip().strip('"')

    def get_records(
        self,
        app_link: str,
        report_link: str,
        *,
        criteria: str | None = None,
        max_records: int = 200,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        url = self._data_report_url(app_link, report_link)
        params: dict[str, Any] = {
            "max_records": self._creator_batch_size(max_records)
        }
        if criteria:
            params["criteria"] = criteria
        if fields:
            params["field_config"] = "custom"
            params["fields"] = ",".join(fields)
        else:
            params["field_config"] = "all"

        records: list[dict[str, Any]] = []
        cursor: str | None = None

        while len(records) < max_records:
            headers: dict[str, str] = {}
            if cursor:
                headers["record_cursor"] = cursor

            response = self._request("GET", url, params=params, headers=headers)
            if response.status_code == 400:
                body = response.json()
                if body.get("code") == 9280:
                    break  # Zoho returns 400 when no records match criteria
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("data", [])
            if not isinstance(batch, list):
                raise RuntimeError(f"Unexpected Creator records response: {payload}")

            records.extend(batch)
            cursor = response.headers.get("record_cursor")
            if not cursor or not batch:
                break

        return records[:max_records]

    def get_form_fields(self, app_link: str, form_link: str) -> dict[str, Any]:
        url = self._form_fields_url(app_link, form_link)
        response = self._request("GET", url)
        response.raise_for_status()
        return response.json()

    def debug_fields(
        self,
        app_link: str,
        report_link: str,
        *,
        form_link: str = "",
        sample_limit: int = 1,
    ) -> dict[str, Any]:
        sample_records = self.get_records(
            app_link, report_link, max_records=max(sample_limit, 1)
        )
        result: dict[str, Any] = {
            "sample_record_count": len(sample_records),
            "sample_record_keys": sorted(sample_records[0].keys()) if sample_records else [],
            "sample_record": sample_records[0] if sample_records else {},
        }
        if form_link:
            try:
                result["form_fields"] = self.get_form_fields(app_link, form_link)
            except Exception as exc:
                result["form_fields_error"] = str(exc)
        else:
            result["form_fields_error"] = (
                "encoding_form is not configured; set it to fetch full field metadata."
            )
        return result

    def update_record(
        self,
        app_link: str,
        report_link: str,
        record_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        url = self._record_url(app_link, report_link, record_id)
        response = self._request("PATCH", url, json={"data": data})
        response.raise_for_status()
        return response.json()

    def upload_file_to_record(
        self,
        app_link: str,
        report_link: str,
        record_id: str,
        field_name: str,
        file_path: Path,
        *,
        upload_name: str | None = None,
    ) -> dict[str, Any]:
        url = self._upload_url(app_link, report_link, record_id, field_name)
        filename = upload_name or file_path.name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        # Validate file before uploading
        if not file_path.exists():
            raise RuntimeError(f"Download file does not exist: {file_path}")
        file_size = file_path.stat().st_size
        if file_size == 0:
            raise RuntimeError(f"Download file is empty (0 bytes): {file_path}")
        logger.info(
            "Uploading file '%s' (%d bytes, %s) to field '%s' for record %s",
            filename, file_size, content_type, field_name, record_id,
        )

        with file_path.open("rb") as handle:
            files = {"file": (filename, handle, content_type)}
            response = self._request("POST", url, files=files)
        response.raise_for_status()
        result = response.json()

        # Validate Zoho Creator response — code 3000 means success
        resp_code = result.get("code")
        if resp_code and int(resp_code) != 3000:
            logger.error(
                "Zoho Creator upload failed for record %s field '%s': %s",
                record_id, field_name, result,
            )
            raise RuntimeError(
                f"Zoho Creator upload rejected: code={resp_code}, "
                f"message={result.get('message', 'unknown')}"
            )
        logger.info(
            "Upload confirmed for record %s field '%s': %s",
            record_id, field_name, result.get("message", "OK"),
        )
        return result

    def download_file_field_to_path(
        self,
        app_link: str,
        report_link: str,
        record_id: str,
        field_name: str,
        destination: Path,
        *,
        filepath: str | None = None,
    ) -> Path:
        url = self._download_url(app_link, report_link, record_id, field_name)
        params = {}
        if filepath:
            params["filepath"] = filepath
        response = self._request("GET", url, params=params, stream=True, accept_json=False)
        response.raise_for_status()
        raw_filename = self._filename_from_response(response) or destination.name
        # Strip any path components - keep only the filename
        filename = raw_filename.rsplit("/", 1)[-1].lstrip("/")
        
        # If the filename lacks an extension but we know the content type, append it
        if "." not in filename:
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
            if content_type:
                import mimetypes
                ext = mimetypes.guess_extension(content_type)
                if ext:
                    # Sometimes Windows mimetypes returns .jpe instead of .jpg
                    if ext == ".jpe":
                        ext = ".jpg"
                    if ext == ".htm":
                        ext = "" # ignore htm for images mostly
                    filename = f"{filename}{ext}"

        final_path = destination.with_name(filename)
        with final_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        return final_path

    def build_archive_criteria(self, item_name_value: str, status_value: str) -> str:
        item_field = self.config["archive_item_name_field"]
        status_field = self.config["archive_status_field"]
        escaped_name = creator_criteria_value(item_name_value)
        escaped_status = creator_criteria_value(status_value)
        return (
            f'({item_field}.contains("{escaped_name}")'
            f' && {status_field} == "{escaped_status}")'
        )
