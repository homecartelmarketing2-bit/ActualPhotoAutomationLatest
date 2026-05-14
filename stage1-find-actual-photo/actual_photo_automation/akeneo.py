from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


class AkeneoClient:
    def __init__(
        self,
        host: str,
        client_id: str,
        secret: str,
        username: str,
        password: str,
        channel: str = "home_cartel",
        session: requests.Session | None = None,
        timeout: float = 60,
    ):
        self.host = host.rstrip("/")
        self.client_id = client_id
        self.secret = secret
        self.username = username
        self.password = password
        self.channel = channel
        self.session = session or requests.Session()
        self.timeout = timeout
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: float = 0.0

    def authenticate(self) -> str:
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token

        credentials = base64.b64encode(
            f"{self.client_id}:{self.secret}".encode()
        ).decode()

        if self.refresh_token:
            payload = {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
        else:
            payload = {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
            }

        url = f"{self.host}/api/oauth/v1/token"
        response = self.session.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        self.expires_at = time.time() + int(data.get("expires_in", 3600))
        logger.debug("Akeneo authentication successful")
        return self.access_token

    def _headers(self) -> dict[str, str]:
        token = self.authenticate()
        return {"Authorization": f"Bearer {token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        retry_on_401: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        url = f"{self.host}{path}"
        timeout = kwargs.pop("timeout", self.timeout)
        headers = {**self._headers(), **kwargs.pop("headers", {})}

        response = self.session.request(
            method=method,
            url=url,
            headers=headers,
            timeout=timeout,
            **kwargs,
        )

        if response.status_code == 401 and retry_on_401:
            logger.info("Akeneo token expired, re-authenticating")
            self.access_token = None
            self.refresh_token = None
            headers = {**self._headers(), **kwargs.pop("headers", {})}
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
        return response

    @staticmethod
    def product_name_to_identifier(name: str) -> str:
        """Convert 'Drave | Table Lamp' -> 'DRAVE_TABLE_LAMP'."""
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name)
        cleaned = cleaned.strip("_")
        return cleaned.upper()

    def search_product_models_by_name(
        self, name: str
    ) -> list[dict[str, Any]]:
        """Search product models by exact name match."""
        params = {
            "search": json.dumps({"name": [{"operator": "=", "value": name}]}),
            "limit": 5,
        }
        response = self._request("GET", "/api/rest/v1/product-models", params=params)
        if response.status_code != 200:
            logger.debug(
                "Product model search by name failed: %s %s",
                response.status_code, response.text[:200],
            )
            return []
        return response.json().get("_embedded", {}).get("items", [])

    def search_products_by_name(
        self, name: str
    ) -> list[dict[str, Any]]:
        """Search products by exact name match."""
        params = {
            "search": json.dumps({"name": [{"operator": "=", "value": name}]}),
            "limit": 5,
        }
        response = self._request("GET", "/api/rest/v1/products", params=params)
        if response.status_code != 200:
            logger.debug(
                "Product search by name failed: %s %s",
                response.status_code, response.text[:200],
            )
            return []
        return response.json().get("_embedded", {}).get("items", [])

    def find_by_name(
        self, product_name: str
    ) -> tuple[dict[str, Any] | None, str, str]:
        """Find a product or product-model by name attribute.

        Returns (data, identifier_or_code, type) where type is
        'product_model', 'product', or 'not_found'.
        """
        # Try product models first (photos are usually on models)
        models = self.search_product_models_by_name(product_name)
        if models:
            model = models[0]
            return model, model.get("code", ""), "product_model"

        # Try products
        products = self.search_products_by_name(product_name)
        if products:
            product = products[0]
            return product, product.get("identifier", ""), "product"

        # Try stripping variant suffixes like "/Large", "/Black", " blk", etc.
        stripped = re.sub(r"\s*/\s*\w+$", "", product_name).strip()
        if stripped != product_name:
            models = self.search_product_models_by_name(stripped)
            if models:
                return models[0], models[0].get("code", ""), "product_model"
            products = self.search_products_by_name(stripped)
            if products:
                return products[0], products[0].get("identifier", ""), "product"

        # Try stripping trailing color/size words after last space
        stripped2 = re.sub(r"\s+(?:blk|gld|wht|black|gold|white|large|small|medium)\s*$", "", product_name, flags=re.IGNORECASE).strip()
        if stripped2 != product_name and stripped2 != stripped:
            models = self.search_product_models_by_name(stripped2)
            if models:
                return models[0], models[0].get("code", ""), "product_model"

        # Try CONTAINS search as fallback using the name before "|"
        before_pipe = product_name.split("|")[0].strip()
        if before_pipe:
            params = {
                "search": json.dumps(
                    {"name": [{"operator": "CONTAINS", "value": before_pipe}]}
                ),
                "limit": 10,
            }
            response = self._request("GET", "/api/rest/v1/product-models", params=params)
            if response.status_code == 200:
                items = response.json().get("_embedded", {}).get("items", [])
                for item in items:
                    name_vals = item.get("values", {}).get("name", [])
                    item_name = name_vals[0].get("data", "") if name_vals else ""
                    if item_name.lower() == product_name.lower():
                        return item, item.get("code", ""), "product_model"
                # If no exact match, try matching the base name (before /)
                for item in items:
                    name_vals = item.get("values", {}).get("name", [])
                    item_name = name_vals[0].get("data", "") if name_vals else ""
                    # Match base name: "Kara | Modern Chandelier" matches "Kara | Modern Chandelier/Large"
                    if item_name and product_name.lower().startswith(item_name.lower()):
                        return item, item.get("code", ""), "product_model"

        return None, "", "not_found"

    def list_all_product_models(self) -> list[dict[str, Any]]:
        """Paginate through all product-models in Akeneo."""
        all_models: list[dict[str, Any]] = []
        path: str | None = "/api/rest/v1/product-models"
        params: dict[str, Any] = {"limit": 100}

        while path:
            response = self._request("GET", path, params=params)
            response.raise_for_status()
            data = response.json()

            items = data.get("_embedded", {}).get("items", [])
            all_models.extend(items)
            logger.debug("Fetched %d product models so far", len(all_models))

            next_link = data.get("_links", {}).get("next", {}).get("href")
            if next_link:
                path = next_link.replace(self.host, "")
                params = {}
            else:
                path = None

        return all_models

    def list_attributes(
        self, type_filter: str = "pim_catalog_image"
    ) -> list[dict[str, Any]]:
        """List Akeneo attributes, optionally filtered by type."""
        all_attributes: list[dict[str, Any]] = []
        path: str | None = "/api/rest/v1/attributes"
        params: dict[str, Any] = {"limit": 100}

        while path:
            response = self._request("GET", path, params=params)
            response.raise_for_status()
            data = response.json()

            items = data.get("_embedded", {}).get("items", [])
            all_attributes.extend(items)

            next_link = data.get("_links", {}).get("next", {}).get("href")
            if next_link:
                path = next_link.replace(self.host, "")
                params = {}
            else:
                path = None

        if type_filter:
            return [a for a in all_attributes if a.get("type") == type_filter]
        return all_attributes

    def get_product(self, identifier: str) -> dict[str, Any] | None:
        """Get a product by identifier. Returns None if not found."""
        response = self._request("GET", f"/api/rest/v1/products/{identifier}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get_product_model(self, code: str) -> dict[str, Any] | None:
        """Get a product model by code. Returns None if not found."""
        response = self._request("GET", f"/api/rest/v1/product-models/{code}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def find_product_or_model(
        self, identifier: str
    ) -> tuple[dict[str, Any] | None, str]:
        """Find as product first, then as product-model.

        Returns (product_data, type) where type is 'product', 'product_model', or 'not_found'.
        """
        product = self.get_product(identifier)
        if product:
            return product, "product"

        model = self.get_product_model(identifier)
        if model:
            return model, "product_model"

        return None, "not_found"

    def product_has_actual_photos(
        self,
        product_data: dict[str, Any],
        attribute_codes: list[str],
        scope: str | None = None,
    ) -> bool:
        """Check if any of the actual photo attributes already have data."""
        values = product_data.get("values", {})
        for attr_code in attribute_codes:
            attr_values = values.get(attr_code, [])
            for entry in attr_values:
                if scope and entry.get("scope") != scope:
                    continue
                if entry.get("data"):
                    return True
        return False

    def _upload_media_raw(self, file_path: Path) -> str | None:
        """Upload a media file without product association. Returns the media file path or None."""
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        url = f"{self.host}/api/rest/v1/media-files"
        token = self.authenticate()

        # Upload with empty product payload to just store the file
        product_payload: dict[str, Any] = {
            "identifier": "",
            "attribute": "",
            "scope": None,
            "locale": None,
        }

        with file_path.open("rb") as f:
            response = self.session.post(
                url,
                files={"file": (file_path.name, f, content_type)},
                data={"product": json.dumps(product_payload)},
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )

        if response.status_code == 201:
            # The Location header contains the media file code/path
            location = response.headers.get("Location", "")
            # Extract the media file path (e.g. /api/rest/v1/media-files/path_to_file)
            if location:
                media_code = location.rsplit("/", 1)[-1]
                logger.info("Uploaded media file: %s -> %s", file_path.name, media_code)
                return media_code
            return None
        else:
            logger.error(
                "Failed to upload media file %s: %s %s",
                file_path.name, response.status_code, response.text[:500],
            )
            return None

    def upload_media_file(
        self,
        file_path: Path,
        product_identifier: str,
        attribute_code: str,
        *,
        product_type: str = "product",
        scope: str | None = None,
        locale: str | None = None,
    ) -> requests.Response:
        """Upload a media file and associate it with a product/product-model attribute."""
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

        if product_type == "product":
            # Direct upload with product association
            product_payload: dict[str, Any] = {
                "identifier": product_identifier,
                "attribute": attribute_code,
                "scope": scope,
                "locale": locale,
            }

            url = f"{self.host}/api/rest/v1/media-files"
            token = self.authenticate()

            with file_path.open("rb") as f:
                response = self.session.post(
                    url,
                    files={"file": (file_path.name, f, content_type)},
                    data={"product": json.dumps(product_payload)},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout,
                )

            if response.status_code == 201:
                logger.info(
                    "Uploaded %s to %s.%s", file_path.name, product_identifier, attribute_code
                )
            else:
                logger.error(
                    "Failed to upload %s to %s.%s: %s %s",
                    file_path.name,
                    product_identifier,
                    attribute_code,
                    response.status_code,
                    response.text[:500],
                )
            return response

        # For product models: use 'product_model' key with 'code' field
        product_model_payload: dict[str, Any] = {
            "code": product_identifier,
            "attribute": attribute_code,
            "scope": scope,
            "locale": locale,
        }

        url = f"{self.host}/api/rest/v1/media-files"
        token = self.authenticate()

        with file_path.open("rb") as f:
            response = self.session.post(
                url,
                files={"file": (file_path.name, f, content_type)},
                data={"product_model": json.dumps(product_model_payload)},
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )

        if response.status_code == 201:
            logger.info(
                "Uploaded %s to product-model %s.%s",
                file_path.name, product_identifier, attribute_code,
            )
        else:
            logger.error(
                "Failed to upload %s to product-model %s.%s: %s %s",
                file_path.name,
                product_identifier,
                attribute_code,
                response.status_code,
                response.text[:500],
            )
        return response
