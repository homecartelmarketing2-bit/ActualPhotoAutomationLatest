from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import requests

logger = logging.getLogger(__name__)


class ZohoAuth:
    def __init__(self, config: dict[str, Any], session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self.access_token: str | None = None
        self.expires_at = 0.0
        self.timeout = float(config.get("request_timeout_seconds", 60))

    def refresh_access_token(self, force: bool = False) -> str:
        if self.access_token and not force and time.time() < self.expires_at - 60:
            return self.access_token

        payload = {
            "client_id": self.config["zoho_client_id"],
            "client_secret": self.config["zoho_client_secret"],
            "refresh_token": self.config["zoho_refresh_token"],
            "grant_type": "refresh_token",
        }
        response = self.session.post(
            self.config["zoho_token_url"], data=payload, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise RuntimeError(f"Zoho token refresh failed: {data}")

        expires_in = int(data.get("expires_in", 3600))
        self.access_token = access_token
        self.expires_at = time.time() + expires_in
        return access_token

    def build_headers(
        self,
        extra_headers: Mapping[str, str] | None = None,
        accept_json: bool = True,
    ) -> dict[str, str]:
        headers = {"Authorization": f"Zoho-oauthtoken {self.refresh_access_token()}"}
        if accept_json:
            headers["Accept"] = "application/json"
        if extra_headers:
            headers.update(dict(extra_headers))
        return headers

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        retry_on_401: bool = True,
        accept_json: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        request_headers = self.build_headers(headers, accept_json=accept_json)
        timeout = kwargs.pop("timeout", self.timeout)
        response = self.session.request(
            method=method,
            url=url,
            headers=request_headers,
            timeout=timeout,
            **kwargs,
        )
        if response.status_code == 401 and retry_on_401:
            logger.info("Zoho access token expired, refreshing and retrying request")
            self.refresh_access_token(force=True)
            request_headers = self.build_headers(headers, accept_json=accept_json)
            response = self.session.request(
                method=method,
                url=url,
                headers=request_headers,
                timeout=timeout,
                **kwargs,
            )
        return response
