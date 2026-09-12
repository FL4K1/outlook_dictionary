"""Unit tests for Microsoft Graph Mail Adapter (PR-2.2).

Validates 20 specific requirements (A through T) using mocked HTTP responses:
A. Folder discovery
B. Initial delta request
C. Correct Prefer header
D. Correct $select
E. nextLink returned unchanged
F. deltaLink returned unchanged
G. continuation request keeps Prefer: IdType="ImmutableId" header
H. continuation URL is not modified
I. normal ProviderMessage mapping
J. absent field -> UNSET
K. explicit null -> None
L. sender mapping
M. TO/CC/BCC mapping
N. @removed deleted -> folder_removed
O. 401 mapping -> AuthExpiredError
P. 403 mapping -> ProviderPermissionError
Q. 404 mapping -> ProviderNotFoundError
R. 410 mapping -> DeltaCursorExpiredError
S. 429 mapping + Retry-After handling -> ProviderRateLimitedError
T. provider errors never expose tokens
"""

from __future__ import annotations

from collections.abc import Callable  # noqa: TC003
from datetime import UTC, datetime

import httpx
import pytest

from mip_providers import (
    UNSET,
    AuthExpiredError,
    DeltaCursorExpiredError,
    MicrosoftGraphMailAdapter,
    ProviderEmailAddress,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitedError,
)


def _create_mock_adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    access_token: str = "secret_access_token_abc123",  # noqa: S107
) -> tuple[MicrosoftGraphMailAdapter, list[httpx.Request]]:

    requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = MicrosoftGraphMailAdapter(access_token=access_token, client=client)
    return adapter, requests


@pytest.mark.asyncio
async def test_a_folder_discovery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1.0/me/mailFolders"
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "f1", "displayName": "Inbox", "parentFolderId": "0"},
                    {"id": "f2", "displayName": "SubFolder", "parentFolderId": "f1"},
                ]
            },
        )

    adapter, requests = _create_mock_adapter(handler)
    folders = await adapter.get_folders()

    assert len(requests) == 1
    assert len(folders) == 2
    assert folders[0].provider_folder_id == "f1"
    assert folders[0].name == "Inbox"
    assert folders[0].parent_id is None
    assert folders[1].provider_folder_id == "f2"
    assert folders[1].name == "SubFolder"
    assert folders[1].parent_id == "f1"


@pytest.mark.asyncio
async def test_a_folder_discovery_multi_page() -> None:
    page2_url = "https://graph.microsoft.com/v1.0/me/mailFolders?$top=250&$skiptoken=folder_skip_99"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Prefer") == 'IdType="ImmutableId"'
        if "$skiptoken=folder_skip_99" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "f3", "displayName": "Archive", "parentFolderId": "0"},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "f1", "displayName": "Inbox", "parentFolderId": "0"},
                    {"id": "f2", "displayName": "SubFolder", "parentFolderId": "f1"},
                ],
                "@odata.nextLink": page2_url,
            },
        )

    adapter, requests = _create_mock_adapter(handler)
    folders = await adapter.get_folders()

    assert len(requests) == 2
    assert str(requests[1].url) == page2_url
    assert len(folders) == 3
    assert [f.provider_folder_id for f in folders] == ["f1", "f2", "f3"]


def test_http_timeout_configuration() -> None:
    adapter = MicrosoftGraphMailAdapter(access_token="tok_123")
    assert isinstance(adapter.timeout, httpx.Timeout)
    assert adapter.timeout.connect == 5.0
    assert adapter.timeout.read == 30.0
    assert adapter.timeout.write == 10.0
    assert adapter.timeout.pool == 5.0

    custom_timeout = httpx.Timeout(15.0)
    custom_adapter = MicrosoftGraphMailAdapter(access_token="tok_123", timeout=custom_timeout)
    assert custom_adapter.timeout.read == 15.0


@pytest.mark.asyncio
async def test_b_initial_delta_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1.0/me/mailFolders/inbox/messages/delta"
        return httpx.Response(200, json={"value": []})

    adapter, requests = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    assert len(requests) == 1
    assert page.messages == []
    assert page.removals == []


@pytest.mark.asyncio
async def test_c_correct_prefer_header_initial() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Prefer") == 'IdType="ImmutableId", odata.maxpagesize=500'
        return httpx.Response(200, json={"value": []})

    adapter, requests = _create_mock_adapter(handler)
    await adapter.get_message_delta("inbox")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_d_correct_select_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        select_param = request.url.params.get("$select")
        assert select_param == (
            "id,subject,body,bodyPreview,sender,toRecipients,ccRecipients,bccRecipients,"
            "receivedDateTime,hasAttachments,isRead"
        )
        return httpx.Response(200, json={"value": []})

    adapter, requests = _create_mock_adapter(handler)
    await adapter.get_message_delta("inbox")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_e_next_link_returned_unchanged() -> None:
    next_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$skiptoken=opaque_token_123"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [], "@odata.nextLink": next_url})

    adapter, _ = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    assert page.next_continuation == next_url
    assert page.has_more is True
    assert page.is_delta_checkpoint is False


