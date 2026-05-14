from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .auth import ZohoAuth
from .helpers import (
    MatchResult,
    MediaCandidate,
    SearchTerms,
    candidate_score,
    is_supported_media,
    unique_media,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkDriveItem:
    item_id: str
    name: str
    item_type: str
    parent_id: str = ""
    path: str = ""
    modified_at: str = ""


class WorkDrive:
    def __init__(self, auth: ZohoAuth, config: dict[str, Any]):
        self.auth = auth
        self.config = config
        self.base_url = config["workdrive_base_url"].rstrip("/")
        self.download_base_url = config["workdrive_download_base_url"].rstrip("/")
        self.throttle_seconds = float(config.get("api_throttle_seconds", 0.5))
        self.page_size = int(config.get("workdrive_list_page_size", 50))
        self.max_pages = int(config.get("workdrive_max_pages", 10))
        self.search_result_limit = int(config.get("workdrive_search_result_limit", 25))
        self.global_search_enabled = bool(
            config.get("workdrive_global_search_enabled", True)
        )
        self._team_id: str | None = config.get("workdrive_team_id") or None

    def _sleep(self) -> None:
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)

    def _request(self, method: str, url: str, **kwargs: Any):
        self._sleep()
        headers = kwargs.pop("headers", {})
        accept_json = kwargs.pop("accept_json", True)
        if accept_json:
            headers.setdefault("Accept", "application/vnd.api+json")
        last_exc = None
        for attempt in range(3):
            try:
                return self.auth.request(
                    method, url, headers=dict(headers), accept_json=accept_json, **kwargs
                )
            except Exception as exc:
                last_exc = exc
                wait = (attempt + 1) * 2
                logger.warning(
                    "WorkDrive request failed (attempt %d/3), retrying in %ds: %s",
                    attempt + 1, wait, exc,
                )
                time.sleep(wait)
        raise last_exc

    def _folder_files_url(self, folder_id: str) -> str:
        return f"{self.base_url}/files/{folder_id}/files"

    def _download_url(self, file_id: str) -> str:
        return f"{self.download_base_url}/{file_id}"

    def _team_records_url(self, team_id: str) -> str:
        return f"{self.base_url}/teams/{team_id}/records"

    @staticmethod
    def _filename_from_response(response) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
        if not match:
            return ""
        return match.group(1).strip().strip('"')

    def _parse_item(self, payload: dict[str, Any]) -> WorkDriveItem | None:
        item_id = str(payload.get("id", "")).strip()
        attributes = payload.get("attributes", {}) if isinstance(payload, dict) else {}
        name = str(attributes.get("name") or payload.get("name") or "").strip()
        raw_type = str(attributes.get("type") or payload.get("type") or "").lower()
        parent_id = str(attributes.get("parent_id") or "").strip()
        modified_at = str(
            attributes.get("modified_time")
            or attributes.get("updated_time")
            or attributes.get("last_modified_time")
            or ""
        ).strip()
        if not item_id or not name:
            return None
        if "folder" in raw_type:
            item_type = "folder"
        elif raw_type in {"file", "files"}:
            item_type = "file"
        elif is_supported_media(name):
            item_type = "file"
        else:
            item_type = "unknown"
        return WorkDriveItem(
            item_id=item_id,
            name=name,
            item_type=item_type,
            parent_id=parent_id,
            path=name,
            modified_at=modified_at,
        )

    def _search_result_to_item(self, payload: dict[str, Any]) -> WorkDriveItem | None:
        return self._parse_item(payload)

    def get_preferred_team_id(self) -> str:
        if self._team_id:
            return self._team_id
        response = self._request("GET", f"{self.base_url}/users/me")
        response.raise_for_status()
        payload = response.json()
        self._team_id = str(
            payload.get("data", {}).get("attributes", {}).get("preferred_team_id", "")
        ).strip() or None
        if not self._team_id:
            raise RuntimeError("WorkDrive preferred_team_id was not available")
        return self._team_id

    def search_team_items(self, keyword: str) -> list[WorkDriveItem]:
        if not self.global_search_enabled:
            return []
        keyword = keyword.strip()
        if not keyword:
            return []

        team_id = self.get_preferred_team_id()
        url = self._team_records_url(team_id)
        params = {
            "search[all]": keyword,
            "page[limit]": min(self.search_result_limit, 50),
            "page[offset]": 0,
        }
        response = self._request("GET", url, params=params)
        if response.status_code >= 400:
            error_preview = response.text[:500]
            logger.warning(
                "WorkDrive global search unavailable for keyword '%s' (status=%s): %s",
                keyword,
                response.status_code,
                error_preview,
            )
            return []

        payload = response.json()
        data = payload.get("data", [])
        if not isinstance(data, list):
            logger.warning(
                "Unexpected WorkDrive global search payload for keyword '%s': %s",
                keyword,
                payload,
            )
            return []

        items: list[WorkDriveItem] = []
        for row in data:
            item = self._search_result_to_item(row)
            if item:
                items.append(item)
        return items

    def list_folder_items(self, folder_id: str) -> list[WorkDriveItem]:
        url = self._folder_files_url(folder_id)
        items: list[WorkDriveItem] = []

        for page_index in range(self.max_pages):
            params = {
                "page[limit]": self.page_size,
                "page[offset]": page_index * self.page_size,
            }
            response = self._request("GET", url, params=params)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", [])
            if not isinstance(data, list) or not data:
                break

            batch_count = 0
            for row in data:
                item = self._parse_item(row)
                if item:
                    items.append(item)
                    batch_count += 1

            if batch_count < self.page_size:
                break

        return items

    def walk_folder_items(
        self,
        folder_id: str,
        *,
        max_depth: int,
        current_depth: int = 0,
        parent_path: str = "",
    ) -> list[WorkDriveItem]:
        items = self.list_folder_items(folder_id)
        walked: list[WorkDriveItem] = []

        for item in items:
            current_path = f"{parent_path}/{item.name}" if parent_path else item.name
            item_with_path = WorkDriveItem(
                item_id=item.item_id,
                name=item.name,
                item_type=item.item_type,
                parent_id=item.parent_id,
                path=current_path,
                modified_at=item.modified_at,
            )
            walked.append(item_with_path)

            if item.item_type == "folder" and current_depth < max_depth:
                walked.extend(
                    self.walk_folder_items(
                        item.item_id,
                        max_depth=max_depth,
                        current_depth=current_depth + 1,
                        parent_path=current_path,
                    )
                )

        return walked

    def _collect_media_from_folder(
        self, folder_id: str, *, depth: int, max_depth: int
    ) -> list[MediaCandidate]:
        media: list[MediaCandidate] = []
        items = self.list_folder_items(folder_id)
        for item in items:
            if item.item_type == "file" and is_supported_media(item.name):
                media.append(
                    MediaCandidate(
                        source="workdrive",
                        identifier=item.item_id,
                        name=item.name,
                    )
                )
            elif item.item_type == "folder" and depth < max_depth:
                media.extend(
                    self._collect_media_from_folder(
                        item.item_id, depth=depth + 1, max_depth=max_depth
                    )
                )
        return media

    def _targeted_folder_search(
        self,
        parent_folder_ids: list[str],
        search_terms: SearchTerms,
        *,
        max_depth: int = 2,
    ) -> list[WorkDriveItem]:
        """Search smartly: list top-level items, only descend into matching folders."""
        matched_items: list[WorkDriveItem] = []

        for folder_id in parent_folder_ids:
            logger.info("Listing top-level items in folder %s", folder_id)
            try:
                top_items = self.list_folder_items(folder_id)
            except Exception as exc:
                logger.warning("Failed to list folder %s: %s", folder_id, exc)
                continue

            for item in top_items:
                score = candidate_score(item.name, search_terms)
                if item.item_type == "file" and is_supported_media(item.name) and score > 0:
                    matched_items.append(item)
                elif item.item_type == "folder" and score > 0:
                    matched_items.append(item)
                elif item.item_type == "folder" and max_depth > 0:
                    # No name match at this level - check one level deeper
                    try:
                        sub_items = self.list_folder_items(item.item_id)
                    except Exception as exc:
                        logger.warning("Failed to list subfolder %s: %s", item.name, exc)
                        continue
                    for sub in sub_items:
                        sub_with_path = WorkDriveItem(
                            item_id=sub.item_id,
                            name=sub.name,
                            item_type=sub.item_type,
                            parent_id=sub.parent_id,
                            path=f"{item.name}/{sub.name}",
                            modified_at=sub.modified_at,
                        )
                        sub_score = candidate_score(sub.name, search_terms)
                        if sub_score > 0:
                            matched_items.append(sub_with_path)

        return matched_items

    def find_best_media_match(
        self,
        parent_folder_id: str,
        search_terms: SearchTerms,
        *,
        max_depth: int = 2,
        parent_folder_ids: list[str] | None = None,
    ) -> MatchResult | None:
        search_pool: list[WorkDriveItem] = []
        keyword = search_terms.primary_keyword or search_terms.normalized
        if keyword:
            search_pool.extend(self.search_team_items(keyword))

        if not search_pool:
            logger.info(
                "Global search unavailable for '%s'; using targeted folder search",
                keyword or search_terms.original,
            )
            folders_to_search = parent_folder_ids or [parent_folder_id]
            search_pool = self._targeted_folder_search(
                folders_to_search, search_terms, max_depth=max_depth
            )
            if not search_pool:
                return None

        scored: list[tuple[int, WorkDriveItem]] = []
        for item in search_pool:
            score = max(
                candidate_score(item.name, search_terms),
                candidate_score(item.path, search_terms),
            )
            if score > 0:
                scored.append((score, item))
        if not scored:
            return None

        scored.sort(
            key=lambda entry: (
                entry[0],
                1 if entry[1].item_type == "folder" else 0,
                len(entry[1].name),
            ),
            reverse=True,
        )
        best_score, best_item = scored[0]
        min_score = int(self.config.get("workdrive_min_match_score", 30))
        if best_score < min_score:
            logger.info(
                "Best match '%s' score=%d is below threshold %d, skipping",
                best_item.name, best_score, min_score,
            )
            return None

        if best_item.item_type == "folder":
            media = unique_media(
                self._collect_media_from_folder(
                    best_item.item_id, depth=0, max_depth=max_depth
                )
            )
            if not media:
                return None
            return MatchResult(
                source="workdrive",
                matched_name=best_item.name,
                media=tuple(media),
                detail=f"Best folder match score={best_score} path={best_item.path}",
            )

        matching_files = [
            MediaCandidate(source="workdrive", identifier=item.item_id, name=item.name)
            for score, item in scored
            if item.item_type == "file"
            and is_supported_media(item.name)
            and score >= max(best_score - 10, 1)
        ]
        matching_files = unique_media(matching_files)
        if not matching_files:
            return None

        return MatchResult(
            source="workdrive",
            matched_name=best_item.name,
            media=tuple(matching_files),
            detail=f"Best file match score={best_score} path={best_item.path}",
        )

    def download_file_to_path(self, file_id: str, destination: Path) -> Path:
        url = self._download_url(file_id)
        response = self._request("GET", url, stream=True, accept_json=False)
        response.raise_for_status()
        filename = self._filename_from_response(response) or destination.name
        final_path = destination.with_name(filename)
        with final_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        return final_path

    def list_video_files(
        self, folder_ids: list[str], *, max_depth: int = 4
    ) -> list[WorkDriveItem]:
        files: list[WorkDriveItem] = []
        seen_ids: set[str] = set()

        def walk(folder_id: str, *, parent_path: str, depth: int) -> None:
            for item in self.list_folder_items(folder_id):
                current_path = f"{parent_path}/{item.name}" if parent_path else item.name
                item_with_path = WorkDriveItem(
                    item_id=item.item_id,
                    name=item.name,
                    item_type=item.item_type,
                    parent_id=item.parent_id,
                    path=current_path,
                    modified_at=item.modified_at,
                )
                if item.item_type == "file" and Path(item.name).suffix.lower() in {
                    ".mp4",
                    ".mov",
                    ".avi",
                }:
                    if item.item_id not in seen_ids:
                        seen_ids.add(item.item_id)
                        files.append(item_with_path)
                    continue
                if item.item_type == "folder" and depth < max_depth:
                    walk(item.item_id, parent_path=current_path, depth=depth + 1)

        for folder_id in folder_ids:
            walk(folder_id, parent_path="", depth=0)

        files.sort(
            key=lambda item: (
                item.modified_at or "",
                item.path.lower(),
            ),
            reverse=True,
        )
        return files

    def open_file_stream(self, file_id: str, *, range_header: str = ""):
        headers: dict[str, str] = {}
        if range_header:
            headers["Range"] = range_header
        response = self._request(
            "GET",
            self._download_url(file_id),
            stream=True,
            accept_json=False,
            headers=headers,
        )
        response.raise_for_status()
        return response
