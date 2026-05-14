from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .auth import ZohoAuth
from .config import load_config
from .creator import ZohoCreator
from .helpers import (
    MatchResult,
    MediaCandidate,
    build_search_terms,
    creator_criteria_value,
    extract_record_id,
    media_kind,
    scalar_to_text,
    unique_media,
)
from .workdrive import WorkDrive

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingOutcome:
    record_id: str
    product_name: str
    uploaded_count: int
    source: str
    note: str


class ActualPhotoAutomation:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_config()
        self.auth = ZohoAuth(self.config)
        self.creator = ZohoCreator(self.auth, self.config)
        self.workdrive = WorkDrive(self.auth, self.config)
        self.state_path = Path(self.config["processed_state_file"])
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"processed": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("State file is invalid JSON, starting with a fresh state")
            return {"processed": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8"
        )

    def debug_fields(self) -> dict[str, Any]:
        return self.creator.debug_fields(
            self.config["crm_app"],
            self.config["encoding_report"],
            form_link=self.config.get("encoding_form", ""),
        )

    def fetch_pending_requests(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        open_statuses = self.config.get("request_open_values", [])
        type_field = self.config["field_request_type"]
        actual_photo = creator_criteria_value(self.config["request_type_value"])
        type_criteria = f'({type_field} == "{actual_photo}")'

        if open_statuses:
            status_criteria = " || ".join(
                (
                    f'{self.config["field_request_status"]} == '
                    f'"{creator_criteria_value(status)}"'
                )
                for status in open_statuses
            )
            criteria = f'({type_criteria} && ({status_criteria}))'
        else:
            criteria = (
                f'({type_criteria}'
                f' && {self.config["field_request_status"]} != '
                f'"{creator_criteria_value(self.config["request_done_value"])}")'
            )
        return self.creator.get_records(
            self.config["crm_app"],
            self.config["encoding_report"],
            criteria=criteria,
            max_records=limit or int(self.config["max_pending_fetch"]),
        )

    def find_archive_match(self, product_name: str) -> MatchResult | None:
        search_terms = build_search_terms(product_name)
        query_text = search_terms.primary_keyword or product_name
        if not query_text:
            return None

        criteria = self.creator.build_archive_criteria(
            query_text, self.config["archive_approved_value"]
        )
        archive_records = self.creator.get_records(
            self.config["workflow_app"],
            self.config["archive_report"],
            criteria=criteria,
            max_records=20,
        )
        if not archive_records:
            return None

        scored: list[tuple[int, dict[str, Any]]] = []
        item_field = self.config["archive_item_name_field"]
        for record in archive_records:
            candidate_name = scalar_to_text(record.get(item_field))
            if not candidate_name:
                continue
            candidate_terms = build_search_terms(candidate_name)
            overlap = len(
                set(search_terms.significant_tokens or search_terms.tokens)
                & set(candidate_terms.tokens)
            )
            score = overlap * 10
            if search_terms.normalized and search_terms.normalized in candidate_terms.normalized:
                score += 50
            if search_terms.primary_keyword and search_terms.primary_keyword in candidate_terms.tokens:
                score += 20
            if score > 0:
                scored.append((score, record))

        if not scored:
            return None

        scored.sort(key=lambda entry: entry[0], reverse=True)
        _, best_record = scored[0]
        archive_record_id = extract_record_id(best_record)
        media_fields = self.config["archive_media_fields"]
        media: list[MediaCandidate] = []

        for field_name in media_fields:
            raw_value = best_record.get(field_name)
            if not raw_value:
                continue
            if isinstance(raw_value, list):
                for file_path in raw_value:
                    file_path_str = str(file_path).strip()
                    if not file_path_str:
                        continue
                    # Extract filepath param value for download
                    if "filepath=" in file_path_str:
                        fp = file_path_str.rsplit("filepath=", 1)[-1]
                    else:
                        fp = file_path_str.rsplit("/", 1)[-1]
                    media.append(
                        MediaCandidate(
                            source="archive",
                            identifier=f"{archive_record_id}:{field_name}:{fp}",
                            name=fp,
                            record_id=archive_record_id,
                            field_name=field_name,
                        )
                    )
            else:
                raw_str = scalar_to_text(raw_value)
                if raw_str:
                    # Extract filename from API path if present
                    if "filepath=" in raw_str:
                        fname = raw_str.rsplit("filepath=", 1)[-1]
                    elif "/" in raw_str:
                        fname = raw_str.rsplit("/", 1)[-1]
                    else:
                        fname = raw_str
                    media.append(
                        MediaCandidate(
                            source="archive",
                            identifier=f"{archive_record_id}:{field_name}",
                            name=fname,
                            record_id=archive_record_id,
                            field_name=field_name,
                        )
                    )

        if not media:
            return None

        return MatchResult(
            source="archive",
            matched_name=scalar_to_text(best_record.get(item_field)),
            media=tuple(media),
            detail=f"Archive record {archive_record_id}",
        )

    def _download_candidate(self, candidate: MediaCandidate, target_dir: Path) -> Path:
        if candidate.source == "workdrive":
            destination = target_dir / candidate.name
            return self.workdrive.download_file_to_path(candidate.identifier, destination)

        if candidate.source == "archive" and candidate.record_id and candidate.field_name:
            destination = target_dir / candidate.name
            return self.creator.download_file_field_to_path(
                self.config["workflow_app"],
                self.config["archive_report"],
                candidate.record_id,
                candidate.field_name,
                destination,
                filepath=candidate.name,
            )

        raise RuntimeError(f"Unsupported media candidate: {candidate}")

    def _update_remarks_only(self, record_id: str, message: str) -> None:
        remarks_field = self.config.get("field_remarks", "").strip()
        if not remarks_field:
            logger.warning("Remarks field is not configured; skipping remark update")
            return
        self.creator.update_record(
            self.config["crm_app"],
            self.config["encoding_report"],
            record_id,
            {remarks_field: message},
        )

    def _mark_success(self, record_id: str, message: str, *, change_status: bool = True) -> None:
        data: dict[str, Any] = {
            self.config["field_remarks"]: message,
        }
        if change_status:
            data[self.config["field_request_status"]] = self.config["request_done_value"]
        self.creator.update_record(
            self.config["crm_app"],
            self.config["encoding_report"],
            record_id,
            data,
        )

    def _upload_target_field(self, filename: str, candidate: MediaCandidate | None = None) -> str:
        kind = media_kind(filename)
        
        # If filename doesn't have a known extension, guess based on source field name
        if not kind and candidate and candidate.field_name:
            field_lower = candidate.field_name.lower()
            if "image" in field_lower or "photo" in field_lower or "pic" in field_lower:
                kind = "image"
            elif "video" in field_lower or "vid" in field_lower or "mp4" in field_lower:
                kind = "video"

        if kind == "image":
            return (
                self.config.get("field_image_media", "").strip()
                or self.config.get("field_actual_media", "").strip()
            )
        if kind == "video":
            return (
                self.config.get("field_video_media", "").strip()
                or self.config.get("field_actual_media", "").strip()
            )
        return self.config.get("field_actual_media", "").strip()

    def _is_already_processed(self, record_id: str) -> bool:
        """#2: Check state file to avoid re-processing records across restarts.
        For 'not_found' records, allow retry after not_found_retry_hours has passed.
        For successfully uploaded records, skip permanently.
        """
        entry = self.state.get("processed", {}).get(record_id)
        if not entry:
            return False

        # Successfully uploaded records are always skipped
        if entry.get("source") != "not_found":
            return True

        # "not_found" records: re-check after TTL expires
        retry_hours = float(self.config.get("not_found_retry_hours", 24))
        processed_at = entry.get("processed_at", "")
        if not processed_at:
            return True
        try:
            processed_time = datetime.strptime(processed_at, "%Y-%m-%d %H:%M:%S")
            if datetime.now() - processed_time > timedelta(hours=retry_hours):
                # TTL expired — remove from state so it gets re-checked
                logger.info(
                    "Not-found TTL expired for record %s (%s), will retry",
                    record_id, entry.get("product_name", ""),
                )
                del self.state["processed"][record_id]
                self._save_state()
                return False
        except (ValueError, KeyError):
            pass
        return True

    def _record_not_found(self, record_id: str, product_name: str, message: str, *, dry_run: bool = False) -> None:
        """#1: Update remarks and track 'not found' so we don't keep searching every cycle."""
        if dry_run:
            return
        if message:
            try:
                self._update_remarks_only(record_id, message)
            except Exception as exc:
                logger.warning("Failed to update not-found remarks for %s: %s", record_id, exc)
        self.state.setdefault("processed", {})[record_id] = {
            "product_name": product_name,
            "source": "not_found",
            "uploaded_count": 0,
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_state()

    def process_record(
        self, record: dict[str, Any], *, dry_run: bool = False
    ) -> ProcessingOutcome:
        record_id = extract_record_id(record)
        product_name = scalar_to_text(record.get(self.config["field_product_name"]))
        if not product_name:
            message = "Automation could not determine Product Name."
            logger.warning("%s Record=%s", message, record_id)
            return ProcessingOutcome(record_id, "", 0, "none", message)

        request_type = scalar_to_text(record.get(self.config["field_request_type"]))
        expected_type = scalar_to_text(self.config.get("request_type_value", ""))
        if request_type != expected_type:
            message = (
                f"Request type '{request_type or 'unknown'}' is not eligible for "
                f"automation; expected '{expected_type}'"
            )
            logger.info("%s (record=%s, product=%s)", message, record_id, product_name)
            return ProcessingOutcome(record_id, product_name, 0, "skip", message)

        # #2: Skip records already processed (tracked in state file)
        if self._is_already_processed(record_id):
            message = f"Already processed (in state file), skipping"
            logger.info("%s (record=%s, product=%s)", message, record_id, product_name)
            return ProcessingOutcome(record_id, product_name, 0, "skip", message)

        # Check if record already has an actual photo/video attached
        # Note: "Image_Attachment" is a separate field (product reference image),
        # NOT the actual photo/video — so we don't check it here.
        image_field = self.config.get("field_image_media", "").strip()
        video_field = self.config.get("field_video_media", "").strip()
        actual_field = self.config.get("field_actual_media", "").strip()
        for check_field in [image_field, video_field, actual_field]:
            if check_field and scalar_to_text(record.get(check_field)):
                message = f"Record already has attachment in '{check_field}', skipping"
                logger.info("%s (record=%s, product=%s)", message, record_id, product_name)
                return ProcessingOutcome(record_id, product_name, 0, "skip", message)

        search_terms = build_search_terms(product_name)
        all_media: list[MediaCandidate] = []
        sources: list[str] = []
        matched_name = ""

        workdrive_match = self.workdrive.find_best_media_match(
            self.config["workdrive_parent_folder_id"],
            search_terms,
            max_depth=int(self.config["workdrive_search_depth"]),
            parent_folder_ids=self.config.get("workdrive_parent_folder_ids") or None,
        )
        if workdrive_match and workdrive_match.media:
            all_media.extend(workdrive_match.media)
            sources.append("workdrive")
            matched_name = workdrive_match.matched_name
            logger.info("Found %d file(s) in WorkDrive for %s", len(workdrive_match.media), product_name)

        archive_match = self.find_archive_match(product_name)
        if archive_match and archive_match.media:
            all_media.extend(archive_match.media)
            sources.append("archive")
            if not matched_name:
                matched_name = archive_match.matched_name
            logger.info("Found %d file(s) in Archive for %s", len(archive_match.media), product_name)

        all_media = unique_media(all_media)
        source_label = "+".join(sources) if sources else "none"

        # Generate custom remarks dynamically based on matched sources
        source_names_map = {"archive": "Kanban Notes", "workdrive": "Zoho Drive", "akeneo": "Akeneo"}
        display_sources = [source_names_map.get(s, s.title()) for s in sources]
        
        if len(display_sources) == 1:
            sources_str = display_sources[0]
            success_msg = f"Automated retrieval of actual photos/videos from {sources_str}. Please check attachment if accurate"
        elif len(display_sources) > 1:
            sources_str = " and ".join([", ".join(display_sources[:-1]), display_sources[-1]]) if len(display_sources) > 2 else " and ".join(display_sources)
            success_msg = f"Automated Retrieval of Actual Photos/Videos from {sources_str}. Please check attachment if accurate"
        else:
            success_msg = ""

        not_found_msg = "Not available from Kanban Notes / Zoho Drive / Akeneo. Waiting for actual photos and videos from the Supplier"

        if not all_media:
            logger.info("No matching photo/video found for %s (record=%s)", product_name, record_id)
            # #1: Update remarks and track so we don't loop on this record
            self._record_not_found(record_id, product_name, not_found_msg, dry_run=dry_run)
            return ProcessingOutcome(record_id, product_name, 0, "none", not_found_msg)

        # #4: Limit max files per record to avoid uploading too many
        max_files = int(self.config.get("max_upload_files_per_record", 15))
        if len(all_media) > max_files:
            logger.info(
                "Limiting upload from %d to %d files for %s",
                len(all_media), max_files, product_name,
            )
            all_media = all_media[:max_files]

        if dry_run:
            note = (
                f"Dry-run matched {len(all_media)} file(s) from "
                f"{source_label}: {matched_name}"
            )
            logger.info("%s", note)
            return ProcessingOutcome(record_id, product_name, 0, source_label, note)

        with TemporaryDirectory(prefix="actual-photo-automation-") as temp_dir:
            temp_path = Path(temp_dir)
            uploaded_count = 0
            failed_count = 0
            for index, candidate in enumerate(all_media, start=1):
                try:
                    download_path = self._download_candidate(candidate, temp_path)
                except Exception as exc:
                    logger.error(
                        "Failed to download %s for record %s: %s",
                        candidate.name, record_id, exc,
                    )
                    failed_count += 1
                    continue

                if not download_path.name:
                    download_path = download_path.with_name(f"asset-{index}.bin")

                # Validate downloaded file before uploading
                if not download_path.exists() or download_path.stat().st_size == 0:
                    logger.error(
                        "Downloaded file is missing or empty: %s (candidate=%s, source=%s)",
                        download_path, candidate.name, candidate.source,
                    )
                    failed_count += 1
                    continue

                logger.info(
                    "Uploading %s (%d bytes) for record %s from %s",
                    download_path.name,
                    download_path.stat().st_size,
                    record_id,
                    candidate.source,
                )
                target_field = self._upload_target_field(download_path.name, candidate=candidate)
                if not target_field:
                    logger.error(
                        "No configured upload field for media file %s", download_path.name
                    )
                    failed_count += 1
                    continue

                try:
                    self.creator.upload_file_to_record(
                        self.config["crm_app"],
                        self.config["encoding_report"],
                        record_id,
                        target_field,
                        download_path,
                        upload_name=candidate.name or download_path.name,
                    )
                    uploaded_count += 1
                except Exception as exc:
                    logger.error(
                        "Failed to upload %s to field '%s' for record %s: %s",
                        download_path.name, target_field, record_id, exc,
                    )
                    failed_count += 1

        if uploaded_count <= 0:
            message = (
                f"No files were successfully uploaded for {product_name}"
                f" ({failed_count} failed download/upload)"
            )
            logger.warning("%s (record=%s)", message, record_id)
            self._update_remarks_only(record_id, message)
            return ProcessingOutcome(record_id, product_name, 0, source_label, message)

        if failed_count > 0:
            logger.warning(
                "Partial upload for record %s: %d succeeded, %d failed",
                record_id, uploaded_count, failed_count,
            )

        # #5: Mark success — for both Actual Photo and Specifications, update status to Done
        # Actual Photo requests remain the only eligible request type here.
        fallback_msg = self.config.get("success_remarks", "This is uploaded by Automated")
        self._mark_success(record_id, success_msg or fallback_msg, change_status=True)
        self.state.setdefault("processed", {})[record_id] = {
            "product_name": product_name,
            "source": source_label,
            "uploaded_count": uploaded_count,
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_state()
        note = f"Uploaded {uploaded_count} file(s) from {source_label}: {matched_name}"
        return ProcessingOutcome(record_id, product_name, uploaded_count, source_label, note)

    def process_pending_records(
        self,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        product_filter: str = "",
    ) -> list[ProcessingOutcome]:
        records = self.fetch_pending_requests(limit=limit)
        logger.info("Fetched %s pending Actual Photo request(s)", len(records))
        if product_filter:
            filter_lower = product_filter.lower()
            records = [
                r for r in records
                if filter_lower in scalar_to_text(r.get(self.config["field_product_name"])).lower()
            ]
            logger.info("Filtered to %s record(s) matching '%s'", len(records), product_filter)
        # Filter out already-processed records early to save API calls
        unprocessed = []
        skipped_count = 0
        for r in records:
            rid = extract_record_id(r)
            if self._is_already_processed(rid):
                skipped_count += 1
            else:
                unprocessed.append(r)
        if skipped_count:
            logger.info("Skipped %d already-processed record(s) from state file", skipped_count)
        records = unprocessed

        outcomes: list[ProcessingOutcome] = []
        for record in records:
            record_id = extract_record_id(record)
            try:
                outcome = self.process_record(record, dry_run=dry_run)
                outcomes.append(outcome)
            except Exception as exc:
                message = f"Automation error: {exc}"
                logger.exception("Failed to process record %s", record_id)
                outcomes.append(
                    ProcessingOutcome(record_id, "", 0, "error", message)
                )
        return outcomes

    def run_forever(self) -> None:
        poll_interval = float(self.config["poll_interval_seconds"])
        max_consecutive_errors = 5
        consecutive_errors = 0
        logger.info("Starting Actual Photo polling loop every %s second(s)", poll_interval)
        while True:
            try:
                self.process_pending_records(dry_run=False)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                logger.exception(
                    "Polling cycle failed (%d/%d consecutive errors): %s",
                    consecutive_errors, max_consecutive_errors, exc,
                )
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        "Too many consecutive errors (%d), stopping polling loop",
                        consecutive_errors,
                    )
                    raise
                # Back off longer on repeated failures
                backoff = poll_interval * consecutive_errors
                logger.info("Backing off for %s seconds before next attempt", backoff)
                time.sleep(backoff)
                continue
            time.sleep(poll_interval)