@pytest.mark.asyncio
async def test_f_delta_link_returned_unchanged() -> None:
    delta_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=opaque_delta_456"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": delta_url})

    adapter, _ = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    assert page.next_continuation == delta_url
    assert page.has_more is False
    assert page.is_delta_checkpoint is True


@pytest.mark.asyncio
async def test_g_continuation_request_prefer_header() -> None:
    cont_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$skiptoken=abc"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Prefer") == 'IdType="ImmutableId"'
        return httpx.Response(200, json={"value": []})

    adapter, requests = _create_mock_adapter(handler)
    await adapter.get_message_delta("inbox", opaque_continuation=cont_url)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_h_continuation_url_is_not_modified() -> None:
    cont_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$skiptoken=abc%3D%3D&custom=1"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == cont_url
        return httpx.Response(200, json={"value": []})

    adapter, requests = _create_mock_adapter(handler)
    await adapter.get_message_delta("inbox", opaque_continuation=cont_url)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_i_normal_provider_message_mapping() -> None:
    msg_json = {
        "id": "immutable_msg_001",
        "subject": "Project Status Update",
        "body": {"contentType": "html", "content": "<p>All good</p>"},
        "bodyPreview": "All good",
        "sender": {"emailAddress": {"name": "Alice Developer", "address": "alice@company.com"}},
        "toRecipients": [{"emailAddress": {"name": "Bob Manager", "address": "bob@company.com"}}],
        "ccRecipients": [],
        "bccRecipients": [],
        "receivedDateTime": "2026-09-11T12:00:00Z",
        "hasAttachments": False,
        "isRead": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [msg_json]})

    adapter, _ = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    assert len(page.messages) == 1
    msg = page.messages[0]
    assert msg.provider_message_id == "immutable_msg_001"
    assert msg.subject == "Project Status Update"
    assert msg.body == {"contentType": "html", "content": "<p>All good</p>"}
    assert msg.body_preview == "All good"
    assert msg.sender == ProviderEmailAddress(email="alice@company.com", name="Alice Developer")
    assert msg.recipients_to == [ProviderEmailAddress(email="bob@company.com", name="Bob Manager")]
    assert msg.recipients_cc == []
    assert msg.recipients_bcc == []
    assert msg.received_date_time == datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    assert msg.has_attachments is False
    assert msg.is_read is True


@pytest.mark.asyncio
async def test_j_absent_field_maps_to_unset() -> None:
    sparse_json = {"id": "msg_sparse_002"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [sparse_json]})

    adapter, _ = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    msg = page.messages[0]
    assert msg.provider_message_id == "msg_sparse_002"
    assert msg.subject is UNSET
    assert msg.body is UNSET
    assert msg.body_preview is UNSET
    assert msg.sender is UNSET
    assert msg.recipients_to is UNSET
    assert msg.recipients_cc is UNSET
    assert msg.recipients_bcc is UNSET
    assert msg.received_date_time is UNSET
    assert msg.has_attachments is UNSET
    assert msg.is_read is UNSET


@pytest.mark.asyncio
async def test_k_explicit_null_maps_to_none() -> None:
    null_json = {
        "id": "msg_null_003",
        "subject": None,
        "body": None,
        "sender": None,
        "toRecipients": None,
        "receivedDateTime": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [null_json]})

    adapter, _ = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    msg = page.messages[0]
    assert msg.provider_message_id == "msg_null_003"
    assert msg.subject is None
    assert msg.body is None
    assert msg.sender is None
    assert msg.recipients_to is None
    assert msg.received_date_time is None


@pytest.mark.asyncio
async def test_l_sender_mapping() -> None:
    sender_json = {
        "id": "msg_sender_004",
        "sender": {"emailAddress": {"name": "Carol Analyst", "address": "carol@firm.org"}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [sender_json]})

    adapter, _ = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    msg = page.messages[0]
    assert msg.sender == ProviderEmailAddress(email="carol@firm.org", name="Carol Analyst")


