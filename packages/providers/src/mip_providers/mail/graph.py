"""Microsoft Graph mail provider adapter.

Implements folder discovery and /messages/delta continuation tracking
for Microsoft Graph API using Immutable IDs and UNSET presence semantics.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any

import httpx

from mip_providers.base import (
    UNSET,
    ProviderDeltaPage,
    ProviderEmailAddress,
    ProviderFolder,
    ProviderMessage,
    ProviderRemoval,
    UnsetType,
)
from mip_providers.errors import (
    AuthExpiredError,
    DeltaCursorExpiredError,
    ProviderError,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitedError,
)

GRAPH_DELTA_SELECT_FIELDS = (
    "id,subject,body,bodyPreview,sender,toRecipients,ccRecipients,bccRecipients,"
    "receivedDateTime,hasAttachments,isRead"
)


DEFAULT_GRAPH_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class MicrosoftGraphMailAdapter:
    """Microsoft Graph API adapter for mail folder discovery and delta sync."""

    DEFAULT_TIMEOUT = DEFAULT_GRAPH_TIMEOUT

    def __init__(
        self,
        access_token: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://graph.microsoft.com/v1.0",
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._access_token = access_token
        self._external_client = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or self.DEFAULT_TIMEOUT

    @property
    def timeout(self) -> httpx.Timeout:
        """Return the bounded HTTP timeout configuration."""
        return self._timeout

    async def get_folders(self) -> list[ProviderFolder]:
        """Discover mail folders via GET /me/mailFolders, following @odata.nextLink pagination."""
        url: str | None = f"{self._base_url}/me/mailFolders?$top=250"
        folders: list[ProviderFolder] = []

        while url:
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
                "Prefer": 'IdType="ImmutableId"',
            }

            if self._external_client:
                response = await self._external_client.get(
                    url, headers=headers, timeout=self._timeout
                )
                self._raise_for_status(response)
                data = response.json()
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(url, headers=headers)
                    self._raise_for_status(response)
                    data = response.json()

            items = data.get("value", [])
            for item in items:
                parent_id = item.get("parentFolderId")
                if parent_id in ("", "0", "null", None):
                    parent_id = None
                folders.append(
                    ProviderFolder(
                        provider_folder_id=item["id"],
                        name=item.get("displayName", ""),
                        parent_id=parent_id,
                        is_active=True,
                    )
                )

            url = data.get("@odata.nextLink")

        return folders

    async def get_message_delta(
        self,
        folder_id: str,
        opaque_continuation: str | None = None,
    ) -> ProviderDeltaPage:
        """Fetch a page of message changes via GET /me/mailFolders/{id}/messages/delta.

        Initial request injects $select and Prefer: IdType="ImmutableId".
        Continuation requests preserve opaque continuation URL and send Prefer header.
        """
        if opaque_continuation:
            url = opaque_continuation
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
                "Prefer": 'IdType="ImmutableId"',
            }
        else:
            path = f"/me/mailFolders/{folder_id}/messages/delta?$select={GRAPH_DELTA_SELECT_FIELDS}"
            url = f"{self._base_url}{path}"

            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
                "Prefer": 'IdType="ImmutableId", odata.maxpagesize=500',
            }

        if self._external_client:
            response = await self._external_client.get(url, headers=headers, timeout=self._timeout)
            self._raise_for_status(response)
            data = response.json()
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=headers)
                self._raise_for_status(response)
                data = response.json()

        messages: list[ProviderMessage] = []
        removals: list[ProviderRemoval] = []

        items = data.get("value", [])
        for item in items:
            if "@removed" in item:
                removals.append(
                    ProviderRemoval(
                        provider_message_id=item["id"],
                        reason="folder_removed",
                    )
                )
            else:
                messages.append(self._map_provider_message(item))

        next_link = data.get("@odata.nextLink")
        delta_link = data.get("@odata.deltaLink")

        if next_link:
            next_continuation = next_link
            has_more = True
            is_delta_checkpoint = False
        elif delta_link:
            next_continuation = delta_link
            has_more = False
            is_delta_checkpoint = True
        else:
            next_continuation = None
            has_more = False
            is_delta_checkpoint = True

        return ProviderDeltaPage(
            messages=messages,
            removals=removals,
            next_continuation=next_continuation,
            has_more=has_more,
            is_delta_checkpoint=is_delta_checkpoint,
        )

    def _map_provider_message(self, item: dict[str, Any]) -> ProviderMessage:
        provider_msg_id = item["id"]
        return ProviderMessage(
            provider_message_id=provider_msg_id,
            subject=item.get("subject", UNSET),
            body=item.get("body", UNSET),
            body_preview=item.get("bodyPreview", UNSET),
            sender=self._parse_recipient(item, "sender"),
            recipients_to=self._parse_recipients_list(item, "toRecipients"),
            recipients_cc=self._parse_recipients_list(item, "ccRecipients"),
            recipients_bcc=self._parse_recipients_list(item, "bccRecipients"),
            received_date_time=self._parse_datetime(item, "receivedDateTime"),
            has_attachments=item.get("hasAttachments", UNSET),
            is_read=item.get("isRead", UNSET),
        )

    @staticmethod
    def _parse_recipient(item: dict[str, Any], key: str) -> ProviderEmailAddress | UnsetType | None:
        if key not in item:
            return UNSET
        val = item[key]
        if val is None:
            return None
        addr_dict = val.get("emailAddress", {}) if isinstance(val, dict) else {}
        return ProviderEmailAddress(
            email=addr_dict.get("address", ""),
            name=addr_dict.get("name", "") or "",
        )

    @staticmethod
    def _parse_recipients_list(
        item: dict[str, Any], key: str
    ) -> list[ProviderEmailAddress] | UnsetType | None:
        if key not in item:
            return UNSET
        val = item[key]
        if val is None:
            return None
        if not isinstance(val, list):
            return None
        res: list[ProviderEmailAddress] = []
        for r in val:
            if isinstance(r, dict):
                addr_dict = (
                    r.get("emailAddress", {}) if isinstance(r.get("emailAddress"), dict) else {}
                )
                res.append(
                    ProviderEmailAddress(
                        email=addr_dict.get("address", ""),
                        name=addr_dict.get("name", "") or "",
                    )
                )
        return res

    @staticmethod
    def _parse_datetime(item: dict[str, Any], key: str) -> datetime | UnsetType | None:

        if key not in item:
            return UNSET
        val = item[key]
        if val is None:
            return None
        if isinstance(val, str):
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        return None

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return

        status_code = response.status_code
        request_id = response.headers.get("client-request-id") or response.headers.get("request-id")

        retry_after: int | None = None
        if status_code == 429:
            raw_retry = response.headers.get("retry-after")
            if raw_retry and raw_retry.isdigit():
                retry_after = int(raw_retry)

        err_msg = f"HTTP {status_code} error from Graph API"
        with contextlib.suppress(Exception):
            err_body = response.json()
            if isinstance(err_body, dict) and "error" in err_body:
                graph_err = err_body["error"]
                code = graph_err.get("code", "")
                msg = graph_err.get("message", "")
                if code or msg:
                    err_msg = f"Graph API error: {code} - {msg}"

        if self._access_token and self._access_token in err_msg:
            err_msg = err_msg.replace(self._access_token, "[REDACTED]")

        if status_code == 401:
            raise AuthExpiredError(err_msg, status_code=status_code, request_id=request_id)
        if status_code == 403:
            raise ProviderPermissionError(err_msg, status_code=status_code, request_id=request_id)
        if status_code == 404:
            raise ProviderNotFoundError(err_msg, status_code=status_code, request_id=request_id)
        if status_code == 410:
            raise DeltaCursorExpiredError(err_msg, status_code=status_code, request_id=request_id)
        if status_code == 429:
            raise ProviderRateLimitedError(
                err_msg,
                status_code=status_code,
                request_id=request_id,
                retry_after=retry_after,
            )
        raise ProviderError(err_msg, status_code=status_code, request_id=request_id)