@pytest.mark.asyncio
async def test_m_recipients_mapping() -> None:
    recip_json = {
        "id": "msg_recip_005",
        "toRecipients": [
            {"emailAddress": {"name": "To One", "address": "to1@a.com"}},
            {"emailAddress": {"name": "To Two", "address": "to2@a.com"}},
        ],
        "ccRecipients": [{"emailAddress": {"name": "CC One", "address": "cc1@a.com"}}],
        "bccRecipients": [{"emailAddress": {"name": "BCC One", "address": "bcc1@a.com"}}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [recip_json]})

    adapter, _ = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    msg = page.messages[0]
    assert msg.recipients_to == [
        ProviderEmailAddress(email="to1@a.com", name="To One"),
        ProviderEmailAddress(email="to2@a.com", name="To Two"),
    ]
    assert msg.recipients_cc == [ProviderEmailAddress(email="cc1@a.com", name="CC One")]
    assert msg.recipients_bcc == [ProviderEmailAddress(email="bcc1@a.com", name="BCC One")]


@pytest.mark.asyncio
async def test_n_removed_deleted_maps_to_folder_removed() -> None:
    removed_json = {"id": "msg_removed_006", "@removed": {"reason": "deleted"}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [removed_json]})

    adapter, _ = _create_mock_adapter(handler)
    page = await adapter.get_message_delta("inbox")

    assert len(page.messages) == 0
    assert len(page.removals) == 1
    rem = page.removals[0]
    assert rem.provider_message_id == "msg_removed_006"
    assert rem.reason == "folder_removed"


@pytest.mark.asyncio
async def test_o_401_maps_to_auth_expired_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "InvalidAuthenticationToken",
                    "message": "Access token has expired.",
                }
            },
            headers={"client-request-id": "req-401-id"},
        )

    adapter, _ = _create_mock_adapter(handler)

    with pytest.raises(AuthExpiredError) as exc_info:
        await adapter.get_message_delta("inbox")

    assert exc_info.value.status_code == 401
    assert exc_info.value.request_id == "req-401-id"
    assert "InvalidAuthenticationToken" in str(exc_info.value)


@pytest.mark.asyncio
async def test_p_403_maps_to_provider_permission_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": "ErrorAccessDenied", "message": "Access denied"}},
            headers={"client-request-id": "req-403-id"},
        )

    adapter, _ = _create_mock_adapter(handler)

    with pytest.raises(ProviderPermissionError) as exc_info:
        await adapter.get_message_delta("inbox")

    assert exc_info.value.status_code == 403
    assert exc_info.value.request_id == "req-403-id"


@pytest.mark.asyncio
async def test_q_404_maps_to_provider_not_found_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "ErrorFolderNotFound", "message": "Folder not found"}},
            headers={"client-request-id": "req-404-id"},
        )

    adapter, _ = _create_mock_adapter(handler)

    with pytest.raises(ProviderNotFoundError) as exc_info:
        await adapter.get_message_delta("non_existent_folder")

    assert exc_info.value.status_code == 404
    assert exc_info.value.request_id == "req-404-id"


@pytest.mark.asyncio
async def test_r_410_maps_to_delta_cursor_expired_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            410,
            json={"error": {"code": "resyncRequired", "message": "Delta token is stale"}},
            headers={"client-request-id": "req-410-id"},
        )

    adapter, _ = _create_mock_adapter(handler)

    with pytest.raises(DeltaCursorExpiredError) as exc_info:
        await adapter.get_message_delta(
            "inbox", opaque_continuation="https://graph.microsoft.com/v1.0/stale_token"
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.request_id == "req-410-id"


@pytest.mark.asyncio
async def test_s_429_maps_to_provider_rate_limited_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"code": "ActivityLimitReached", "message": "Rate limit exceeded"}},
            headers={"Retry-After": "30", "client-request-id": "req-429-id"},
        )

    adapter, _ = _create_mock_adapter(handler)

    with pytest.raises(ProviderRateLimitedError) as exc_info:
        await adapter.get_message_delta("inbox")

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after == 30
    assert exc_info.value.request_id == "req-429-id"


@pytest.mark.asyncio
async def test_t_provider_errors_never_expose_tokens() -> None:
    secret_token = "SUPER_SECRET_OAUTH_BEARER_TOKEN_99999"  # noqa: S105

    def handler(request: httpx.Request) -> httpx.Response:
        # Simulate an error response body that accidentally reflects the auth header
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "InvalidRequest",
                    "message": f"Error processing header Bearer {secret_token}",
                }
            },
        )

    adapter, _ = _create_mock_adapter(handler, access_token=secret_token)

    try:
        await adapter.get_message_delta("inbox")
    except Exception as exc:
        exc_str = str(exc)
        exc_repr = repr(exc)
        assert secret_token not in exc_str
        assert secret_token not in exc_repr
